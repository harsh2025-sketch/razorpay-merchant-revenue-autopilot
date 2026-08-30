"""Deterministic portfolio ranking for newly detected revenue opportunities.

The detector remains the source of observable conversion-gap evidence. This
layer only decides which untouched opportunity deserves scarce experiment
traffic first. It never calls the LLM or hidden evaluation model.

Priority is deliberately explainable rather than learned:

1. Estimate incremental captures if the segment merely matched its observed
   comparison cohort: ``conversion_gap * segment_attempts``.
2. Convert that into a recoverable-GMV *proxy* using the segment's currently
   observed captured average order value. This is an opportunity-sizing proxy,
   not a revenue forecast.
3. Divide by ``1 + prior_terminal_trials_for_segment`` so equally valuable
   untouched segments are explored before repeatedly testing the same segment.
4. Merchant policy must expose at least one allowed intervention for a
   candidate to be considered executable.

No arbitrary weighted blend is used. Detector severity is a deterministic
secondary tie-break when GMV proxies are equal or unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sqlalchemy.orm import Session

from app.db.models import Hypothesis, Merchant, MerchantPolicy, Opportunity
from app.engines.metrics import get_segment_metrics
from app.services.memory import MerchantExperimentMemory, get_merchant_experiment_memory

ACTIVE_OPPORTUNITY_STATUSES = frozenset({"detected", "investigating"})


class OpportunityPortfolioError(Exception):
    """Base error for deterministic opportunity-portfolio reads."""


class OpportunityPortfolioMerchantNotFoundError(OpportunityPortfolioError):
    """The requested merchant does not exist."""


@dataclass(frozen=True)
class RankedOpportunity:
    rank: int
    opportunity_id: str
    segment: str
    status: str
    detector_severity: float
    detected_conversion_rate: float | None
    comparison_conversion_rate: float | None
    conversion_gap: float
    segment_attempts: int
    average_captured_order_value_paise: float | None
    estimated_incremental_captures: float
    estimated_recoverable_gmv_paise: int | None
    prior_terminal_trials: int
    history_factor: float
    allowed_intervention_count: int
    previously_tried_interventions: tuple[str, ...]
    untried_allowed_interventions: tuple[str, ...]
    policy_feasible: bool
    history_adjusted_gmv_proxy_paise: int | None
    priority_index: float


@dataclass(frozen=True)
class OpportunityPortfolio:
    merchant_id: str
    opportunities: tuple[RankedOpportunity, ...]
    next_best_opportunity_id: str | None


def _allowed_interventions(policy: MerchantPolicy | None) -> tuple[str, ...]:
    if policy is None or not isinstance(policy.allowed_interventions, (list, tuple)):
        return ()
    values: list[str] = []
    seen: set[str] = set()
    for value in policy.allowed_interventions:
        if not isinstance(value, str) or not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return tuple(values)


def _conversion_gap(opportunity: Opportunity) -> float:
    evidence_gap = (opportunity.evidence or {}).get("absolute_gap")
    if isinstance(evidence_gap, (int, float)) and not isinstance(evidence_gap, bool):
        return max(0.0, min(1.0, float(evidence_gap)))
    if opportunity.detected_value is None or opportunity.baseline_value is None:
        return 0.0
    return max(0.0, min(1.0, float(opportunity.baseline_value - opportunity.detected_value)))


def _segment_attempts(opportunity: Opportunity) -> int:
    raw = (opportunity.evidence or {}).get("segment_attempts")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return 0
    return max(0, int(raw))


def _tried_interventions(memory: MerchantExperimentMemory, segment: str) -> tuple[str, ...]:
    values = sorted({row.intervention_type for row in memory.records if row.segment == segment})
    return tuple(values)


def _prior_trial_count(memory: MerchantExperimentMemory, segment: str) -> int:
    return sum(row.segment == segment for row in memory.records)


def _candidate_rows(
    db: Session,
    merchant_id: str,
    opportunities: Sequence[Opportunity] | None,
) -> list[Opportunity]:
    if opportunities is not None:
        rows = [row for row in opportunities if row.merchant_id == merchant_id]
    else:
        rows = list(
            db.query(Opportunity)
            .filter(
                Opportunity.merchant_id == merchant_id,
                Opportunity.status.in_(tuple(ACTIVE_OPPORTUNITY_STATUSES)),
            )
            .order_by(
                Opportunity.severity.desc(),
                Opportunity.created_at.desc(),
                Opportunity.id.asc(),
            )
            .all()
        )

    # The portfolio is for opportunities that have not entered AI diagnosis.
    # Half-finished cycles are resumed by Autopilot and are never re-ranked as
    # alternative candidates.
    untouched: list[Opportunity] = []
    for row in rows:
        if row.status not in ACTIVE_OPPORTUNITY_STATUSES:
            continue
        has_hypothesis = (
            db.query(Hypothesis.id).filter(Hypothesis.opportunity_id == row.id).first()
            is not None
        )
        if not has_hypothesis:
            untouched.append(row)
    return untouched


def build_opportunity_portfolio(
    db: Session,
    merchant_id: str,
    *,
    opportunities: Sequence[Opportunity] | None = None,
) -> OpportunityPortfolio:
    """Rank untouched active opportunities without mutating lifecycle state."""
    if db.get(Merchant, merchant_id) is None:
        raise OpportunityPortfolioMerchantNotFoundError(
            f"Merchant not found: {merchant_id!r}"
        )

    candidates = _candidate_rows(db, merchant_id, opportunities)
    if not candidates:
        return OpportunityPortfolio(
            merchant_id=merchant_id,
            opportunities=(),
            next_best_opportunity_id=None,
        )

    policy = (
        db.query(MerchantPolicy).filter(MerchantPolicy.merchant_id == merchant_id).one_or_none()
    )
    allowed = _allowed_interventions(policy)
    memory = get_merchant_experiment_memory(db, merchant_id)
    segment_metrics = {row.segment: row for row in get_segment_metrics(db, merchant_id)}

    provisional: list[dict] = []
    for opportunity in candidates:
        segment = opportunity.segment or ""
        metrics = segment_metrics.get(segment)
        attempts = _segment_attempts(opportunity)
        if attempts == 0 and metrics is not None:
            attempts = metrics.attempts
        gap = _conversion_gap(opportunity)
        incremental_captures = gap * attempts
        aov = metrics.average_captured_order_value_paise if metrics is not None else None
        recoverable_gmv = (
            int(round(incremental_captures * aov))
            if aov is not None and aov >= 0
            else None
        )
        prior_trials = _prior_trial_count(memory, segment)
        history_factor = 1.0 / (1.0 + prior_trials)
        adjusted_gmv = (
            int(round(recoverable_gmv * history_factor))
            if recoverable_gmv is not None
            else None
        )
        tried = _tried_interventions(memory, segment)
        untried = tuple(value for value in allowed if value not in set(tried))
        policy_feasible = bool(allowed)
        provisional.append(
            {
                "opportunity": opportunity,
                "segment": segment,
                "gap": gap,
                "attempts": attempts,
                "aov": aov,
                "incremental_captures": incremental_captures,
                "recoverable_gmv": recoverable_gmv,
                "prior_trials": prior_trials,
                "history_factor": history_factor,
                "adjusted_gmv": adjusted_gmv,
                "tried": tried,
                "untried": untried,
                "policy_feasible": policy_feasible,
                "adjusted_severity": float(opportunity.severity) * history_factor,
            }
        )

    provisional.sort(
        key=lambda row: (
            0 if row["policy_feasible"] else 1,
            -(row["adjusted_gmv"] if row["adjusted_gmv"] is not None else -1),
            -row["adjusted_severity"],
            row["segment"],
            row["opportunity"].id,
        )
    )

    feasible_values = [
        row["adjusted_gmv"]
        for row in provisional
        if row["policy_feasible"] and row["adjusted_gmv"] is not None
    ]
    max_adjusted_gmv = max(feasible_values) if feasible_values else None
    feasible_severity = [
        row["adjusted_severity"] for row in provisional if row["policy_feasible"]
    ]
    max_adjusted_severity = max(feasible_severity) if feasible_severity else 0.0

    ranked: list[RankedOpportunity] = []
    for index, row in enumerate(provisional, start=1):
        if not row["policy_feasible"]:
            priority_index = 0.0
        elif max_adjusted_gmv is not None and max_adjusted_gmv > 0 and row["adjusted_gmv"] is not None:
            priority_index = row["adjusted_gmv"] / max_adjusted_gmv
        elif max_adjusted_severity > 0:
            priority_index = row["adjusted_severity"] / max_adjusted_severity
        else:
            priority_index = 0.0
        opportunity = row["opportunity"]
        ranked.append(
            RankedOpportunity(
                rank=index,
                opportunity_id=opportunity.id,
                segment=row["segment"],
                status=opportunity.status,
                detector_severity=float(opportunity.severity),
                detected_conversion_rate=opportunity.detected_value,
                comparison_conversion_rate=opportunity.baseline_value,
                conversion_gap=row["gap"],
                segment_attempts=row["attempts"],
                average_captured_order_value_paise=row["aov"],
                estimated_incremental_captures=row["incremental_captures"],
                estimated_recoverable_gmv_paise=row["recoverable_gmv"],
                prior_terminal_trials=row["prior_trials"],
                history_factor=row["history_factor"],
                allowed_intervention_count=len(allowed),
                previously_tried_interventions=row["tried"],
                untried_allowed_interventions=row["untried"],
                policy_feasible=row["policy_feasible"],
                history_adjusted_gmv_proxy_paise=row["adjusted_gmv"],
                priority_index=max(0.0, min(1.0, float(priority_index))),
            )
        )

    next_best = next((row.opportunity_id for row in ranked if row.policy_feasible), None)
    return OpportunityPortfolio(
        merchant_id=merchant_id,
        opportunities=tuple(ranked),
        next_best_opportunity_id=next_best,
    )
