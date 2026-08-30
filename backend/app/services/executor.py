"""Razorpay experiment executor (Task 13).

This module is the deterministic execution boundary between an approved
experiment and real Razorpay Test Mode resources::

    APPROVED EXPERIMENT
           |
    EXECUTION VALIDATION
           |
    APPLICATION IDEMPOTENCY
           |
    RAZORPAY CLIENT
           |
    REAL TEST RESOURCE
           |
    RazorpayResource
           |
    OperationExecution

Design boundaries:

- It NEVER re-runs the policy engine. The persisted
  ``PolicyDecision`` with ``decision == "APPROVE"`` is the only authorization
  checked.
- It NEVER calls the simulation/causal model, OpenAI, or the statistics
  engine. Real Razorpay calls are used once per experiment to prove that the
  approved treatment can be deployed; synthetic customer traffic remains a
  separate concern.
- It NEVER sends offers, guesses Offer IDs, or creates Razorpay Offers.
  ``offer_discount`` fails closed for automated deployment until a semantic
  discount can be mapped to a verified pre-created Offer.
- It NEVER commits the database transaction. The caller controls commits.
- It NEVER automatically retries a POST. Network/timeout/5xx failures are
  recorded as ambiguous (``pending``) and a second deploy is refused.
"""

from __future__ import annotations

import copy
import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    Experiment,
    ExperimentResult,
    OperationExecution,
    PolicyDecision,
    RazorpayResource,
)
from app.services.audit import (
    ACTOR_RAZORPAY_EXECUTOR,
    ENTITY_EXPERIMENT,
    EXPERIMENT_ROLLED_BACK,
    RAZORPAY_RESOURCE_CANCELLED,
    RAZORPAY_RESOURCE_CREATED,
    record_audit_event_once,
)
from app.services.idempotency import (
    begin_operation,
    mark_operation_ambiguous,
    mark_operation_failed,
    mark_operation_succeeded,
)
from app.services.razorpay import (
    PAYMENT_LINK_METHOD_KEYS,
    RazorpayClient,
    RazorpayError,
)

__all__ = [
    "DEPLOY_OPERATION_TYPE",
    "DESCRIPTION",
    "ExperimentExecutionAuthorizationError",
    "ExperimentExecutionConfigurationError",
    "ExperimentExecutionError",
    "ExperimentExecutionStateError",
    "ROLLBACK_OPERATION_TYPE",
    "TEST_AMOUNT_PAISE",
    "compute_expire_by",
    "deploy_experiment_treatment",
    "rollback_experiment_treatment",
]

#: Fixed, safe Test Mode amount used for every treatment Payment Link.
TEST_AMOUNT_PAISE = 10000

#: Currency used for all real treatment resources.
CURRENCY = "INR"

#: Human-readable description for Treatment Payment Links.
DESCRIPTION = "Merchant Revenue Autopilot test treatment"

DEPLOY_OPERATION_TYPE = "deploy_treatment"
ROLLBACK_OPERATION_TYPE = "rollback_treatment"

#: Interventions that can be automatically deployed as Payment Links.
SUPPORTED_DEPLOYMENT_TYPES = frozenset(
    {"payment_method_config", "partial_payment", "expiry_config"}
)

#: Razorpay rejects Payment Links with expiry further than 180 days.
MAX_EXPIRY_HOURS = 24 * 180

#: Conservative reference ID budget (well inside Razorpay's documented limits).
MAX_REFERENCE_ID_LENGTH = 64

_HASHED_REFERENCE_ID_LENGTH = 12

_NOTIFY = {"sms": False, "email": False}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ExperimentExecutionError(Exception):
    """Base error for the Razorpay experiment executor."""


class ExperimentExecutionConfigurationError(ExperimentExecutionError):
    """Raised when an experiment cannot be mapped to a supported real resource."""


class ExperimentExecutionAuthorizationError(ExperimentExecutionError):
    """Raised when persisted policy/lifecycle state does not authorize deploy."""


