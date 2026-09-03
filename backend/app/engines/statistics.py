"""Deterministic fixed-horizon conversion-rate decision engine."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Experiment, ExperimentResult, PaymentAttempt
from app.schemas.statistics import StatisticalEvaluation
from app.services.audit import (
    ACTOR_STATISTICS,
    ENTITY_EXPERIMENT,
    EXPERIMENT_COMPLETED,
    TREATMENT_PROMOTED,
    record_audit_event_once,
)
from app.services.champion import get_merchant_champion_state

ALPHA = 0.05
PRACTICAL_ABSOLUTE_LIFT = 0.02

class StatisticalEvaluationError(ValueError):
    """Raised when experiment data or lifecycle state cannot be evaluated."""

def _validate(name: str, count: int, conversions: int) -> None:
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise StatisticalEvaluationError(f"{name}_count must be a positive integer")
    if isinstance(conversions, bool) or not isinstance(conversions, int):
        raise StatisticalEvaluationError(f"{name}_conversions must be an integer")
    if conversions < 0 or conversions > count:
        raise StatisticalEvaluationError(f"{name}_conversions must be between zero and count")

def evaluate_conversion_experiment(*, experiment_id: str, control_count: int,
    control_conversions: int, treatment_count: int, treatment_conversions: int,
    alpha: float = ALPHA, practical_absolute_lift: float = PRACTICAL_ABSOLUTE_LIFT) -> StatisticalEvaluation:
    _validate("control", control_count, control_conversions)
    _validate("treatment", treatment_count, treatment_conversions)
    if isinstance(alpha, bool) or not math.isfinite(alpha) or not 0 < alpha < 1:
        raise StatisticalEvaluationError("alpha must be finite and between zero and one")
    if isinstance(practical_absolute_lift, bool) or not math.isfinite(practical_absolute_lift) or practical_absolute_lift < 0:
        raise StatisticalEvaluationError("practical_absolute_lift must be finite and non-negative")
    pc, pt = control_conversions / control_count, treatment_conversions / treatment_count
    lift = pt - pc
    pooled = (control_conversions + treatment_conversions) / (control_count + treatment_count)
    se = math.sqrt(pooled * (1 - pooled) * (1 / control_count + 1 / treatment_count))
    if se == 0:
        p_value = 1.0 if lift == 0 else 0.0
    else:
        z = lift / se
        p_value = math.erfc(abs(z) / math.sqrt(2))
    se_diff = math.sqrt(pc * (1-pc) / control_count + pt * (1-pt) / treatment_count)
    margin = 1.959963984540054 * se_diff
    relative = None if pc == 0 else lift / pc
    significant = p_value < alpha
    decision = "KEEP" if significant and lift >= practical_absolute_lift else "ROLLBACK" if significant and lift <= -practical_absolute_lift else "INCONCLUSIVE"
    return StatisticalEvaluation(experiment_id=experiment_id, control_count=control_count, treatment_count=treatment_count,
        control_conversions=control_conversions, treatment_conversions=treatment_conversions, control_rate=pc,
        treatment_rate=pt, absolute_lift=lift, relative_lift=relative, p_value=p_value,
        confidence_interval_lower=lift-margin, confidence_interval_upper=lift+margin,
        is_significant=significant, decision=decision)

def evaluate_experiment_results(db: Session, experiment_id: str) -> ExperimentResult:
    existing = db.scalar(select(ExperimentResult).where(ExperimentResult.experiment_id == experiment_id))
    if existing is not None:
        return existing
    experiment = db.get(Experiment, experiment_id)
    if experiment is None:
        raise StatisticalEvaluationError("experiment not found")
    if experiment.status != "running":
        raise StatisticalEvaluationError("experiment must be running")
    attempts = list(db.scalars(select(PaymentAttempt).where(PaymentAttempt.experiment_id == experiment_id)))
    valid = {"control", "treatment"}
    if any(a.variant is not None and a.variant not in valid for a in attempts):
        raise StatisticalEvaluationError("invalid experimental variant")
    control = [a for a in attempts if a.variant == "control"]
    treatment = [a for a in attempts if a.variant == "treatment"]
    if len(control) < experiment.min_sample_per_variant or len(treatment) < experiment.min_sample_per_variant:
        raise StatisticalEvaluationError("insufficient sample")
    evaluation = evaluate_conversion_experiment(experiment_id=experiment_id, control_count=len(control),
        control_conversions=sum(a.status == "captured" for a in control), treatment_count=len(treatment),
        treatment_conversions=sum(a.status == "captured" for a in treatment))
    decided_at = datetime.now(timezone.utc)
    result = ExperimentResult(**evaluation.model_dump(), decided_at=decided_at)
    db.add(result)
    experiment.status = "completed"
    # PaymentAttempt timestamps may be anchored to a deterministic simulator
    # clock. The Experiment lifecycle must instead record when the statistical
    # decision actually happened so product history cannot appear to end before
    # the experiment was created.
    experiment.ended_at = decided_at
    db.flush()
    record_audit_event_once(
        db,
        merchant_id=experiment.merchant_id,
        event_type=EXPERIMENT_COMPLETED,
        entity_type=ENTITY_EXPERIMENT,
        entity_id=experiment.id,
        data={
            "decision": evaluation.decision,
            "p_value": evaluation.p_value,
            "absolute_lift": evaluation.absolute_lift,
            "control_rate": evaluation.control_rate,
            "treatment_rate": evaluation.treatment_rate,
        },
        actor=ACTOR_STATISTICS,
    )
    if evaluation.decision == "KEEP":
        champion = get_merchant_champion_state(db, experiment.merchant_id)
        record_audit_event_once(
            db,
            merchant_id=experiment.merchant_id,
            event_type=TREATMENT_PROMOTED,
            entity_type=ENTITY_EXPERIMENT,
            entity_id=experiment.id,
            data={
                "champion_version": champion.version,
                "intervention_type": experiment.intervention_type,
                "source_experiment_id": experiment.id,
                "absolute_lift": evaluation.absolute_lift,
                "p_value": evaluation.p_value,
            },
            actor=ACTOR_STATISTICS,
        )
    return result
