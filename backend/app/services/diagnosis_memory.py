"""Read-only experiment memory for memory-aware AI diagnosis.

Task 19D keeps the LLM in its original role: it may propose a hypothesis, but
persisted merchant history constrains which exact semantic proposals may be
accepted.  This module never calls the model and never maps semantic parameters
to planner/Razorpay configuration.

A previous exact ROLLBACK or INCONCLUSIVE proposal is blocked while observable
evidence remains materially unchanged.  The same proposal may be reconsidered
when the current anomaly has materially moved.  A previous POLICY_REJECTED
proposal remains blocked regardless of conversion movement because changed
observational evidence does not make identical policy-unsafe parameters safer.

The prompt payload also includes compact intervention-family exploration facts.
They do not reveal simulator knowledge or declare a family good or bad; they
only expose which families have already reached terminal trials for the same
merchant segment, so an untried allowed family can be preferred when current
observable evidence reasonably supports one.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    Experiment,
    ExperimentResult,
    Hypothesis,
    Opportunity,
    PolicyDecision,
)

MATERIAL_RATE_DELTA = 0.02
MATERIAL_ATTEMPT_MIN_DELTA = 100
MATERIAL_ATTEMPT_RELATIVE_DELTA = 0.20
DIAGNOSIS_MEMORY_LIMIT = 8

_RATE_KEYS: tuple[str, ...] = (
    "absolute_gap",
    "segment_conversion_rate",
    "comparison_conversion_rate",
)
_BLOCKING_OUTCOMES = frozenset({"ROLLBACK", "INCONCLUSIVE", "POLICY_REJECTED"})
_RESULT_OUTCOMES = frozenset({"KEEP", "ROLLBACK", "INCONCLUSIVE"})


@dataclass(frozen=True)
class DiagnosisMemoryTrial:
    """One prior terminal/rejected semantic hypothesis relevant to diagnosis."""

    experiment_id: str
    opportunity_id: str
    intervention_type: str
    intervention_params: dict[str, Any]
    outcome: str
    absolute_lift: float | None
    p_value: float | None
    evidence_materially_changed: bool
    evidence_change_reasons: tuple[str, ...]

    @property
    def repeat_blocked(self) -> bool:
        if self.outcome == "POLICY_REJECTED":
            return True
        return (
            self.outcome in {"ROLLBACK", "INCONCLUSIVE"}
            and not self.evidence_materially_changed
        )


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def material_evidence_change(
    current_catalog: dict[str, object],
    prior_catalog: dict[str, object],
) -> tuple[bool, tuple[str, ...]]:
    """Return whether observable anomaly evidence moved enough to reconsider.

    Materiality is deliberately small, explicit, and deterministic:
    - >= 2 percentage-point movement in the core conversion/gap rates, or
    - at least 100 additional segment observations *and* >= 20% sample growth.

    Tiny metric noise never counts as a new situation.
    """
    reasons: list[str] = []

    for key in _RATE_KEYS:
        current = _finite_number(current_catalog.get(key))
        prior = _finite_number(prior_catalog.get(key))
        if current is None or prior is None:
            continue
        delta = abs(current - prior)
        if delta > MATERIAL_RATE_DELTA or math.isclose(
            delta,
            MATERIAL_RATE_DELTA,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            reasons.append(f"{key}_delta={delta:.6f}")

    current_attempts = _finite_number(current_catalog.get("segment_attempts"))
    prior_attempts = _finite_number(prior_catalog.get("segment_attempts"))
    if current_attempts is not None and prior_attempts is not None:
        absolute_growth = current_attempts - prior_attempts
        if prior_attempts > 0:
            relative_growth = absolute_growth / prior_attempts
            enough_growth = (
                absolute_growth >= MATERIAL_ATTEMPT_MIN_DELTA
                and relative_growth >= MATERIAL_ATTEMPT_RELATIVE_DELTA
            )
        else:
            enough_growth = absolute_growth >= MATERIAL_ATTEMPT_MIN_DELTA
        if enough_growth:
            reasons.append(f"segment_attempt_growth={int(absolute_growth)}")

    return bool(reasons), tuple(reasons)


def _latest_policy_rows(
    db: Session, experiment_ids: Sequence[str]
) -> dict[str, PolicyDecision]:
    if not experiment_ids:
        return {}
    rows = (
        db.query(PolicyDecision)
        .filter(PolicyDecision.experiment_id.in_(list(experiment_ids)))
        .order_by(
            PolicyDecision.experiment_id.asc(),
            PolicyDecision.evaluated_at.asc(),
            PolicyDecision.id.asc(),
        )
        .all()
    )
    latest: dict[str, PolicyDecision] = {}
    for row in rows:
        latest[row.experiment_id] = row
    return latest


def _result_rows(
    db: Session, experiment_ids: Sequence[str]
) -> dict[str, ExperimentResult]:
    if not experiment_ids:
        return {}
    return {
        row.experiment_id: row
        for row in db.query(ExperimentResult)
        .filter(ExperimentResult.experiment_id.in_(list(experiment_ids)))
        .all()
    }


def build_diagnosis_memory(
    db: Session,
    opportunity: Opportunity,
    current_evidence_catalog: dict[str, object],
    *,
    evidence_catalog_builder: Callable[[Opportunity], dict[str, object]],
    limit: int = DIAGNOSIS_MEMORY_LIMIT,
) -> tuple[DiagnosisMemoryTrial, ...]:
    """Return recent relevant experiment memory without mutating state.

    Only prior experiments for the same merchant and segment are considered.
    Unfinished experiments without a result or policy rejection are omitted.
    The current opportunity is always excluded.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise ValueError("limit must be a positive integer")

    experiments = list(
        db.query(Experiment)
        .filter(
            Experiment.merchant_id == opportunity.merchant_id,
            Experiment.segment == opportunity.segment,
            Experiment.opportunity_id != opportunity.id,
        )
        .order_by(Experiment.created_at.desc(), Experiment.id.desc())
        .all()
    )
    if not experiments:
        return ()

    experiment_ids = [row.id for row in experiments]
    results = _result_rows(db, experiment_ids)
    policies = _latest_policy_rows(db, experiment_ids)

    trials: list[DiagnosisMemoryTrial] = []
    for experiment in experiments:
        result = results.get(experiment.id)
        policy = policies.get(experiment.id)
        if result is not None and result.decision in _RESULT_OUTCOMES:
            outcome = str(result.decision)
        elif policy is not None and policy.decision == "REJECT":
            outcome = "POLICY_REJECTED"
        else:
            continue

        hypothesis = db.get(Hypothesis, experiment.hypothesis_id)
        prior_opportunity = db.get(Opportunity, experiment.opportunity_id)
        if hypothesis is None or prior_opportunity is None:
            continue
        if hypothesis.merchant_id != opportunity.merchant_id:
            continue

        prior_catalog = evidence_catalog_builder(prior_opportunity)
        changed, reasons = material_evidence_change(
            current_evidence_catalog,
            prior_catalog,
        )
        trials.append(
            DiagnosisMemoryTrial(
                experiment_id=experiment.id,
                opportunity_id=prior_opportunity.id,
                intervention_type=hypothesis.intervention_type,
                intervention_params=dict(hypothesis.intervention_params or {}),
                outcome=outcome,
                absolute_lift=(
                    float(result.absolute_lift)
                    if result is not None and result.absolute_lift is not None
                    else None
                ),
                p_value=(
                    float(result.p_value)
                    if result is not None and result.p_value is not None
                    else None
                ),
                evidence_materially_changed=changed,
                evidence_change_reasons=reasons,
            )
        )
        if len(trials) >= limit:
            break

    return tuple(trials)


