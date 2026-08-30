"""Deterministic experiment planner (Task 09).

Converts a validated persisted Hypothesis into a deterministic Experiment
plan using canonical, semantic control/treatment configurations.

Core boundary::

    AI output:  Hypothesis.intervention_type / intervention_params
                                 |
                    DETERMINISTIC EXPERIMENT PLANNER
                                 |
                            Experiment

The LLM never constructs traffic allocation, sample-size targets, Razorpay
API payloads, success thresholds, or rollback decisions - all of that is
controlled by code in this module.

What this module deliberately does NOT do:
- call OpenAI or Razorpay,
- enforce MerchantPolicy limits (Task 10 approves/rejects plans),
- simulate outcomes or access the sealed causal model,
- compute statistical significance or decide winners,
- launch experiments,
- commit the database transaction (caller controls the transaction).

Unsafe-but-well-formed proposals (e.g. a 20% discount above a merchant's
15% policy cap) must survive planning unchanged so the policy layer can
see and reject them.
"""

from __future__ import annotations

import math

from sqlalchemy.orm import Session

from app.db.models import Experiment, Hypothesis, Opportunity
from app.services.audit import (
    ACTOR_PLANNER,
    ENTITY_EXPERIMENT,
    EXPERIMENT_PLANNED,
    record_audit_event_once,
)
from app.schemas.experiment import (
    DEFAULT_GUARDRAILS,
    DEFAULT_MAX_DURATION_HOURS,
    DEFAULT_MIN_SAMPLE_PER_VARIANT,
    DEFAULT_PRIMARY_METRIC,
    DEFAULT_TREATMENT_EXPOSURE,
    ExperimentPlan,
)
from app.schemas.hypothesis import (
    EXPIRY_MAX_HOURS,
    INTERVENTION_TYPE_SET,
    OFFER_DISCOUNT_MAX_PCT,
    PARTIAL_PAYMENT_MIN_MAX_PCT,
    PAYMENT_METHOD_KEYS,
)
from app.services.champion import champion_control_config

# ---------------------------------------------------------------------------
# Planner errors (small, no generic error framework)
# ---------------------------------------------------------------------------


class ExperimentPlanningError(Exception):
    """Raised when a persisted Hypothesis cannot be planned defensively.

    Covers: hypothesis missing, opportunity missing, merchant mismatch,
    hypothesis not proposed, segment missing, unsupported intervention,
    and malformed intervention params. Database content is never trusted
    blindly.
    """


# ---------------------------------------------------------------------------
# Small deterministic helpers
# ---------------------------------------------------------------------------


