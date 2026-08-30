"""Opportunity detector - segment conversion divergence.

Detects underperforming segments by comparing a segment vs all other segments combined.
Uses only observable PaymentAttempt data. No access to sealed evaluation model.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Dict

from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.db.models import PaymentAttempt, Opportunity
from app.services.audit import (
    ACTOR_DETECTOR,
    ENTITY_OPPORTUNITY,
    OPPORTUNITY_DETECTED,
    record_audit_event_once,
)

# We may use metric helpers for evidence, but not required.
# Importing metrics is allowed (they are observable).
from app.engines.metrics import (
    get_payment_method_metrics,
    get_failure_reason_counts,
)


# ---------------------------------------------------------------------------
# Public detected type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DetectedOpportunity:
    segment: str
    segment_attempts: int
    segment_captured: int
    segment_conversion_rate: float
    comparison_attempts: int
    comparison_captured: int
    comparison_conversion_rate: float
    absolute_gap: float
    severity: float
    evidence: dict


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------

def _compute_severity(absolute_gap: float, segment_attempts: int, total_attempts: int) -> float:
    """Deterministic severity: absolute_gap * sqrt(segment_attempts / total_attempts)

    Documented and bounded between 0 and 1.
    """
    if total_attempts <= 0 or segment_attempts <= 0:
        return 0.0
    ratio = segment_attempts / total_attempts
    # ratio in [0,1], sqrt in [0,1]
    raw = absolute_gap * math.sqrt(ratio)
    # Clamp to [0,1]
    if raw < 0.0:
        return 0.0
    if raw > 1.0:
        return 1.0
    return raw


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_segment_conversion_opportunities(
    db: Session,
    merchant_id: str,
    *,
    min_segment_attempts: int = 100,
    min_absolute_gap: float = 0.08,
    max_results: int = 3,
) -> List[DetectedOpportunity]:
    """Detect underperforming segments vs complement baseline.

    - Compares each segment against all other segments combined.
    - Only underperforming (comparison_rate - segment_rate > 0) are considered.
    - Skips segments with insufficient data or gap below threshold.
    - Returns sorted by severity descending, tie-break by segment name.
    """
    # Aggregate per segment: attempts, captured
    rows = (
        db.query(
            PaymentAttempt.segment,
            func.count(PaymentAttempt.id).label("attempts"),
            func.sum(case((PaymentAttempt.status == "captured", 1), else_=0)).label("captured"),
        )
        .filter(PaymentAttempt.merchant_id == merchant_id)
        .filter(PaymentAttempt.segment.is_not(None))
        .group_by(PaymentAttempt.segment)
        .all()
    )

    # Build maps
    segment_stats: Dict[str, tuple[int, int]] = {}  # segment -> (attempts, captured)
    total_attempts = 0
    total_captured = 0
    for seg, attempts, captured in rows:
        attempts_i = int(attempts or 0)
        captured_i = int(captured or 0)
        segment_stats[seg] = (attempts_i, captured_i)
        total_attempts += attempts_i
        total_captured += captured_i

    if total_attempts == 0:
        return []

    detected: List[DetectedOpportunity] = []

    for segment, (seg_attempts, seg_captured) in segment_stats.items():
        # Minimum data rules
        if seg_attempts < min_segment_attempts:
            continue

        comp_attempts = total_attempts - seg_attempts
        comp_captured = total_captured - seg_captured

        if comp_attempts < min_segment_attempts:
            continue

        if seg_attempts == 0 or comp_attempts == 0:
            continue

        seg_rate = seg_captured / seg_attempts if seg_attempts > 0 else None
        comp_rate = comp_captured / comp_attempts if comp_attempts > 0 else None

        if seg_rate is None or comp_rate is None:
            continue

        # Direction: only underperforming
        gap = comp_rate - seg_rate
        if gap <= 0:
            continue

        if gap < min_absolute_gap:
            continue

        severity = _compute_severity(gap, seg_attempts, total_attempts)

        # Build observable evidence
        # Payment method metrics for target segment
        pm_metrics = get_payment_method_metrics(db, merchant_id, segment=segment)
        pm_evidence: Dict[str, dict] = {}
        for pm in pm_metrics:
            pm_evidence[pm.payment_method] = {
                "attempts": pm.attempts,
                "captured": pm.captured,
                "failed": pm.failed,
                "abandoned": pm.abandoned,
                "success_rate": pm.success_rate,
            }

        failure_counts = get_failure_reason_counts(db, merchant_id, segment=segment)

        evidence = {
            "segment": segment,
            "segment_attempts": seg_attempts,
            "segment_captured": seg_captured,
            "segment_conversion_rate": seg_rate,
            "comparison_attempts": comp_attempts,
            "comparison_captured": comp_captured,
            "comparison_conversion_rate": comp_rate,
            "absolute_gap": gap,
            "payment_method_metrics": pm_evidence,
            "failure_reasons": failure_counts,
        }

        detected.append(
            DetectedOpportunity(
                segment=segment,
                segment_attempts=seg_attempts,
                segment_captured=seg_captured,
                segment_conversion_rate=seg_rate,
                comparison_attempts=comp_attempts,
                comparison_captured=comp_captured,
                comparison_conversion_rate=comp_rate,
                absolute_gap=gap,
                severity=severity,
                evidence=evidence,
            )
        )

    # Sort by severity descending, tie-break by segment name deterministically
    detected.sort(key=lambda d: (-d.severity, d.segment))

    # Enforce max_results
    if max_results is not None and max_results >= 0:
        detected = detected[:max_results]

    return detected


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

ACTIVE_STATUSES = {"detected", "investigating"}


def persist_detected_opportunities(
    db: Session,
    merchant_id: str,
    detected: List[DetectedOpportunity],
) -> List[Opportunity]:
    """Persist detected opportunities, suppressing duplicate active ones.

    - If an existing Opportunity exists with same merchant_id, type,
      segment, and status in active set, return existing without creating duplicate.
    - Otherwise create new Opportunity record.
    - Flushes but does not commit; caller may commit.
    """
    persisted: List[Opportunity] = []

    for det in detected:
        # Check for existing active opportunity
        existing = (
            db.query(Opportunity)
            .filter(Opportunity.merchant_id == merchant_id)
            .filter(Opportunity.type == "segment_conversion_divergence")
            .filter(Opportunity.segment == det.segment)
            .filter(Opportunity.status.in_(list(ACTIVE_STATUSES)))
            .order_by(Opportunity.created_at.desc())
            .first()
        )

        if existing is not None:
            persisted.append(existing)
            continue

        opp = Opportunity(
            merchant_id=merchant_id,
            type="segment_conversion_divergence",
            segment=det.segment,
            severity=det.severity,
            detected_metric="conversion_rate",
            detected_value=det.segment_conversion_rate,
            baseline_value=det.comparison_conversion_rate,
            evidence=det.evidence,
            status="detected",
        )
        db.add(opp)
        # Flush to get ID and make it visible for subsequent duplicate checks in same batch
        db.flush()
        record_audit_event_once(
            db,
            merchant_id=merchant_id,
            event_type=OPPORTUNITY_DETECTED,
            entity_type=ENTITY_OPPORTUNITY,
            entity_id=opp.id,
            data={
                "type": opp.type,
                "segment": opp.segment,
                "severity": opp.severity,
            },
            actor=ACTOR_DETECTOR,
        )
        persisted.append(opp)

    return persisted


def run_opportunity_detection(
    db: Session,
    merchant_id: str,
    *,
    min_segment_attempts: int = 100,
    min_absolute_gap: float = 0.08,
    max_results: int = 3,
) -> List[Opportunity]:
    """Convenience: detect and persist.

    Does not commit; caller may commit. Flushes via persist helper.
    """
    detected = detect_segment_conversion_opportunities(
        db,
        merchant_id,
        min_segment_attempts=min_segment_attempts,
        min_absolute_gap=min_absolute_gap,
        max_results=max_results,
    )
    persisted = persist_detected_opportunities(db, merchant_id, detected)
    return persisted