def prompt_memory_payload(
    trials: Sequence[DiagnosisMemoryTrial],
) -> list[dict[str, object]]:
    """Return compact, merchant-visible memory safe for the diagnosis prompt.

    Existing terminal-trial records remain the payload shape. Exploration facts
    are additive metadata so older prompt/tests/consumers retain their contract.
    """
    if not trials:
        return []

    family_counts: dict[str, int] = {}
    for trial in trials:
        family_counts[trial.intervention_type] = (
            family_counts.get(trial.intervention_type, 0) + 1
        )
    tried_families = sorted(family_counts)
    family_count_payload = {
        family: family_counts[family] for family in tried_families
    }
    guidance = (
        "When current observable evidence plausibly supports more than one "
        "allowed intervention, prefer an untried intervention family before "
        "another parameter variation of a family that has already ended "
        "INCONCLUSIVE or ROLLBACK. Never invent evidence just to force novelty."
    )

    return [
        {
            "experiment_id": trial.experiment_id,
            "intervention_type": trial.intervention_type,
            "intervention_params": dict(trial.intervention_params),
            "outcome": trial.outcome,
            "absolute_lift": trial.absolute_lift,
            "p_value": trial.p_value,
            "evidence_materially_changed": trial.evidence_materially_changed,
            "repeat_blocked": trial.repeat_blocked,
            "tried_intervention_families": tried_families,
            "terminal_trials_by_family": family_count_payload,
            "exploration_guidance": guidance,
        }
        for trial in trials
    ]


def stale_repeat_reason(
    trials: Sequence[DiagnosisMemoryTrial],
    *,
    intervention_type: str,
    intervention_params: dict[str, object],
) -> str | None:
    """Explain why an exact semantic proposal is disallowed, if applicable."""
    params = dict(intervention_params)
    for trial in trials:
        if trial.intervention_type != intervention_type:
            continue
        if trial.intervention_params != params:
            continue
        if trial.outcome == "POLICY_REJECTED":
            return (
                f"exact proposal was policy-rejected in experiment {trial.experiment_id}; "
                "changed conversion evidence does not make identical parameters policy-safe"
            )
        if (
            trial.outcome in {"ROLLBACK", "INCONCLUSIVE"}
            and not trial.evidence_materially_changed
        ):
            return (
                f"exact proposal previously ended {trial.outcome} in experiment "
                f"{trial.experiment_id} and current observable evidence is not materially changed"
            )
    return None


def has_blocking_history(
    trials: Sequence[DiagnosisMemoryTrial],
) -> bool:
    """Whether the prompt contains at least one do-not-repeat historical config."""
    return any(trial.repeat_blocked and trial.outcome in _BLOCKING_OUTCOMES for trial in trials)
