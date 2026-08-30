"""Opportunity detector - segment conversion divergence.

Detects underperforming segments by comparing a segment vs all other segments
combined. Task 21B makes the evidence boundary explicit: opportunity detection
uses historical merchant observations only (``experiment_id IS NULL``), never
simulated experiment traffic, and an exhausted observation revision cannot be
replayed into another detector pass until new data has been appended.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db.models import Opportunity, PaymentAttempt
from app.services.audit import (
    ACTOR_DETECTOR,
    ENTITY_OPPORTUNITY,
    OPPORTUNITY_DETECTED,
    record_audit_event_once,
)
from app.services.incremental_data import has_new_data_since


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


def _compute_severity(
    absolute_gap: float,
    segment_attempts: int,
    total_attempts: int,
) -> float:
    """Deterministic bounded severity: gap * sqrt(segment share)."""
    if total_attempts <= 0 or segment_attempts <= 0:
        return 0.0
    raw = absolute_gap * math.sqrt(segment_attempts / total_attempts)
    return min(1.0, max(0.0, raw))


def _historical_payment_method_metrics(
    db: Session,
    merchant_id: str,
    *,
    segment: str,
) -> Dict[str, dict]:
    rows = (
        db.query(
            PaymentAttempt.payment_method,
            func.count(PaymentAttempt.id).label("attempts"),
            func.sum(case((PaymentAttempt.status == "captured", 1), else_=0)).label(
                "captured"
            ),
            func.sum(case((PaymentAttempt.status == "failed", 1), else_=0)).label(
                "failed"
            ),
            func.sum(case((PaymentAttempt.status == "abandoned", 1), else_=0)).label(
                "abandoned"
            ),
        )
        .filter(PaymentAttempt.merchant_id == merchant_id)
        .filter(PaymentAttempt.experiment_id.is_(None))
        .filter(PaymentAttempt.segment == segment)
        .filter(PaymentAttempt.payment_method.is_not(None))
        .group_by(PaymentAttempt.payment_method)
        .all()
    )
    evidence: Dict[str, dict] = {}
    for payment_method, attempts, captured, failed, abandoned in rows:
        attempts_i = int(attempts or 0)
        captured_i = int(captured or 0)
        evidence[payment_method] = {
            "attempts": attempts_i,
            "captured": captured_i,
            "failed": int(failed or 0),
            "abandoned": int(abandoned or 0),
            "success_rate": (
                captured_i / attempts_i if attempts_i > 0 else None
            ),
        }
    return dict(sorted(evidence.items()))


def _historical_failure_reason_counts(
    db: Session,
    merchant_id: str,
    *,
    segment: str,
) -> Dict[str, int]:
    rows = (
        db.query(PaymentAttempt.failure_reason, func.count(PaymentAttempt.id))
        .filter(PaymentAttempt.merchant_id == merchant_id)
        .filter(PaymentAttempt.experiment_id.is_(None))
        .filter(PaymentAttempt.segment == segment)
        .filter(PaymentAttempt.status == "failed")
        .filter(PaymentAttempt.failure_reason.is_not(None))
        .group_by(PaymentAttempt.failure_reason)
        .all()
    )
    return {reason: int(count or 0) for reason, count in sorted(rows)}


def detect_segment_conversion_opportunities(
    db: Session,
    merchant_id: str,
    *,
    min_segment_attempts: int = 100,
    min_absolute_gap: float = 0.08,
    max_results: int = 3,
) -> List[DetectedOpportunity]:
    """Detect underperforming historical segments vs their complement."""
    rows = (
        db.query(
            PaymentAttempt.segment,
            func.count(PaymentAttempt.id).label("attempts"),
            func.sum(case((PaymentAttempt.status == "captured", 1), else_=0)).label(
                "captured"
            ),
        )
        .filter(PaymentAttempt.merchant_id == merchant_id)
        .filter(PaymentAttempt.experiment_id.is_(None))
        .filter(PaymentAttempt.segment.is_not(None))
        .group_by(PaymentAttempt.segment)
        .all()
    )

    segment_stats: Dict[str, tuple[int, int]] = {}
    total_attempts = 0
    total_captured = 0
    for segment, attempts, captured in rows:
        attempts_i = int(attempts or 0)
        captured_i = int(captured or 0)
        segment_stats[segment] = (attempts_i, captured_i)
        total_attempts += attempts_i
        total_captured += captured_i

    if total_attempts == 0:
        return []

    detected: List[DetectedOpportunity] = []
    for segment, (segment_attempts, segment_captured) in segment_stats.items():
        if segment_attempts < min_segment_attempts:
            continue

        comparison_attempts = total_attempts - segment_attempts
        comparison_captured = total_captured - segment_captured
        if comparison_attempts < min_segment_attempts:
            continue
        if segment_attempts == 0 or comparison_attempts == 0:
            continue

        segment_rate = segment_captured / segment_attempts
        comparison_rate = comparison_captured / comparison_attempts
        gap = comparison_rate - segment_rate
        if gap <= 0 or gap < min_absolute_gap:
            continue

        severity = _compute_severity(gap, segment_attempts, total_attempts)
        evidence = {
            "segment": segment,
            "segment_attempts": segment_attempts,
            "segment_captured": segment_captured,
            "segment_conversion_rate": segment_rate,
            "comparison_attempts": comparison_attempts,
            "comparison_captured": comparison_captured,
            "comparison_conversion_rate": comparison_rate,
            "absolute_gap": gap,
            "payment_method_metrics": _historical_payment_method_metrics(
                db, merchant_id, segment=segment
            ),
            "failure_reasons": _historical_failure_reason_counts(
                db, merchant_id, segment=segment
            ),
        }
        detected.append(
            DetectedOpportunity(
                segment=segment,
                segment_attempts=segment_attempts,
                segment_captured=segment_captured,
                segment_conversion_rate=segment_rate,
                comparison_attempts=comparison_attempts,
                comparison_captured=comparison_captured,
                comparison_conversion_rate=comparison_rate,
                absolute_gap=gap,
                severity=severity,
                evidence=evidence,
            )
        )

    detected.sort(key=lambda row: (-row.severity, row.segment))
    if max_results is not None and max_results >= 0:
        detected = detected[:max_results]
    return detected


ACTIVE_STATUSES = {"detected", "investigating"}


def persist_detected_opportunities(
    db: Session,
    merchant_id: str,
    detected: List[DetectedOpportunity],
) -> List[Opportunity]:
    """Persist detected opportunities, suppressing duplicate active ones."""
    persisted: List[Opportunity] = []
    for item in detected:
        existing = (
            db.query(Opportunity)
            .filter(Opportunity.merchant_id == merchant_id)
            .filter(Opportunity.type == "segment_conversion_divergence")
            .filter(Opportunity.segment == item.segment)
            .filter(Opportunity.status.in_(list(ACTIVE_STATUSES)))
            .order_by(Opportunity.created_at.desc())
            .first()
        )
        if existing is not None:
            persisted.append(existing)
            continue

        opportunity = Opportunity(
            merchant_id=merchant_id,
            type="segment_conversion_divergence",
            segment=item.segment,
            severity=item.severity,
            detected_metric="conversion_rate",
            detected_value=item.segment_conversion_rate,
            baseline_value=item.comparison_conversion_rate,
            evidence=item.evidence,
            status="detected",
        )
        db.add(opportunity)
        db.flush()
        record_audit_event_once(
            db,
            merchant_id=merchant_id,
            event_type=OPPORTUNITY_DETECTED,
            entity_type=ENTITY_OPPORTUNITY,
            entity_id=opportunity.id,
            data={
                "type": opportunity.type,
                "segment": opportunity.segment,
                "severity": opportunity.severity,
            },
            actor=ACTOR_DETECTOR,
        )
        persisted.append(opportunity)
    return persisted


def _detection_allowed_for_current_revision(db: Session, merchant_id: str) -> bool:
    """Refuse a fresh scan after an exhausted unchanged observation pass."""
    active = (
        db.query(Opportunity.id)
        .filter(Opportunity.merchant_id == merchant_id)
        .filter(Opportunity.status.in_(list(ACTIVE_STATUSES)))
        .first()
    )
    if active is not None:
        # Existing active opportunities are returned idempotently by the
        # persistence layer; this is not a new observation pass.
        return True

    latest = (
        db.query(Opportunity)
        .filter(Opportunity.merchant_id == merchant_id)
        .order_by(Opportunity.created_at.desc(), Opportunity.id.desc())
        .first()
    )
    if latest is None:
        return True
    return has_new_data_since(db, merchant_id, latest.created_at)


def run_opportunity_detection(
    db: Session,
    merchant_id: str,
    *,
    min_segment_attempts: int = 100,
    min_absolute_gap: float = 0.08,
    max_results: int = 3,
) -> List[Opportunity]:
    """Detect and persist exactly once per merchant-data revision."""
    if not _detection_allowed_for_current_revision(db, merchant_id):
        return []
    detected = detect_segment_conversion_opportunities(
        db,
        merchant_id,
        min_segment_attempts=min_segment_attempts,
        min_absolute_gap=min_absolute_gap,
        max_results=max_results,
    )
    return persist_detected_opportunities(db, merchant_id, detected)