def _is_number(value: object) -> bool:
    """True for finite ints/floats; bools are not numbers here."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def _reject_unsupported_keys(
    intervention_type: str, params: dict[str, object], allowed_keys: set[str]
) -> None:
    unsupported = sorted(set(params) - allowed_keys)
    if unsupported:
        raise ExperimentPlanningError(
            f"intervention_params for '{intervention_type}' contain unsupported "
            f"keys: {unsupported}; allowed keys: {sorted(allowed_keys)}"
        )


def _require_non_empty_params(
    intervention_type: str, params: object
) -> dict[str, object]:
    if not isinstance(params, dict) or not params:
        raise ExperimentPlanningError(
            f"intervention_params for '{intervention_type}' must be a "
            f"non-empty object"
        )
    return params


# ---------------------------------------------------------------------------
# Deterministic intervention mappings
#
# Each mapping transforms semantic hypothesis params into canonical
# experiment configs. These are NOT raw Razorpay API payloads: no
# "options"/"checkout" nesting, no invented Razorpay offer IDs.
# ---------------------------------------------------------------------------


def _map_payment_method_config(
    params: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    _reject_unsupported_keys("payment_method_config", params, set(PAYMENT_METHOD_KEYS))

    # Preserve only keys present in the hypothesis params, emitted in the
    # canonical payment-method order for full determinism.
    flags = {key: params[key] for key in PAYMENT_METHOD_KEYS if key in params}
    if not flags:
        raise ExperimentPlanningError(
            "payment_method_config params must include at least one of "
            f"{list(PAYMENT_METHOD_KEYS)}"
        )
    for method, value in flags.items():
        if not isinstance(value, bool):
            raise ExperimentPlanningError(
                f"payment_method_config value for '{method}' must be a boolean, "
                f"got {type(value).__name__}"
            )

    control: dict[str, object] = {"payment_methods": "merchant_default"}
    treatment: dict[str, object] = {"payment_methods": flags}
    return control, treatment


def _map_offer_discount(
    params: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    _reject_unsupported_keys("offer_discount", params, {"discount_pct"})

    discount = params.get("discount_pct")
    if not _is_number(discount):
        raise ExperimentPlanningError(
            "offer_discount 'discount_pct' must be a number"
        )
    if not 0 < discount <= OFFER_DISCOUNT_MAX_PCT:
        raise ExperimentPlanningError(
            f"offer_discount 'discount_pct' must satisfy 0 < discount_pct <= "
            f"{OFFER_DISCOUNT_MAX_PCT}, got {discount}"
        )

    # Semantic treatment only. No Razorpay Offer ID is invented here; later
    # execution maps this to a pre-created Offer Registry / Razorpay offer.
    control: dict[str, object] = {"offer": None}
    treatment: dict[str, object] = {"discount_pct": discount}
    return control, treatment


def _map_partial_payment(
    params: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    _reject_unsupported_keys(
        "partial_payment", params, {"accept_partial", "first_min_partial_amount_pct"}
    )

    if "accept_partial" not in params:
        raise ExperimentPlanningError(
            "partial_payment params must include 'accept_partial'"
        )
    accept = params["accept_partial"]
    if not isinstance(accept, bool):
        raise ExperimentPlanningError(
            "partial_payment 'accept_partial' must be a boolean"
        )

    treatment: dict[str, object] = {"accept_partial": accept}
    if "first_min_partial_amount_pct" in params:
        pct = params["first_min_partial_amount_pct"]
        if not _is_number(pct):
            raise ExperimentPlanningError(
                "partial_payment 'first_min_partial_amount_pct' must be a number"
            )
        if not 0 < pct <= PARTIAL_PAYMENT_MIN_MAX_PCT:
            raise ExperimentPlanningError(
                f"partial_payment 'first_min_partial_amount_pct' must satisfy "
                f"0 < pct <= {PARTIAL_PAYMENT_MIN_MAX_PCT}, got {pct}"
            )
        if accept is not True:
            raise ExperimentPlanningError(
                "partial_payment 'first_min_partial_amount_pct' requires "
                "'accept_partial' to be true"
            )
        treatment["first_min_partial_amount_pct"] = pct
    # If first_min_partial_amount_pct is missing: only include accept_partial.

    control: dict[str, object] = {"accept_partial": False}
    return control, treatment


def _map_expiry_config(
    params: dict[str, object],
) -> tuple[dict[str, object], dict[str, object]]:
    _reject_unsupported_keys("expiry_config", params, {"expiry_hours"})

    expiry_hours = params.get("expiry_hours")
    if not _is_number(expiry_hours):
        raise ExperimentPlanningError(
            "expiry_config 'expiry_hours' must be a number"
        )
    if not 0 < expiry_hours <= EXPIRY_MAX_HOURS:
        raise ExperimentPlanningError(
            f"expiry_config 'expiry_hours' must satisfy 0 < expiry_hours <= "
            f"{EXPIRY_MAX_HOURS} (180 days), got {expiry_hours}"
        )

    control: dict[str, object] = {"expiry_hours": "merchant_default"}
    treatment: dict[str, object] = {"expiry_hours": expiry_hours}
    return control, treatment


_CONFIG_BUILDERS = {
    "payment_method_config": _map_payment_method_config,
    "offer_discount": _map_offer_discount,
    "partial_payment": _map_partial_payment,
    "expiry_config": _map_expiry_config,
}


# ---------------------------------------------------------------------------
# Pure plan construction (no DB access)
# ---------------------------------------------------------------------------


def build_experiment_plan(
    *,
    hypothesis: Hypothesis,
    opportunity: Opportunity,
) -> ExperimentPlan:
    """Deterministically convert a Hypothesis + Opportunity into an ExperimentPlan.

    Pure function: reads only the supplied ORM objects, never touches the
    database, never consults MerchantPolicy, never calls OpenAI/Razorpay,
    and never simulates outcomes.

    Defensively rejects malformed rows: merchant mismatch, missing/empty
    segment, hypothesis not in 'proposed' status, unsupported intervention
    types, and malformed intervention params.
    """
    if hypothesis.opportunity_id != opportunity.id:
        raise ExperimentPlanningError(
            f"hypothesis {hypothesis.id!r} is not linked to opportunity "
            f"{opportunity.id!r}"
        )
    if hypothesis.merchant_id != opportunity.merchant_id:
        raise ExperimentPlanningError(
            f"merchant mismatch: hypothesis merchant "
            f"{hypothesis.merchant_id!r} != opportunity merchant "
            f"{opportunity.merchant_id!r}"
        )
    if hypothesis.status != "proposed":
        raise ExperimentPlanningError(
            f"hypothesis {hypothesis.id!r} must have status 'proposed' to be "
            f"planned, got {hypothesis.status!r}"
        )

    segment = opportunity.segment
    if not isinstance(segment, str) or not segment.strip():
        raise ExperimentPlanningError(
            f"opportunity {opportunity.id!r} has no usable segment "
            f"(got {opportunity.segment!r})"
        )
    segment = segment.strip()

    intervention_type = hypothesis.intervention_type
    if intervention_type not in INTERVENTION_TYPE_SET:
        raise ExperimentPlanningError(
            f"unsupported intervention_type: {intervention_type!r}; "
            f"allowed: {sorted(INTERVENTION_TYPE_SET)}"
        )

    params = _require_non_empty_params(intervention_type, hypothesis.intervention_params)
    control_config, treatment_config = _CONFIG_BUILDERS[intervention_type](params)

    return ExperimentPlan(
        merchant_id=hypothesis.merchant_id,
        hypothesis_id=hypothesis.id,
        opportunity_id=opportunity.id,
        name=f"{segment}-{intervention_type}",
        segment=segment,
        intervention_type=intervention_type,
        control_config=control_config,
        treatment_config=treatment_config,
        traffic_split_treatment_pct=DEFAULT_TREATMENT_EXPOSURE,
        primary_metric=DEFAULT_PRIMARY_METRIC,
        guardrail_metrics=list(DEFAULT_GUARDRAILS),
        min_sample_per_variant=DEFAULT_MIN_SAMPLE_PER_VARIANT,
        max_duration_hours=DEFAULT_MAX_DURATION_HOURS,
    )


# ---------------------------------------------------------------------------
# Persistence (flush only - caller controls the transaction)
# ---------------------------------------------------------------------------


def persist_experiment_plan(db: Session, plan: ExperimentPlan) -> Experiment:
    """Persist an ExperimentPlan as an Experiment ORM row.

    Adds and flushes, but never commits - the caller controls the
    transaction. The experiment starts in 'proposed' status with no
    runtime timestamps; policy approval (Task 10) happens later.
    """
    experiment = Experiment(
        merchant_id=plan.merchant_id,
        hypothesis_id=plan.hypothesis_id,
        opportunity_id=plan.opportunity_id,
        name=plan.name,
        segment=plan.segment,
        intervention_type=plan.intervention_type,
        control_config=dict(plan.control_config),
        treatment_config=dict(plan.treatment_config),
        traffic_split_treatment_pct=plan.traffic_split_treatment_pct,
        primary_metric=plan.primary_metric,
        guardrail_metrics=list(plan.guardrail_metrics),
        min_sample_per_variant=plan.min_sample_per_variant,
        max_duration_hours=plan.max_duration_hours,
        status="proposed",
        started_at=None,
        ended_at=None,
    )
    db.add(experiment)
    db.flush()
    record_audit_event_once(
        db,
        merchant_id=experiment.merchant_id,
        event_type=EXPERIMENT_PLANNED,
        entity_type=ENTITY_EXPERIMENT,
        entity_id=experiment.id,
        data={
            "segment": experiment.segment,
            "intervention_type": experiment.intervention_type,
            "traffic_split_treatment_pct": experiment.traffic_split_treatment_pct,
        },
        actor=ACTOR_PLANNER,
    )
    return experiment


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def plan_experiment(db: Session, hypothesis_id: str) -> Experiment:
    """Convert a validated persisted Hypothesis into a persisted Experiment.

    Flow:
    1. fetch the Hypothesis (clear error if missing),
    2. duplicate suppression: return the latest existing Experiment for this
       hypothesis (one Experiment per Hypothesis, regardless of status),
    3. fetch the linked Opportunity,
    4. defensively validate merchant match, hypothesis status, segment,
       intervention type, and params,
    5. build the deterministic plan,
    6. persist the Experiment and flush - never commit.

    MerchantPolicy is deliberately NOT consulted: unsafe proposals (e.g. a
    20% discount or a 10% exposure above merchant caps) must survive to the
    policy layer (Task 10) unchanged.
    """
    hypothesis = db.get(Hypothesis, hypothesis_id)
    if hypothesis is None:
        raise ExperimentPlanningError(f"Hypothesis not found: {hypothesis_id!r}")

    existing = (
        db.query(Experiment)
        .filter(Experiment.hypothesis_id == hypothesis.id)
        .order_by(Experiment.created_at.desc(), Experiment.id.desc())
        .first()
    )
    if existing is not None:
        # Duplicate suppression: one Experiment per Hypothesis.
        return existing

    opportunity = db.get(Opportunity, hypothesis.opportunity_id)
    if opportunity is None:
        raise ExperimentPlanningError(
            f"Opportunity not found for hypothesis {hypothesis.id!r}: "
            f"{hypothesis.opportunity_id!r}"
        )

    plan = build_experiment_plan(hypothesis=hypothesis, opportunity=opportunity)
    champion_control, _champion_version, _champion_source = champion_control_config(
        db,
        merchant_id=hypothesis.merchant_id,
        intervention_type=plan.intervention_type,
        fallback_control=dict(plan.control_config),
    )
    if dict(plan.treatment_config) == champion_control:
        raise ExperimentPlanningError(
            "challenger configuration is identical to current champion"
        )
    if dict(plan.control_config) != champion_control:
        plan = plan.model_copy(update={"control_config": champion_control})
    return persist_experiment_plan(db, plan)