class ExperimentExecutionStateError(ExperimentExecutionError):
    """Raised when persisted state is inconsistent / requires manual recovery."""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _is_number(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _as_utc(value: datetime) -> datetime:
    """Interpret naive datetimes as UTC and normalize aware ones to UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def compute_expire_by(*, expiry_hours: float, now: datetime | None = None) -> int:
    """Return a future Unix timestamp for a Payment Link expiration.

    ``now`` defaults to the current UTC time; tests inject a deterministic
    timestamp so results do not depend on wall-clock time.
    """
    if not _is_number(expiry_hours):
        raise ExperimentExecutionConfigurationError(
            "expiry_config expiry_hours must be a finite number"
        )
    if expiry_hours <= 0 or expiry_hours > MAX_EXPIRY_HOURS:
        raise ExperimentExecutionConfigurationError(
            f"expiry_config expiry_hours must satisfy 0 < value <= "
            f"{MAX_EXPIRY_HOURS}, got {expiry_hours}"
        )
    base = _as_utc(now) if now is not None else datetime.now(timezone.utc)
    return int((base + timedelta(hours=expiry_hours)).timestamp())


def _build_reference_id(experiment_id: str) -> str:
    """Build a deterministic, reasonably short Razorpay reference_id."""
    base = f"mra_{experiment_id}_treatment_v1"
    if len(base) <= MAX_REFERENCE_ID_LENGTH:
        return base
    digest = hashlib.sha256(experiment_id.encode("utf-8")).hexdigest()
    return f"mra_{digest[:_HASHED_REFERENCE_ID_LENGTH]}_treatment_v1"


def _validate_payment_method_config(treatment_config: dict[str, Any]) -> dict[str, bool]:
    methods = treatment_config.get("payment_methods")
    if not isinstance(methods, dict) or not methods:
        raise ExperimentExecutionConfigurationError(
            "payment_method_config treatment must contain a non-empty "
            "'payment_methods' dictionary"
        )
    for method, enabled in methods.items():
        if method not in PAYMENT_LINK_METHOD_KEYS:
            raise ExperimentExecutionConfigurationError(
                f"unsupported payment method {method!r}; allowed keys: "
                f"{sorted(PAYMENT_LINK_METHOD_KEYS)}"
            )
        if not isinstance(enabled, bool):
            raise ExperimentExecutionConfigurationError(
                f"payment method {method!r} must be boolean"
            )
    return dict(methods)


def _validate_partial_payment(treatment_config: dict[str, Any]) -> dict[str, Any]:
    accept_partial = treatment_config.get("accept_partial")
    if accept_partial is not True:
        raise ExperimentExecutionConfigurationError(
            "partial_payment treatment must have accept_partial=True; a "
            "partial-payment treatment with accept_partial=False is not a "
            "meaningful treatment"
        )
    pct = treatment_config.get("first_min_partial_amount_pct")
    if not _is_number(pct):
        raise ExperimentExecutionConfigurationError(
            "partial_payment treatment requires a numeric "
            "'first_min_partial_amount_pct'"
        )
    if pct <= 0 or pct > 1.0:
        raise ExperimentExecutionConfigurationError(
            f"partial_payment first_min_partial_amount_pct must satisfy "
            f"0 < value <= 1, got {pct}"
        )
    first_min = round(TEST_AMOUNT_PAISE * pct)
    if first_min <= 0:
        raise ExperimentExecutionConfigurationError(
            "partial_payment first_min_partial_amount must be > 0"
        )
    if first_min > TEST_AMOUNT_PAISE:
        raise ExperimentExecutionConfigurationError(
            "partial_payment first_min_partial_amount must not exceed the "
            "test amount"
        )
    return {"accept_partial": True, "first_min_partial_amount": first_min}


def _validate_expiry_config(treatment_config: dict[str, Any]) -> dict[str, Any]:
    expiry_hours = treatment_config.get("expiry_hours")
    if not _is_number(expiry_hours):
        raise ExperimentExecutionConfigurationError(
            "expiry_config treatment requires a numeric 'expiry_hours'"
        )
    if expiry_hours <= 0 or expiry_hours > MAX_EXPIRY_HOURS:
        raise ExperimentExecutionConfigurationError(
            f"expiry_config expiry_hours must satisfy 0 < value <= "
            f"{MAX_EXPIRY_HOURS}, got {expiry_hours}"
        )
    return {"expiry_hours": expiry_hours}


def _map_treatment_to_razorpay(
    *,
    intervention_type: str,
    treatment_config: dict[str, Any],
    now: datetime | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Translate canonical semantic treatment config into Razorpay args.

    Returns ``(stable_execution, razorpay_args)``. ``stable_execution`` is
    used for the idempotency hash and contains no wall-clock derived values.
    The returned ``razorpay_args`` does not include ``reference_id``; the
    deploy function adds the deterministic reference before calling the
    client.
    """
    if intervention_type == "offer_discount":
        raise ExperimentExecutionConfigurationError(
            "offer_discount requires a verified pre-created Offer mapping before "
            "automated deployment."
        )
    if intervention_type not in SUPPORTED_DEPLOYMENT_TYPES:
        raise ExperimentExecutionConfigurationError(
            f"unsupported deployment intervention type {intervention_type!r}"
        )

    base_args: dict[str, Any] = {
        "amount": TEST_AMOUNT_PAISE,
        "currency": CURRENCY,
        "description": DESCRIPTION,
        "notify": dict(_NOTIFY),
    }
    stable_execution: dict[str, Any] = {}

    if intervention_type == "payment_method_config":
        methods = _validate_payment_method_config(treatment_config)
        base_args["payment_methods"] = dict(methods)
        stable_execution = {"payment_methods": dict(methods)}
    elif intervention_type == "partial_payment":
        partial = _validate_partial_payment(treatment_config)
        base_args["accept_partial"] = partial["accept_partial"]
        base_args["first_min_partial_amount"] = partial["first_min_partial_amount"]
        stable_execution = dict(partial)
    elif intervention_type == "expiry_config":
        expiry = _validate_expiry_config(treatment_config)
        base_args["expire_by"] = compute_expire_by(
            expiry_hours=expiry["expiry_hours"], now=now
        )
        stable_execution = dict(expiry)

    return stable_execution, base_args


def _build_operation_request_payload(
    experiment: Experiment,
    reference_id: str,
    stable_execution: dict[str, Any],
) -> dict[str, Any]:
    """Build the stable canonical payload used for the idempotency hash.

    Time-derived values such as ``expire_by`` are deliberately not included.
    They change between wall-clock calls and would otherwise prevent a
    successful deploy from being reused. The semantic treatment config and
    all other deterministic inputs are included.
    """
    return {
        "operation": DEPLOY_OPERATION_TYPE,
        "version": "v1",
        "experiment_id": experiment.id,
        "intervention_type": experiment.intervention_type,
        "treatment_config": copy.deepcopy(experiment.treatment_config),
        "test_amount": TEST_AMOUNT_PAISE,
        "currency": CURRENCY,
        "reference_id": reference_id,
        "description": DESCRIPTION,
        "notify": dict(_NOTIFY),
        "execution": copy.deepcopy(stable_execution),
    }


def _build_rollback_request_payload(
    experiment: Experiment, resource: RazorpayResource
) -> dict[str, Any]:
    return {
        "operation": ROLLBACK_OPERATION_TYPE,
        "version": "v1",
        "experiment_id": experiment.id,
        "resource_type": resource.resource_type,
        "razorpay_id": resource.razorpay_id,
        "variant": resource.variant,
        "action": "cancel",
    }


def _safe_response_meta(response: dict[str, Any]) -> dict[str, Any]:
    """Return a tiny, secret-free metadata envelope from an external response."""
    metadata: dict[str, Any] = {}
    for key in ("id", "status", "reference_id"):
        value = response.get(key)
        if value is not None:
            metadata[key] = str(value)[:200]
    return metadata


def _build_resource_config(
    *,
    experiment: Experiment,
    reference_id: str,
    stable_execution: dict[str, Any],
) -> dict[str, Any]:
    """Canonical semantic/execution snapshot persisted on ``RazorpayResource``."""
    return {
        "operation": DEPLOY_OPERATION_TYPE,
        "version": "v1",
        "intervention_type": experiment.intervention_type,
        "treatment_config": copy.deepcopy(experiment.treatment_config),
        "test_amount": TEST_AMOUNT_PAISE,
        "currency": CURRENCY,
        "reference_id": reference_id,
        "description": DESCRIPTION,
        "notify": dict(_NOTIFY),
        "execution": copy.deepcopy(stable_execution),
    }


# ---------------------------------------------------------------------------
# Persistence helpers (flush only, never commit)
# ---------------------------------------------------------------------------


def _get_experiment(db: Session, experiment_id: str) -> Experiment | None:
    return db.get(Experiment, experiment_id)


def _validate_deploy_status(experiment: Experiment) -> None:
    if experiment.status not in ("approved", "running"):
        raise ExperimentExecutionAuthorizationError(
            f"experiment {experiment.id!r} has status {experiment.status!r}; "
            "only 'approved' or 'running' experiments may deploy"
        )


def _get_latest_policy_decision(
    db: Session, experiment_id: str
) -> PolicyDecision | None:
    return (
        db.query(PolicyDecision)
        .filter(PolicyDecision.experiment_id == experiment_id)
        .order_by(PolicyDecision.evaluated_at.desc(), PolicyDecision.id.desc())
        .first()
    )


def _verify_approval(db: Session, experiment: Experiment) -> None:
    decision = _get_latest_policy_decision(db, experiment.id)
    if decision is None:
        raise ExperimentExecutionAuthorizationError(
            f"experiment {experiment.id!r} has no persisted PolicyDecision; "
            "refusing to deploy"
        )
    if decision.merchant_id != experiment.merchant_id:
        raise ExperimentExecutionAuthorizationError(
            f"policy decision for experiment {experiment.id!r} belongs to a "
            "different merchant; refusing to deploy"
        )
    if decision.decision != "APPROVE":
        raise ExperimentExecutionAuthorizationError(
            f"experiment {experiment.id!r} has persisted policy decision "
            f"{decision.decision!r}; only APPROVE permits deployment"
        )


def _resolve_razorpay_client(
    razorpay_client: RazorpayClient | None,
) -> tuple[RazorpayClient, bool]:
    """Return ``(client, owns_client)``.

    The executor never reads credentials inside ``RazorpayClient``; it loads
    ``Settings`` here and constructs the client explicitly.
    """
    if razorpay_client is not None:
        return razorpay_client, False
    settings = get_settings()
    key_id = settings.RAZORPAY_KEY_ID
    key_secret = settings.RAZORPAY_KEY_SECRET
    if not key_id or not key_secret:
        raise ExperimentExecutionConfigurationError(
            "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are required to deploy "
            "real Razorpay Test Mode resources"
        )
    return RazorpayClient(key_id=key_id, key_secret=key_secret), True


def _mark_execution_error(db: Session, operation: OperationExecution, exc: Exception) -> None:
    """Classify an execution error and record it in OperationExecution."""
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        mark_operation_ambiguous(db, operation)
        return
    if isinstance(exc, RazorpayError):
        status_code = exc.status_code
        if status_code is None:
            # Client-side transport/unknown failure (including wrapped
            # httpx.RequestError) is ambiguous: the resource may exist.
            mark_operation_ambiguous(db, operation)
            return
        if 500 <= status_code <= 599:
            mark_operation_ambiguous(db, operation, status_code=status_code)
            return
        mark_operation_failed(db, operation, status_code=status_code)
        return
    # Any other exception is treated conservatively as ambiguous: we cannot
    # prove that no external resource was created.
    mark_operation_ambiguous(db, operation)


def _find_deployed_resource(
    db: Session,
    experiment: Experiment,
    operation: OperationExecution,
) -> RazorpayResource | None:
    if not operation.razorpay_resource_id:
        return None
    return (
        db.query(RazorpayResource)
        .filter(
            RazorpayResource.experiment_id == experiment.id,
            RazorpayResource.resource_type == "payment_link",
            RazorpayResource.razorpay_id == operation.razorpay_resource_id,
        )
        .one_or_none()
    )


def _find_treatment_resource(
    db: Session, experiment: Experiment
) -> RazorpayResource | None:
    return (
        db.query(RazorpayResource)
        .filter(
            RazorpayResource.experiment_id == experiment.id,
            RazorpayResource.resource_type == "payment_link",
            RazorpayResource.variant == "treatment",
        )
        .order_by(RazorpayResource.created_at.desc(), RazorpayResource.id.desc())
        .first()
    )


def _persist_treatment_resource(
    db: Session,
    *,
    experiment: Experiment,
    reference_id: str,
    stable_execution: dict[str, Any],
    response: dict[str, Any],
) -> RazorpayResource:
    razorpay_id = response.get("id")
    if not isinstance(razorpay_id, str) or not razorpay_id.strip():
        raise ExperimentExecutionStateError(
            "Razorpay create_payment_link returned no usable resource id"
        )
    resource = RazorpayResource(
        experiment_id=experiment.id,
        variant="treatment",
        resource_type="payment_link",
        razorpay_id=razorpay_id.strip(),
        config=_build_resource_config(
            experiment=experiment,
            reference_id=reference_id,
            stable_execution=stable_execution,
        ),
        status="active",
    )
    db.add(resource)
    db.flush()
    return resource


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def deploy_experiment_treatment(
    db: Session,
    experiment_id: str,
    *,
    razorpay_client: RazorpayClient | None = None,
    now: datetime | None = None,
) -> RazorpayResource:
    """Deploy the approved treatment to a single real Razorpay Test resource.

    Args:
        db: SQLAlchemy session. The caller owns the transaction (never committed here).
        experiment_id: ID of an approved/running experiment.
        razorpay_client: Optional injected client for offline tests.
        now: Optional deterministic UTC timestamp for expiry computations.

    Returns:
        The existing ``RazorpayResource`` on a repeated successful call.
    """
    experiment = _get_experiment(db, experiment_id)
    if experiment is None:
        raise ExperimentExecutionError(f"Experiment not found: {experiment_id!r}")

    _validate_deploy_status(experiment)
    _verify_approval(db, experiment)

    stable_execution, raw_args = _map_treatment_to_razorpay(
        intervention_type=experiment.intervention_type,
        treatment_config=dict(experiment.treatment_config),
        now=now,
    )
    reference_id = _build_reference_id(experiment.id)

    request_payload = _build_operation_request_payload(
        experiment, reference_id, stable_execution
    )
    operation_key = f"experiment:{experiment.id}:deploy:treatment:v1"
    operation = begin_operation(
        db,
        operation_key=operation_key,
        operation_type=DEPLOY_OPERATION_TYPE,
        request_payload=request_payload,
    )

    if operation.status == "succeeded":
        resource = _find_deployed_resource(db, experiment, operation)
        if resource is None:
            raise ExperimentExecutionStateError(
                f"operation {operation_key!r} succeeded but its "
                "RazorpayResource row is missing; manual recovery required"
            )
        return resource

    if operation.status != "pending":
        raise ExperimentExecutionStateError(
            f"operation {operation_key!r} is in unexpected state "
            f"{operation.status!r}"
        )

    razorpay_args = dict(raw_args)
    razorpay_args["reference_id"] = reference_id

    client, owns_client = _resolve_razorpay_client(razorpay_client)
    try:
        response = client.create_payment_link(**razorpay_args)
        resource = _persist_treatment_resource(
            db,
            experiment=experiment,
            reference_id=reference_id,
            stable_execution=stable_execution,
            response=response,
        )
        mark_operation_succeeded(
            db,
            operation,
            razorpay_resource_id=resource.razorpay_id,
            response_json=_safe_response_meta(response),
        )
        record_audit_event_once(
            db,
            merchant_id=experiment.merchant_id,
            event_type=RAZORPAY_RESOURCE_CREATED,
            entity_type=ENTITY_EXPERIMENT,
            entity_id=experiment.id,
            data={
                "resource_type": resource.resource_type,
                "razorpay_id": resource.razorpay_id,
                "variant": resource.variant,
            },
            actor=ACTOR_RAZORPAY_EXECUTOR,
        )
        db.flush()
        return resource
    except Exception as exc:
        _mark_execution_error(db, operation, exc)
        raise
    finally:
        if owns_client:
            client.close()
        db.flush()


def rollback_experiment_treatment(
    db: Session,
    experiment_id: str,
    *,
    razorpay_client: RazorpayClient | None = None,
) -> RazorpayResource | None:
    """Cancel a deployed treatment Payment Link after an explicit ROLLBACK.

    Authorization comes only from a persisted ``ExperimentResult`` with
    ``decision == "ROLLBACK"``. ``KEEP`` and ``INCONCLUSIVE`` do not permit
    cancellation, and no experiment status is rewritten here.

    Returns:
        The cancelled resource, or ``None`` when no treatment resource exists.
    """
    experiment = _get_experiment(db, experiment_id)
    if experiment is None:
        raise ExperimentExecutionError(f"Experiment not found: {experiment_id!r}")

    result = (
        db.query(ExperimentResult)
        .filter(ExperimentResult.experiment_id == experiment_id)
        .one_or_none()
    )
    if result is None:
        raise ExperimentExecutionAuthorizationError(
            f"experiment {experiment_id!r} has no ExperimentResult; only an "
            "explicit ROLLBACK decision permits cancellation"
        )
    if result.decision != "ROLLBACK":
        raise ExperimentExecutionAuthorizationError(
            f"experiment {experiment_id!r} has result decision "
            f"{result.decision!r}; only ROLLBACK permits cancellation"
        )

    resource = _find_treatment_resource(db, experiment)
    if resource is None:
        return None

    if resource.status == "cancelled":
        return resource

    operation_key = f"experiment:{experiment.id}:rollback:treatment:v1"
    request_payload = _build_rollback_request_payload(experiment, resource)
    operation = begin_operation(
        db,
        operation_key=operation_key,
        operation_type=ROLLBACK_OPERATION_TYPE,
        request_payload=request_payload,
    )

    if operation.status == "succeeded":
        if resource.status != "cancelled":
            raise ExperimentExecutionStateError(
                f"rollback {operation_key!r} succeeded but the resource is "
                "not cancelled; manual recovery required"
            )
        return resource

    if operation.status != "pending":
        raise ExperimentExecutionStateError(
            f"operation {operation_key!r} is in unexpected state "
            f"{operation.status!r}"
        )

    client, owns_client = _resolve_razorpay_client(razorpay_client)
    try:
        response = client.cancel_payment_link(resource.razorpay_id)
        resource.status = "cancelled"
        mark_operation_succeeded(
            db,
            operation,
            razorpay_resource_id=resource.razorpay_id,
            response_json=_safe_response_meta(response),
        )
        rollback_payload = {
            "resource_type": resource.resource_type,
            "razorpay_id": resource.razorpay_id,
        }
        record_audit_event_once(
            db,
            merchant_id=experiment.merchant_id,
            event_type=RAZORPAY_RESOURCE_CANCELLED,
            entity_type=ENTITY_EXPERIMENT,
            entity_id=experiment.id,
            data=rollback_payload,
            actor=ACTOR_RAZORPAY_EXECUTOR,
        )
        record_audit_event_once(
            db,
            merchant_id=experiment.merchant_id,
            event_type=EXPERIMENT_ROLLED_BACK,
            entity_type=ENTITY_EXPERIMENT,
            entity_id=experiment.id,
            data=rollback_payload,
            actor=ACTOR_RAZORPAY_EXECUTOR,
        )
        db.flush()
        return resource
    except Exception as exc:
        _mark_execution_error(db, operation, exc)
        raise
    finally:
        if owns_client:
            client.close()
        db.flush()
