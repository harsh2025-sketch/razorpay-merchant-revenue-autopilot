"""One-click fixed-horizon experiment orchestration (Task 21C / Task 22).

This service deliberately coordinates existing boundaries instead of replacing
them:

    persisted APPROVE + deployed treatment
        -> existing deterministic runtime
        -> existing fixed sample horizon
        -> existing statistics engine
        -> KEEP / ROLLBACK / INCONCLUSIVE

The deterministic runtime is the sealed TechBazaar evaluation world. Task 22
makes that boundary explicit: an uploaded merchant can never be silently routed
through TechBazaar's synthetic causal model. Real merchants must eventually be
measured from assigned real payment outcomes supplied by a production payment-
event integration.

This service never calls OpenAI, Razorpay, the policy engine, or the sealed
causal model directly. The Task 11 runtime remains the only simulation boundary
and the Task 12 statistics engine remains the only decision boundary.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db.models import (
    Experiment,
    ExperimentResult,
    PaymentAttempt,
    PolicyDecision,
    RazorpayResource,
)
from app.engines.statistics import evaluate_experiment_results
from app.services.experiments import execute_experiment_batch
from app.services.onboarding import TECHBAZAAR_MERCHANT_ID
from app.simulation.runner import MAX_BATCH_SIZE


ONE_CLICK_MAX_RUNTIME_BATCHES = 25
TREATMENT_VARIANT = "treatment"
PAYMENT_LINK_RESOURCE = "payment_link"


class OneClickExperimentError(Exception):
    """Raised when a run-to-decision request cannot safely proceed."""


class LiveExperimentTrafficRequired(OneClickExperimentError):
    """Uploaded merchants must be measured from real assigned payment outcomes."""


@dataclass(frozen=True)
class OneClickExperimentResult:
    experiment_id: str
    generated_attempts: int
    runtime_batches: int
    control_attempts: int
    treatment_attempts: int
    sample_target_per_variant: int
    decision: str
    absolute_lift: float | None
    p_value: float | None


def _variant_count(db: Session, experiment_id: str, variant: str) -> int:
    return (
        db.query(PaymentAttempt)
        .filter(
            PaymentAttempt.experiment_id == experiment_id,
            PaymentAttempt.variant == variant,
        )
        .count()
    )


def _approved_decision(db: Session, experiment: Experiment) -> PolicyDecision | None:
    return (
        db.query(PolicyDecision)
        .filter(PolicyDecision.experiment_id == experiment.id)
        .filter(PolicyDecision.merchant_id == experiment.merchant_id)
        .order_by(PolicyDecision.evaluated_at.desc(), PolicyDecision.id.desc())
        .first()
    )


def _active_treatment_resource(
    db: Session,
    experiment: Experiment,
) -> RazorpayResource | None:
    return (
        db.query(RazorpayResource)
        .filter(
            RazorpayResource.experiment_id == experiment.id,
            RazorpayResource.resource_type == PAYMENT_LINK_RESOURCE,
            RazorpayResource.variant == TREATMENT_VARIANT,
            RazorpayResource.status == "active",
        )
        .order_by(RazorpayResource.created_at.desc(), RazorpayResource.id.desc())
        .first()
    )


def _existing_result(db: Session, experiment_id: str) -> ExperimentResult | None:
    return (
        db.query(ExperimentResult)
        .filter(ExperimentResult.experiment_id == experiment_id)
        .one_or_none()
    )


def _response(
    db: Session,
    *,
    experiment: Experiment,
    result: ExperimentResult,
    generated_attempts: int,
    runtime_batches: int,
) -> OneClickExperimentResult:
    return OneClickExperimentResult(
        experiment_id=experiment.id,
        generated_attempts=generated_attempts,
        runtime_batches=runtime_batches,
        control_attempts=_variant_count(db, experiment.id, "control"),
        treatment_attempts=_variant_count(db, experiment.id, TREATMENT_VARIANT),
        sample_target_per_variant=int(experiment.min_sample_per_variant),
        decision=str(result.decision),
        absolute_lift=result.absolute_lift,
        p_value=result.p_value,
    )


def run_experiment_to_decision(
    db: Session,
    experiment_id: str,
    *,
    seed: int = 20260827,
    max_runtime_batches: int = ONE_CLICK_MAX_RUNTIME_BATCHES,
) -> OneClickExperimentResult:
    """Drive one authorized TechBazaar experiment to its fixed-horizon decision.

    The operation is idempotent after a result exists. Before runtime starts it
    requires the same persisted authorization and deployed treatment evidence
    that the ordinary Autopilot state machine relies on. Each internal runtime
    call is capped by Task 11's existing ``MAX_BATCH_SIZE`` and stops early as
    soon as both variants reach the configured target.

    Task 22 deliberately refuses to synthesize outcomes for any merchant other
    than the canonical TechBazaar evaluation merchant. Doing otherwise would
    turn a benchmark causal model into fabricated production evidence.

    No commit occurs here. The API boundary owns the transaction, so a runtime
    or statistics failure rolls the whole one-click operation back rather than
    leaving a half-finished user action.
    """
    if isinstance(max_runtime_batches, bool) or not isinstance(max_runtime_batches, int):
        raise OneClickExperimentError("max_runtime_batches must be an integer")
    if max_runtime_batches <= 0 or max_runtime_batches > 100:
        raise OneClickExperimentError("max_runtime_batches must be between 1 and 100")

    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise OneClickExperimentError("experiment not found")

    existing = _existing_result(db, experiment.id)
    if existing is not None:
        return _response(
            db,
            experiment=experiment,
            result=existing,
            generated_attempts=0,
            runtime_batches=0,
        )

    decision = _approved_decision(db, experiment)
    if decision is None or decision.decision != "APPROVE":
        raise OneClickExperimentError(
            "experiment is not authorized by a persisted APPROVE policy decision"
        )

    if _active_treatment_resource(db, experiment) is None:
        raise OneClickExperimentError(
            "experiment has no active deployed treatment resource"
        )

    if experiment.status not in {"approved", "running"}:
        raise OneClickExperimentError(
            f"experiment status {experiment.status!r} cannot enter runtime"
        )

    target = int(experiment.min_sample_per_variant)
    if target <= 0:
        raise OneClickExperimentError("experiment sample target must be positive")

    if experiment.merchant_id != TECHBAZAAR_MERCHANT_ID:
        raise LiveExperimentTrafficRequired(
            "uploaded merchants require assigned real experiment outcomes; "
            "the TechBazaar synthetic runtime is evaluation-only"
        )

    generated_total = 0
    batches = 0

    for _ in range(max_runtime_batches):
        control = _variant_count(db, experiment.id, "control")
        treatment = _variant_count(db, experiment.id, TREATMENT_VARIANT)
        if control >= target and treatment >= target:
            break

        before = (control, treatment)
        summary = execute_experiment_batch(
            db,
            experiment.id,
            batch_size=MAX_BATCH_SIZE,
            seed=seed,
        )
        batches += 1
        generated_total += int(summary.generated_attempts)
        after = (int(summary.control_attempts), int(summary.treatment_attempts))

        if after == before or summary.generated_attempts <= 0:
            raise OneClickExperimentError(
                "experiment runtime made no progress toward the fixed horizon"
            )
    else:
        raise OneClickExperimentError(
            "experiment did not reach the fixed horizon within the runtime safety bound"
        )

    control = _variant_count(db, experiment.id, "control")
    treatment = _variant_count(db, experiment.id, TREATMENT_VARIANT)
    if control < target or treatment < target:
        raise OneClickExperimentError(
            "experiment runtime stopped before both variants reached the fixed horizon"
        )

    # Task 12 remains the single statistical authority. No decision rule is
    # copied into this service.
    result = evaluate_experiment_results(db, experiment.id)
    return _response(
        db,
        experiment=experiment,
        result=result,
        generated_attempts=generated_total,
        runtime_batches=batches,
    )


__all__ = [
    "ONE_CLICK_MAX_RUNTIME_BATCHES",
    "LiveExperimentTrafficRequired",
    "OneClickExperimentError",
    "OneClickExperimentResult",
    "run_experiment_to_decision",
]
