"""Deterministic merchant experiment memory derived from persisted lifecycle truth.

This module intentionally owns no database table. Memory is a read model over
existing experiments, policy decisions, statistical results, and treatment
resources so there is only one source of truth for previous optimization
cycles.

Only terminal or policy-rejected experiments become learned history. Active
work is excluded until the lifecycle has reached a safe terminal boundary.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import (
    Experiment,
    ExperimentResult,
    Merchant,
    PolicyDecision,
    RazorpayResource,
)

TERMINAL_EXPERIMENT_STATUSES = frozenset({"completed", "rolled_back", "cancelled"})
STATISTICAL_DECISIONS = frozenset({"KEEP", "ROLLBACK", "INCONCLUSIVE"})


class MerchantMemoryError(Exception):
    """Base error for deterministic merchant-memory reads."""


class MerchantMemoryNotFoundError(MerchantMemoryError):
    """The requested merchant does not exist."""


@dataclass(frozen=True)
class ExperimentMemoryRecord:
    """One immutable learned record derived from a finished experiment cycle."""

    experiment_id: str
    opportunity_id: str
    segment: str
    intervention_type: str
    treatment_config: dict[str, Any]
    treatment_config_fingerprint: str
    experiment_status: str
    policy_decision: str | None
    policy_violations: tuple[str, ...]
    statistical_decision: str | None
    control_rate: float | None
    treatment_rate: float | None
    absolute_lift: float | None
    relative_lift: float | None
    p_value: float | None
    confidence_interval_lower: float | None
    confidence_interval_upper: float | None
    is_significant: bool | None
    treatment_resource_status: str | None
    terminal_reason: str
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None


@dataclass(frozen=True)
class InterventionKnowledge:
    """Aggregated learned history for one merchant segment/intervention pair."""

    segment: str
    intervention_type: str
    trial_count: int
    approved_count: int
    rejected_count: int
    completed_result_count: int
    keep_count: int
    rollback_count: int
    inconclusive_count: int
    latest_outcome: str
    latest_experiment_id: str
    latest_treatment_config: dict[str, Any]
    latest_treatment_config_fingerprint: str
    latest_absolute_lift: float | None
    latest_p_value: float | None


@dataclass(frozen=True)
class MerchantExperimentMemory:
    """Complete structured experiment memory for one merchant."""

    merchant_id: str
    records: tuple[ExperimentMemoryRecord, ...]
    knowledge: tuple[InterventionKnowledge, ...]
    trial_count: int
    completed_result_count: int
    policy_rejection_count: int
    keep_count: int
    rollback_count: int
    inconclusive_count: int


def treatment_config_fingerprint(config: dict[str, Any]) -> str:
    """Return a stable SHA-256 fingerprint for an experiment treatment config."""
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _latest_policy_rows(db: Session, experiment_ids: list[str]) -> dict[str, PolicyDecision]:
    if not experiment_ids:
        return {}
    rows = (
        db.query(PolicyDecision)
        .filter(PolicyDecision.experiment_id.in_(experiment_ids))
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


def _result_rows(db: Session, experiment_ids: list[str]) -> dict[str, ExperimentResult]:
    if not experiment_ids:
        return {}
    return {
        row.experiment_id: row
        for row in db.query(ExperimentResult)
        .filter(ExperimentResult.experiment_id.in_(experiment_ids))
        .all()
    }


def _latest_treatment_resources(
    db: Session, experiment_ids: list[str]
) -> dict[str, RazorpayResource]:
    if not experiment_ids:
        return {}
    rows = (
        db.query(RazorpayResource)
        .filter(
            RazorpayResource.experiment_id.in_(experiment_ids),
            RazorpayResource.variant == "treatment",
        )
        .order_by(
            RazorpayResource.experiment_id.asc(),
            RazorpayResource.created_at.asc(),
            RazorpayResource.id.asc(),
        )
        .all()
    )
    latest: dict[str, RazorpayResource] = {}
    for row in rows:
        if row.experiment_id is not None:
            latest[row.experiment_id] = row
    return latest


def _terminal_reason(
    experiment: Experiment,
    policy: PolicyDecision | None,
    result: ExperimentResult | None,
) -> str:
    if result is not None and result.decision in STATISTICAL_DECISIONS:
        return f"statistical_{result.decision.lower()}"
    if policy is not None and policy.decision == "REJECT":
        return "policy_rejected"
    if experiment.status == "rolled_back":
        return "rolled_back"
    if experiment.status == "cancelled":
        return "cancelled"
    return "completed_without_statistical_result"


def _is_memory_eligible(
    experiment: Experiment,
    policy: PolicyDecision | None,
    result: ExperimentResult | None,
) -> bool:
    if result is not None:
        return True
    if policy is not None and policy.decision == "REJECT":
        return True
    return experiment.status in TERMINAL_EXPERIMENT_STATUSES


def _build_knowledge(
    records: tuple[ExperimentMemoryRecord, ...],
) -> tuple[InterventionKnowledge, ...]:
    grouped: dict[tuple[str, str], list[ExperimentMemoryRecord]] = {}
    for record in records:
        grouped.setdefault((record.segment, record.intervention_type), []).append(record)

    knowledge: list[InterventionKnowledge] = []
    for (segment, intervention_type), trials in sorted(grouped.items()):
        latest = trials[-1]
        knowledge.append(
            InterventionKnowledge(
                segment=segment,
                intervention_type=intervention_type,
                trial_count=len(trials),
                approved_count=sum(row.policy_decision == "APPROVE" for row in trials),
                rejected_count=sum(row.policy_decision == "REJECT" for row in trials),
                completed_result_count=sum(row.statistical_decision is not None for row in trials),
                keep_count=sum(row.statistical_decision == "KEEP" for row in trials),
                rollback_count=sum(row.statistical_decision == "ROLLBACK" for row in trials),
                inconclusive_count=sum(
                    row.statistical_decision == "INCONCLUSIVE" for row in trials
                ),
                latest_outcome=latest.terminal_reason,
                latest_experiment_id=latest.experiment_id,
                latest_treatment_config=dict(latest.treatment_config),
                latest_treatment_config_fingerprint=latest.treatment_config_fingerprint,
                latest_absolute_lift=latest.absolute_lift,
                latest_p_value=latest.p_value,
            )
        )
    return tuple(knowledge)


def get_merchant_experiment_memory(
    db: Session, merchant_id: str
) -> MerchantExperimentMemory:
    """Derive structured learned history for a merchant without mutating state.

    The query is deterministic and deliberately excludes active experiments.
    It never calls the LLM, policy engine, executor, simulation model, or audit
    writer and never commits the SQLAlchemy session.
    """
    if db.get(Merchant, merchant_id) is None:
        raise MerchantMemoryNotFoundError(f"Merchant not found: {merchant_id!r}")

    experiments = list(
        db.query(Experiment)
        .filter(Experiment.merchant_id == merchant_id)
        .order_by(Experiment.created_at.asc(), Experiment.id.asc())
        .all()
    )
    experiment_ids = [row.id for row in experiments]
    policies = _latest_policy_rows(db, experiment_ids)
    results = _result_rows(db, experiment_ids)
    resources = _latest_treatment_resources(db, experiment_ids)

    records: list[ExperimentMemoryRecord] = []
    for experiment in experiments:
        policy = policies.get(experiment.id)
        result = results.get(experiment.id)
        if not _is_memory_eligible(experiment, policy, result):
            continue
        resource = resources.get(experiment.id)
        config = dict(experiment.treatment_config or {})
        violations = tuple(str(value) for value in ((policy.violations if policy else None) or []))
        records.append(
            ExperimentMemoryRecord(
                experiment_id=experiment.id,
                opportunity_id=experiment.opportunity_id,
                segment=experiment.segment,
                intervention_type=experiment.intervention_type,
                treatment_config=config,
                treatment_config_fingerprint=treatment_config_fingerprint(config),
                experiment_status=experiment.status,
                policy_decision=policy.decision if policy is not None else None,
                policy_violations=violations,
                statistical_decision=result.decision if result is not None else None,
                control_rate=result.control_rate if result is not None else None,
                treatment_rate=result.treatment_rate if result is not None else None,
                absolute_lift=result.absolute_lift if result is not None else None,
                relative_lift=result.relative_lift if result is not None else None,
                p_value=result.p_value if result is not None else None,
                confidence_interval_lower=(
                    result.confidence_interval_lower if result is not None else None
                ),
                confidence_interval_upper=(
                    result.confidence_interval_upper if result is not None else None
                ),
                is_significant=result.is_significant if result is not None else None,
                treatment_resource_status=resource.status if resource is not None else None,
                terminal_reason=_terminal_reason(experiment, policy, result),
                created_at=experiment.created_at,
                started_at=experiment.started_at,
                ended_at=experiment.ended_at,
            )
        )

    record_tuple = tuple(records)
    knowledge = _build_knowledge(record_tuple)
    return MerchantExperimentMemory(
        merchant_id=merchant_id,
        records=record_tuple,
        knowledge=knowledge,
        trial_count=len(record_tuple),
        completed_result_count=sum(row.statistical_decision is not None for row in record_tuple),
        policy_rejection_count=sum(row.policy_decision == "REJECT" for row in record_tuple),
        keep_count=sum(row.statistical_decision == "KEEP" for row in record_tuple),
        rollback_count=sum(row.statistical_decision == "ROLLBACK" for row in record_tuple),
        inconclusive_count=sum(
            row.statistical_decision == "INCONCLUSIVE" for row in record_tuple
        ),
    )


def find_equivalent_trials(
    memory: MerchantExperimentMemory,
    *,
    segment: str,
    intervention_type: str,
    treatment_config: dict[str, Any],
) -> tuple[ExperimentMemoryRecord, ...]:
    """Return previous materially identical trials for future memory-aware policy.

    Equivalence is intentionally strict in 19A: same segment, same semantic
    intervention, and the same canonical treatment configuration fingerprint.
    Later phases may decide whether changed evidence is enough to permit a
    repeated trial, but this memory layer only reports persisted history.
    """
    fingerprint = treatment_config_fingerprint(treatment_config)
    return tuple(
        row
        for row in memory.records
        if row.segment == segment
        and row.intervention_type == intervention_type
        and row.treatment_config_fingerprint == fingerprint
    )
