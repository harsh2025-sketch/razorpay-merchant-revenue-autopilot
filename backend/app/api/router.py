"""FastAPI product API for the Merchant Revenue Autopilot (Task 15).

Routes are deliberately thin. Every handler either reads a persisted
aggregate through ``app.services.autopilot`` or performs exactly one lifecycle
action through the same orchestration layer. No detection, diagnosis,
planning, policy, execution, runtime or statistical logic lives here - the
existing deterministic engines decide, the API only orchestrates.

Transaction ownership
---------------------
The API boundary is where the database transaction is owned:

- GET routes never write and never commit,
- POST routes commit only after the *whole* requested operation succeeded,
- any escaping error rolls the session back, so no half-applied lifecycle
  state survives a failed request (the ledger exception described below is the
  only deviation, and it can only ever persist the operation record),
- response payloads are projected before the commit, so a client never sees
  rows that the transaction could still discard.

External boundary state
-----------------------
Task 13 keeps an ``OperationExecution`` ledger row describing every real
Razorpay write, and that row is what makes a repeated call safe. The ledger is
therefore the one thing this boundary keeps when a deploy or rollback fails at
Razorpay: an ambiguous timeout or a 5xx leaves the operation ``pending`` and a
definitive 4xx leaves it ``failed``, both durably, even though the request still
ends in a mapped failure and the lifecycle itself does not advance. Everything
else - including every ordinary domain, validation and lifecycle error - keeps
the all-or-nothing rule above, and no route ever retries an external write on
its own.

Error mapping
-------------
Domain errors become deterministic responses of the shape
``{"detail": {"code": ..., "message": ...}}``. Unexpected failures return a
fixed 500 message: exception class names, stack traces, prompts and every
credential are never echoed to the client.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, TypeVar

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api import schemas
from app.config import get_settings
from app.db.session import get_db
from app.engines.diagnosis import (
    DiagnosisConfigurationError,
    DiagnosisError,
    DiagnosisOutputInvalidError,
)
from app.engines.planner import ExperimentPlanningError
from app.engines.policy import PolicyEvaluationError
from app.engines.statistics import StatisticalEvaluationError
from app.services import autopilot
from app.services.audit import AuditError
from app.services.experiments import ExperimentRuntimeError
from app.services.executor import (
    ExperimentExecutionAuthorizationError,
    ExperimentExecutionConfigurationError,
    ExperimentExecutionError,
    ExperimentExecutionStateError,
)
from app.services.idempotency import IdempotencyConflictError, IdempotencyInProgressError
from app.services.razorpay import RazorpayError

logger = logging.getLogger("app.api")

# HTTP codes used by this surface. Plain integers on purpose: the Starlette
# alias for 422 was renamed upstream, and a small local table keeps the
# mapping readable and version-proof.
HTTP_403_FORBIDDEN = 403
HTTP_404_NOT_FOUND = 404
HTTP_409_CONFLICT = 409
HTTP_422_UNPROCESSABLE = 422
HTTP_500_INTERNAL = 500
HTTP_502_BAD_GATEWAY = 502
HTTP_503_UNAVAILABLE = 503

T = TypeVar("T")

router = APIRouter(prefix="/api/v1")

#: Number of lifecycle events returned by the audit endpoints unless asked
#: otherwise. Kept inside the Task 14 history limit bounds.
DEFAULT_AUDIT_LIMIT = 100
MAX_AUDIT_LIMIT = 1000

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    HTTP_404_NOT_FOUND: {
        "model": schemas.ApiErrorResponse,
        "description": "Unknown or cross-merchant identifier.",
    },
    HTTP_409_CONFLICT: {
        "model": schemas.ApiErrorResponse,
        "description": "Persisted lifecycle state does not permit this action.",
    },
}

#: ``*_API_KEY`` / ``*_SECRET`` style names in a configuration error mean an
#: integration is not wired up (503). Any other configuration error is a
#: merchant-side setting that refuses the action (422).
_ENV_SETTING_NAME = re.compile(r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b")

#: Credentials occasionally surface inside upstream error text; scrub them.
_SECRET_SHAPED_TEXT = re.compile(
    r"(sk-[A-Za-z0-9_.\-]{3,}|rzp_(?:test|live)_[A-Za-z0-9]+|bearer\s+\S+)",
    re.IGNORECASE,
)

#: Ordered (exception types, status, code) rules. Subclasses must precede
#: their base classes so the specific mapping wins.
_ERROR_RULES: tuple[tuple[tuple[type[BaseException], ...], int, str], ...] = (
    (
        (
            autopilot.MerchantNotFoundError,
            autopilot.OpportunityNotFoundError,
            autopilot.HypothesisNotFoundError,
            autopilot.ExperimentNotFoundError,
        ),
        HTTP_404_NOT_FOUND,
        "NOT_FOUND",
    ),
    (
        (autopilot.MerchantPolicyNotConfiguredError,),
        HTTP_422_UNPROCESSABLE,
        "MERCHANT_POLICY_NOT_CONFIGURED",
    ),
    ((autopilot.InvalidTransitionError,), HTTP_409_CONFLICT, "INVALID_TRANSITION"),
    ((DiagnosisOutputInvalidError,), HTTP_502_BAD_GATEWAY, "AI_OUTPUT_REJECTED"),
    ((DiagnosisError,), HTTP_409_CONFLICT, "DIAGNOSIS_FAILED"),
    ((ExperimentPlanningError,), HTTP_422_UNPROCESSABLE, "PLANNING_FAILED"),
    ((PolicyEvaluationError,), HTTP_409_CONFLICT, "POLICY_EVALUATION_FAILED"),
    (
        (ExperimentExecutionAuthorizationError,),
        HTTP_403_FORBIDDEN,
        "EXECUTION_NOT_AUTHORIZED",
    ),
    (
        (
            ExperimentExecutionStateError,
            IdempotencyInProgressError,
            IdempotencyConflictError,
        ),
        HTTP_409_CONFLICT,
        "EXECUTION_STATE_CONFLICT",
    ),
    ((StatisticalEvaluationError,), HTTP_409_CONFLICT, "EXPERIMENT_NOT_READY"),
    ((ExperimentRuntimeError,), HTTP_422_UNPROCESSABLE, "RUNTIME_REJECTED"),
    ((AuditError,), HTTP_422_UNPROCESSABLE, "AUDIT_REQUEST_INVALID"),
    ((RazorpayError,), HTTP_502_BAD_GATEWAY, "RAZORPAY_API_FAILURE"),
    ((ExperimentExecutionError,), HTTP_502_BAD_GATEWAY, "EXECUTION_FAILED"),
)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


def get_openai_client() -> Any | None:
    """Optional OpenAI client injection point.

    ``None`` is the production answer: the Task 08 diagnosis engine then loads
    the configured client itself (and fails with a clear error when no key is
    present). The API never builds prompts and never talks to OpenAI directly.
    Tests override this dependency with a fake.
    """
    return None


def get_razorpay_client() -> Any | None:
    """Optional Razorpay client injection point.

    ``None`` lets the Task 13 executor resolve Razorpay Test Mode credentials
    from settings and construct its own client; the API never calls the
    Razorpay HTTP boundary directly. Tests override this dependency with a
    fake so the whole lifecycle can run offline.
    """
    return None


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------


#: Settings whose values must never be echoed back, even if an upstream error
#: text happens to interpolate them.
_SECRET_SETTING_NAMES = ("RAZORPAY_KEY_SECRET", "RAZORPAY_KEY_ID", "OPENAI_API_KEY")


def _safe_message(exc: BaseException) -> str:
    """Single-line, credential-free description of a domain error."""
    text = " ".join(str(exc).split())
    text = _SECRET_SHAPED_TEXT.sub("[redacted]", text)
    settings = get_settings()
    for name in _SECRET_SETTING_NAMES:
        value = getattr(settings, name, None)
        if isinstance(value, str) and len(value) >= 6:
            text = text.replace(value, "[redacted]")
    return text[:300]


def _to_http_error(exc: BaseException) -> HTTPException:
    """Map one domain exception onto one deterministic HTTP response."""
    if isinstance(exc, HTTPException):  # pragma: no cover - defensive
        return exc

    configuration_errors = (DiagnosisConfigurationError, ExperimentExecutionConfigurationError)
    if isinstance(exc, configuration_errors):
        message = _safe_message(exc)
        # A missing integration credential is unavailable infrastructure; a
        # refusal to map a well-formed plan is a configuration the merchant
        # controls.
        if _ENV_SETTING_NAME.search(message):
            code = (
                "RAZORPAY_NOT_CONFIGURED"
                if isinstance(exc, ExperimentExecutionConfigurationError)
                else "OPENAI_NOT_CONFIGURED"
            )
            return HTTPException(
                HTTP_503_UNAVAILABLE,
                detail={"code": code, "message": message},
            )
        code = (
            "DEPLOYMENT_CONFIG_UNSUPPORTED"
            if isinstance(exc, ExperimentExecutionConfigurationError)
            else "AI_DIAGNOSIS_NOT_ENABLED"
        )
        return HTTPException(
            HTTP_422_UNPROCESSABLE,
            detail={"code": code, "message": message},
        )

    for exception_types, status_code, code in _ERROR_RULES:
        if isinstance(exc, exception_types):
            return HTTPException(
                status_code, detail={"code": code, "message": _safe_message(exc)}
            )

    logger.exception("unhandled error in the Autopilot API")
    return HTTPException(
        HTTP_500_INTERNAL,
        detail={
            "code": "INTERNAL_ERROR",
            "message": "The request could not be completed.",
        },
    )


#: Exceptions proving that an external Razorpay write was actually attempted.
#: Task 13 classifies each of them onto its ``OperationExecution`` ledger row,
#: and that classification is exactly what a later call needs in order to
#: refuse a blind second external write.
#:
#: ``ExperimentExecutionStateError`` belongs to this set on purpose: the
#: executor can only reach it after the external call returned, which is the
#: most ambiguous case there is. The two refusals below are raised strictly
#: before the ledger is opened, so they are ordinary domain errors.
_EXTERNAL_BOUNDARY_ERRORS = (RazorpayError, ExperimentExecutionError)
_PRE_LEDGER_REFUSALS = (
    ExperimentExecutionAuthorizationError,
    ExperimentExecutionConfigurationError,
)


def _external_ledger_must_survive(exc: BaseException) -> bool:
    """True when this failure left a durable Task 13 operation record behind."""
    return isinstance(exc, _EXTERNAL_BOUNDARY_ERRORS) and not isinstance(
        exc, _PRE_LEDGER_REFUSALS
    )


def _preserve_external_ledger(db: Session) -> None:
    """Persist the operation ledger row that the failed call just wrote.

    On this path the ledger is the *only* thing the request changed: the
    executor records the marker and stops before touching lifecycle state, so
    committing it neither half-applies a lifecycle transition nor hides a
    domain error. A commit that itself fails is logged and swallowed - the
    caller must still see the mapped external failure, and the session is
    discarded when the request ends.
    """
    try:
        db.commit()
    except Exception:  # noqa: BLE001 - never mask the original error
        logger.exception("could not persist the Razorpay operation ledger row")


def _run(
    db: Session, build: Callable[[], T], *, write: bool, preserve_external: bool = False
) -> T:
    """Execute one request, owning the transaction boundary.

    ``write=False`` is a pure read: nothing is committed. ``write=True``
    commits only after the full operation produced a response payload and
    rolls the session back on any error. ``preserve_external`` additionally
    keeps Task 13's operation ledger row when the failure happened at the
    external Razorpay boundary; nothing else about the all-or-nothing rule
    changes.
    """
    try:
        payload = build()
    except Exception as exc:  # noqa: BLE001 - everything is mapped or 500
        if preserve_external and _external_ledger_must_survive(exc):
            _preserve_external_ledger(db)
        else:
            db.rollback()
        raise _to_http_error(exc) from None
    if write:
        db.commit()
    return payload


def read_view(db: Session, build: Callable[[], T]) -> T:
    """Project a read model. GET routes never commit."""
    return _run(db, build, write=False)


def write_view(
    db: Session, build: Callable[[], T], *, preserve_external: bool = False
) -> T:
    """Perform one lifecycle action, commit it, and return its projection.

    ``preserve_external`` is reserved for the routes whose whole job is one
    external write (deploy, rollback) and for the Autopilot step that can
    reach that write.
    """
    return _run(db, build, write=True, preserve_external=preserve_external)


def _opportunities(rows: Any) -> list[schemas.OpportunityResponse]:
    return [schemas.OpportunityResponse.model_validate(row) for row in rows]


def _audit_events(events: Any) -> list[schemas.AuditEventResponse]:
    return [schemas.AuditEventResponse.model_validate(event) for event in events]


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


@router.get(
    "/merchants/{merchant_id}",
    response_model=schemas.MerchantSummary,
    responses=ERROR_RESPONSES,
    tags=["merchant"],
    summary="Read the demo merchant profile",
)
def read_merchant(
    merchant_id: str, db: Session = Depends(get_db)
) -> schemas.MerchantSummary:
    return read_view(
        db,
        lambda: schemas.MerchantSummary.model_validate(
            autopilot.merchant_summary(db, merchant_id)
        ),
    )


@router.get(
    "/merchants/{merchant_id}/overview",
    response_model=schemas.MerchantOverviewResponse,
    responses=ERROR_RESPONSES,
    tags=["merchant"],
    summary="Command Center overview for one merchant",
)
def read_overview(
    merchant_id: str, db: Session = Depends(get_db)
) -> schemas.MerchantOverviewResponse:
    return read_view(
        db,
        lambda: schemas.MerchantOverviewResponse.model_validate(
            autopilot.overview(db, merchant_id)
        ),
    )


@router.get(
    "/merchants/{merchant_id}/opportunities",
    response_model=list[schemas.OpportunityResponse],
    responses=ERROR_RESPONSES,
    tags=["opportunities"],
    summary="List persisted opportunities, most relevant first",
)
def read_opportunities(
    merchant_id: str, db: Session = Depends(get_db)
) -> list[schemas.OpportunityResponse]:
    return read_view(
        db, lambda: _opportunities(autopilot.list_opportunities(db, merchant_id))
    )


@router.get(
    "/opportunities/{opportunity_id}",
    response_model=schemas.OpportunityResponse,
    responses=ERROR_RESPONSES,
    tags=["opportunities"],
    summary="Read one opportunity",
)
def read_opportunity(
    opportunity_id: str, db: Session = Depends(get_db)
) -> schemas.OpportunityResponse:
    return read_view(
        db,
        lambda: schemas.OpportunityResponse.model_validate(
            autopilot.get_opportunity(db, opportunity_id)
        ),
    )


@router.get(
    "/opportunities/{opportunity_id}/cycle",
    response_model=schemas.AutopilotCycleResponse,
    responses=ERROR_RESPONSES,
    tags=["opportunities"],
    summary="Complete persisted Autopilot lifecycle for one opportunity",
)
def read_opportunity_cycle(
    opportunity_id: str, db: Session = Depends(get_db)
) -> schemas.AutopilotCycleResponse:
    """One composite read model for an Autopilot detail view.

    The whole merchant-visible lifecycle - observed evidence, hypothesis,
    experiment plan, policy decision, safe policy limits, deployed Razorpay
    Test resource, runtime progress, statistical result and the audit trail -
    is rebuilt from persisted state alone, so a browser refresh of a detail
    page recovers it from this single response. Stages that have not happened
    yet are explicit ``None`` values. Like every GET, this route never
    commits.
    """
    return read_view(
        db,
        lambda: schemas.AutopilotCycleResponse.model_validate(
            autopilot.get_autopilot_cycle(db, opportunity_id)
        ),
    )


@router.get(
    "/experiments/{experiment_id}",
    response_model=schemas.ExperimentResponse,
    responses=ERROR_RESPONSES,
    tags=["experiments"],
    summary="Read one experiment plan",
)
def read_experiment(
    experiment_id: str, db: Session = Depends(get_db)
) -> schemas.ExperimentResponse:
    return read_view(
        db,
        lambda: schemas.ExperimentResponse.model_validate(
            autopilot.get_experiment(db, experiment_id)
        ),
    )


@router.get(
    "/experiments/{experiment_id}/audit",
    response_model=list[schemas.AuditEventResponse],
    responses=ERROR_RESPONSES,
    tags=["audit"],
    summary="Lifecycle audit trail for one experiment",
)
def read_experiment_audit(
    experiment_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_AUDIT_LIMIT, ge=1, le=MAX_AUDIT_LIMIT),
) -> list[schemas.AuditEventResponse]:
    return read_view(
        db,
        lambda: _audit_events(
            autopilot.experiment_audit_history(db, experiment_id, limit=limit)
        ),
    )


@router.get(
    "/merchants/{merchant_id}/audit",
    response_model=list[schemas.AuditEventResponse],
    responses=ERROR_RESPONSES,
    tags=["audit"],
    summary="Lifecycle audit trail for one merchant",
)
def read_merchant_audit(
    merchant_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(DEFAULT_AUDIT_LIMIT, ge=1, le=MAX_AUDIT_LIMIT),
) -> list[schemas.AuditEventResponse]:
    return read_view(
        db,
        lambda: _audit_events(
            autopilot.merchant_audit_history(db, merchant_id, limit=limit)
        ),
    )


# ---------------------------------------------------------------------------
# Lifecycle actions
# ---------------------------------------------------------------------------


@router.post(
    "/merchants/{merchant_id}/detect",
    response_model=list[schemas.OpportunityResponse],
    responses=ERROR_RESPONSES,
    tags=["opportunities"],
    summary="Run opportunity detection",
)
def detect_opportunities(
    merchant_id: str, db: Session = Depends(get_db)
) -> list[schemas.OpportunityResponse]:
    return write_view(
        db, lambda: _opportunities(autopilot.run_detection(db, merchant_id))
    )


@router.post(
    "/opportunities/{opportunity_id}/diagnose",
    response_model=schemas.HypothesisResponse,
    responses={
        **ERROR_RESPONSES,
        HTTP_502_BAD_GATEWAY: {"model": schemas.ApiErrorResponse},
    },
    tags=["hypotheses"],
    summary="Ask the AI to diagnose an opportunity",
)
def diagnose_opportunity(
    opportunity_id: str,
    db: Session = Depends(get_db),
    openai_client: Any | None = Depends(get_openai_client),
) -> schemas.HypothesisResponse:
    return write_view(
        db,
        lambda: schemas.HypothesisResponse.model_validate(
            autopilot.diagnose(db, opportunity_id, client=openai_client)
        ),
    )


@router.post(
    "/hypotheses/{hypothesis_id}/plan",
    response_model=schemas.ExperimentResponse,
    responses=ERROR_RESPONSES,
    tags=["experiments"],
    summary="Deterministically plan an experiment for a hypothesis",
)
def plan_experiment(
    hypothesis_id: str, db: Session = Depends(get_db)
) -> schemas.ExperimentResponse:
    return write_view(
        db,
        lambda: schemas.ExperimentResponse.model_validate(
            autopilot.plan(db, hypothesis_id)
        ),
    )


@router.post(
    "/experiments/{experiment_id}/policy",
    response_model=schemas.PolicyDecisionResponse,
    responses={
        **ERROR_RESPONSES,
        HTTP_422_UNPROCESSABLE: {"model": schemas.ApiErrorResponse},
    },
    tags=["experiments"],
    summary="Authorize or reject a proposed experiment via merchant policy",
)
def authorize_experiment(
    experiment_id: str, db: Session = Depends(get_db)
) -> schemas.PolicyDecisionResponse:
    return write_view(
        db,
        lambda: schemas.PolicyDecisionResponse.model_validate(
            autopilot.authorize_experiment(db, experiment_id)
        ),
    )


@router.post(
    "/experiments/{experiment_id}/deploy",
    response_model=schemas.RazorpayResourceResponse,
    responses={
        **ERROR_RESPONSES,
        HTTP_403_FORBIDDEN: {"model": schemas.ApiErrorResponse},
        HTTP_502_BAD_GATEWAY: {"model": schemas.ApiErrorResponse},
        HTTP_503_UNAVAILABLE: {"model": schemas.ApiErrorResponse},
    },
    tags=["experiments"],
    summary="Deploy the approved treatment to a real Razorpay Test resource",
)
def deploy_experiment(
    experiment_id: str,
    db: Session = Depends(get_db),
    razorpay_client: Any | None = Depends(get_razorpay_client),
) -> schemas.RazorpayResourceResponse:
    """Create the approved treatment resource exactly once.

    If the external create fails the response is still a mapped failure and no
    lifecycle state advances, but Task 13's record of the attempt survives: an
    ambiguous timeout/5xx keeps the operation ``pending`` so a second call is
    refused instead of issuing a duplicate Razorpay write.
    """
    return write_view(
        db,
        lambda: schemas.RazorpayResourceResponse.model_validate(
            autopilot.deploy(db, experiment_id, razorpay_client=razorpay_client)
        ),
        preserve_external=True,
    )


@router.post(
    "/experiments/{experiment_id}/run",
    response_model=schemas.ExperimentRunResponse,
    responses=ERROR_RESPONSES,
    tags=["experiments"],
    summary="Run one batch of simulated experimental traffic",
)
def run_experiment_batch(
    experiment_id: str,
    db: Session = Depends(get_db),
    payload: schemas.RunBatchRequest | None = Body(
        default=None, description="Optional batch size and fixed seed."
    ),
) -> schemas.ExperimentRunResponse:
    request = payload or schemas.RunBatchRequest()
    return write_view(
        db,
        lambda: schemas.ExperimentRunResponse.model_validate(
            autopilot.run_batch(
                db,
                experiment_id,
                batch_size=request.batch_size,
                seed=request.seed,
            )
        ),
    )


@router.post(
    "/experiments/{experiment_id}/evaluate",
    response_model=schemas.ExperimentResultResponse,
    responses=ERROR_RESPONSES,
    tags=["experiments"],
    summary="Evaluate the fixed horizon and record KEEP/ROLLBACK/INCONCLUSIVE",
)
def evaluate_experiment(
    experiment_id: str, db: Session = Depends(get_db)
) -> schemas.ExperimentResultResponse:
    return write_view(
        db,
        lambda: schemas.ExperimentResultResponse.model_validate(
            autopilot.evaluate(db, experiment_id)
        ),
    )


@router.post(
    "/experiments/{experiment_id}/rollback",
    response_model=schemas.ExperimentRollbackResponse,
    responses=ERROR_RESPONSES,
    tags=["experiments"],
    summary="Cancel the deployed treatment after an explicit ROLLBACK",
)
def rollback_experiment(
    experiment_id: str,
    db: Session = Depends(get_db),
    razorpay_client: Any | None = Depends(get_razorpay_client),
) -> schemas.ExperimentRollbackResponse:
    def build() -> schemas.ExperimentRollbackResponse:
        resource = autopilot.rollback(
            db, experiment_id, razorpay_client=razorpay_client
        )
        return schemas.ExperimentRollbackResponse(
            experiment_id=experiment_id,
            status="rolled_back" if resource is not None else "no_active_resource",
            resource=(
                schemas.RazorpayResourceResponse.model_validate(resource)
                if resource is not None
                else None
            ),
        )

    # Same ledger rule as deploy: an ambiguous cancel stays recorded, so a
    # repeated rollback never issues a second cancel call.
    return write_view(db, build, preserve_external=True)


@router.post(
    "/merchants/{merchant_id}/autopilot/step",
    response_model=schemas.AutopilotStepResponse,
    responses={
        **ERROR_RESPONSES,
        HTTP_503_UNAVAILABLE: {"model": schemas.ApiErrorResponse},
    },
    tags=["autopilot"],
    summary="Advance the Autopilot by exactly one lifecycle transition",
)
def advance_autopilot(
    merchant_id: str,
    db: Session = Depends(get_db),
    openai_client: Any | None = Depends(get_openai_client),
    razorpay_client: Any | None = Depends(get_razorpay_client),
) -> schemas.AutopilotStepResponse:
    """One step per request, on purpose.

    The step decided here is derived entirely from persisted state, so the UI
    can show detection, diagnosis, planning, policy authorization, execution
    and evaluation as separate observable moments instead of hiding the whole
    pipeline behind a single request.

    A step whose external Razorpay write fails is not retried here and does
    not half-apply: the request ends in the mapped failure, while Task 13's
    operation ledger row (``pending`` for an ambiguous write, ``failed`` for a
    definitive one) is kept so the next call is refused rather than repeated.
    """

    def build() -> schemas.AutopilotStepResponse:
        step = autopilot.advance_autopilot(
            db,
            merchant_id,
            openai_client=openai_client,
            razorpay_client=razorpay_client,
        )
        return schemas.AutopilotStepResponse.model_validate(step)

    # The step may be the deploy/rollback action, so the ledger rule applies
    # here as well; every other step keeps the plain all-or-nothing commit.
    return write_view(db, build, preserve_external=True)


__all__ = [
    "router",
    "get_openai_client",
    "get_razorpay_client",
    "read_view",
    "write_view",
]
