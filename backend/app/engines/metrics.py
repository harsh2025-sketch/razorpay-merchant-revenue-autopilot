"""Metric engine - deterministic, database-backed, merchant-visible.

This module computes merchant-facing payment metrics from historical
PaymentAttempt rows only. Experiment runtime traffic is deliberately excluded
(``experiment_id IS NULL`` is required) so simulated control/treatment samples
can never change the merchant baseline shown on Overview or used for portfolio
sizing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Optional

from sqlalchemy import func, case, select
from sqlalchemy.orm import Session

from app.db.models import PaymentAttempt

# ---------------------------------------------------------------------------
# Public metric types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConversionMetrics:
    attempts: int
    captured: int
    failed: int
    abandoned: int
    conversion_rate: float | None


@dataclass(frozen=True)
class PaymentMethodMetrics:
    payment_method: str
    attempts: int
    captured: int
    failed: int
    abandoned: int
    success_rate: float | None


@dataclass(frozen=True)
class SegmentMetrics:
    segment: str
    attempts: int
    captured: int
    failed: int
    abandoned: int
    conversion_rate: float | None
    gmv_paise: int
    captured_gmv_paise: int
    average_captured_order_value_paise: float | None


@dataclass(frozen=True)
class AmountBucketMetrics:
    bucket_label: str
    min_amount_paise: int
    max_amount_paise: int | None
    attempts: int
    captured: int
    conversion_rate: float | None


# ---------------------------------------------------------------------------
# Bucket definitions (paise)
# ---------------------------------------------------------------------------

# Fixed deterministic buckets:
# 0–₹1000, ₹1000–₹3000, ₹3000–₹5000, ₹5000–₹10000, ₹10000+
# Lower inclusive, upper exclusive except last.
BUCKET_DEFINITIONS: list[tuple[str, int, int | None]] = [
    ("0-₹1000", 0, 100_000),
    ("₹1000-₹3000", 100_000, 300_000),
    ("₹3000-₹5000", 300_000, 500_000),
    ("₹5000-₹10000", 500_000, 1_000_000),
    ("₹10000+", 1_000_000, None),
]

# Fixed payment method order for deterministic sorting
PAYMENT_METHOD_ORDER: list[str] = ["upi", "card", "netbanking", "wallet"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _conversion_rate(captured: int, attempts: int) -> float | None:
    if attempts == 0:
        return None
    return captured / attempts


def _success_rate(captured: int, attempts: int) -> float | None:
    return _conversion_rate(captured, attempts)


def _aov(captured_gmv: int, captured_count: int) -> float | None:
    if captured_count == 0:
        return None
    return captured_gmv / captured_count


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_overall_metrics(
    db: Session,
    merchant_id: str,
) -> ConversionMetrics:
    """Compute historical conversion metrics for a merchant."""
    # Use SQL aggregation for efficiency. Experimental observations are not
    # merchant baseline evidence and therefore never enter this read model.
    total = db.query(func.count(PaymentAttempt.id)).filter(
        PaymentAttempt.merchant_id == merchant_id,
        PaymentAttempt.experiment_id.is_(None),
    ).scalar() or 0

    if total == 0:
        return ConversionMetrics(
            attempts=0,
            captured=0,
            failed=0,
            abandoned=0,
            conversion_rate=None,
        )

    # Group by status in one query
    rows = (
        db.query(PaymentAttempt.status, func.count(PaymentAttempt.id))
        .filter(
            PaymentAttempt.merchant_id == merchant_id,
            PaymentAttempt.experiment_id.is_(None),
        )
        .group_by(PaymentAttempt.status)
        .all()
    )
    status_counts: dict[str, int] = {status: cnt for status, cnt in rows}

    captured = status_counts.get("captured", 0)
    failed = status_counts.get("failed", 0)
    abandoned = status_counts.get("abandoned", 0)

    # Attempts is total, but ensure it matches sum of known statuses;
    # if there are other statuses, include them in total but not in breakdown?
    # For this task statuses are only captured/failed/abandoned, so sum should equal total.
    # Use total as attempts.
    conv = _conversion_rate(captured, total)

    return ConversionMetrics(
        attempts=total,
        captured=captured,
        failed=failed,
        abandoned=abandoned,
        conversion_rate=conv,
    )


def get_segment_metrics(
    db: Session,
    merchant_id: str,
) -> List[SegmentMetrics]:
    """Compute historical metrics per segment, sorted by segment name."""
    # Aggregate per segment using CASE
    # SELECT segment, count(*), sum(captured), etc., sum(amount), sum(captured amount)
    query = (
        db.query(
            PaymentAttempt.segment,
            func.count(PaymentAttempt.id).label("attempts"),
            func.sum(case((PaymentAttempt.status == "captured", 1), else_=0)).label("captured"),
            func.sum(case((PaymentAttempt.status == "failed", 1), else_=0)).label("failed"),
            func.sum(case((PaymentAttempt.status == "abandoned", 1), else_=0)).label("abandoned"),
            func.sum(PaymentAttempt.amount).label("gmv"),
            func.sum(case((PaymentAttempt.status == "captured", PaymentAttempt.amount), else_=0)).label("captured_gmv"),
        )
        .filter(PaymentAttempt.merchant_id == merchant_id)
        .filter(PaymentAttempt.experiment_id.is_(None))
        .filter(PaymentAttempt.segment.is_not(None))
        .group_by(PaymentAttempt.segment)
        .order_by(PaymentAttempt.segment.asc())
        .all()
    )

    results: List[SegmentMetrics] = []
    for row in query:
        segment_name, attempts, captured, failed, abandoned, gmv, captured_gmv = row
        attempts = int(attempts or 0)
        captured = int(captured or 0)
        failed = int(failed or 0)
        abandoned = int(abandoned or 0)
        gmv = int(gmv or 0)
        captured_gmv = int(captured_gmv or 0)

        conv = _conversion_rate(captured, attempts)
        aov = _aov(captured_gmv, captured)

        results.append(
            SegmentMetrics(
                segment=segment_name,
                attempts=attempts,
                captured=captured,
                failed=failed,
                abandoned=abandoned,
                conversion_rate=conv,
                gmv_paise=gmv,
                captured_gmv_paise=captured_gmv,
                average_captured_order_value_paise=aov,
            )
        )

    # Already sorted by segment name via ORDER BY, but ensure deterministic
    results.sort(key=lambda x: x.segment)
    return results


def get_payment_method_metrics(
    db: Session,
    merchant_id: str,
    *,
    segment: str | None = None,
) -> List[PaymentMethodMetrics]:
    """Compute historical payment-method metrics, optionally by segment.

    Deterministic ordering: upi, card, netbanking, wallet, then unknown sorted.
    """
    q = db.query(
        PaymentAttempt.payment_method,
        func.count(PaymentAttempt.id).label("attempts"),
        func.sum(case((PaymentAttempt.status == "captured", 1), else_=0)).label("captured"),
        func.sum(case((PaymentAttempt.status == "failed", 1), else_=0)).label("failed"),
        func.sum(case((PaymentAttempt.status == "abandoned", 1), else_=0)).label("abandoned"),
    ).filter(
        PaymentAttempt.merchant_id == merchant_id,
        PaymentAttempt.experiment_id.is_(None),
    )

    if segment is not None:
        q = q.filter(PaymentAttempt.segment == segment)

    q = q.filter(PaymentAttempt.payment_method.is_not(None)).group_by(PaymentAttempt.payment_method).all()

    metrics: List[PaymentMethodMetrics] = []
    for pm, attempts, captured, failed, abandoned in q:
        attempts = int(attempts or 0)
        captured = int(captured or 0)
        failed = int(failed or 0)
        abandoned = int(abandoned or 0)
        success = _success_rate(captured, attempts)
        metrics.append(
            PaymentMethodMetrics(
                payment_method=pm,
                attempts=attempts,
                captured=captured,
                failed=failed,
                abandoned=abandoned,
                success_rate=success,
            )
        )

    # Deterministic ordering
    order_map = {name: idx for idx, name in enumerate(PAYMENT_METHOD_ORDER)}

    def sort_key(m: PaymentMethodMetrics):
        # Known methods first in fixed order, unknown after sorted alphabetically
        if m.payment_method in order_map:
            return (0, order_map[m.payment_method], m.payment_method)
        else:
            return (1, len(PAYMENT_METHOD_ORDER), m.payment_method)

    metrics.sort(key=sort_key)
    return metrics


def get_failure_reason_counts(
    db: Session,
    merchant_id: str,
    *,
    segment: str | None = None,
) -> Dict[str, int]:
    """Count historical failure reasons for failed attempts only."""
    q = (
        db.query(PaymentAttempt.failure_reason, func.count(PaymentAttempt.id))
        .filter(PaymentAttempt.merchant_id == merchant_id)
        .filter(PaymentAttempt.experiment_id.is_(None))
        .filter(PaymentAttempt.status == "failed")
    )

    if segment is not None:
        q = q.filter(PaymentAttempt.segment == segment)

    q = q.filter(PaymentAttempt.failure_reason.is_not(None)).group_by(PaymentAttempt.failure_reason).all()

    result: Dict[str, int] = {}
    for reason, cnt in q:
        if reason is None:
            continue
        result[reason] = int(cnt)

    # Deterministic ordering not required for dict, but we can sort keys if needed downstream.
    # Return as regular dict.
    return result


def get_amount_bucket_metrics(
    db: Session,
    merchant_id: str,
) -> List[AmountBucketMetrics]:
    """Compute historical metrics per fixed amount bucket."""
    results: List[AmountBucketMetrics] = []

    for label, min_paise, max_paise in BUCKET_DEFINITIONS:
        # Build query for this bucket
        base_q = db.query(func.count(PaymentAttempt.id)).filter(
            PaymentAttempt.merchant_id == merchant_id,
            PaymentAttempt.experiment_id.is_(None),
            PaymentAttempt.amount >= min_paise,
        )
        if max_paise is not None:
            base_q = base_q.filter(PaymentAttempt.amount < max_paise)

        attempts = base_q.scalar() or 0

        captured_q = db.query(func.count(PaymentAttempt.id)).filter(
            PaymentAttempt.merchant_id == merchant_id,
            PaymentAttempt.experiment_id.is_(None),
            PaymentAttempt.status == "captured",
            PaymentAttempt.amount >= min_paise,
        )
        if max_paise is not None:
            captured_q = captured_q.filter(PaymentAttempt.amount < max_paise)

        captured = captured_q.scalar() or 0

        conv = _conversion_rate(int(captured), int(attempts))

        results.append(
            AmountBucketMetrics(
                bucket_label=label,
                min_amount_paise=min_paise,
                max_amount_paise=max_paise,
                attempts=int(attempts),
                captured=int(captured),
                conversion_rate=conv,
            )
        )

    # Already in ascending order by definition
    return results
