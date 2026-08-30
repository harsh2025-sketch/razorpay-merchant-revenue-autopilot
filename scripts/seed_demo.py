#!/usr/bin/env python3
"""Seed helpers for the TechBazaar Electronics demo baseline.

``seed_demo`` remains the explicit destructive seeding command used by the
local demo reset flow. ``ensure_demo_baseline`` is the non-destructive helper
used by production bootstrap: it creates missing canonical rows without wiping
or advancing lifecycle state.
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

# Add backend directory to sys.path so app modules can be imported
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import Base
from app.db.models import (
    AuditEvent,
    Experiment,
    ExperimentAssignment,
    ExperimentResult,
    Hypothesis,
    Merchant,
    MerchantPolicy,
    OperationExecution,
    Opportunity,
    PaymentAttempt,
    PolicyDecision,
    RazorpayResource,
)
from app.db.session import SessionLocal, create_db_and_tables
from app.simulation.generator import BaselinePaymentEvent, generate_baseline_events
from app.simulation.merchant import TECHBAZAAR_PROFILE

DEFAULT_DEMO_SEED = 20260827
DEFAULT_DEMO_DAYS = 30
TECHBAZAAR_POLICY_ID = "policy_techbazaar"
PAYMENT_ATTEMPT_ID_CHUNK_SIZE = 500


def _ensure_sqlite_parent_dir() -> None:
    """Create the parent directory for local SQLite databases when needed."""
    url = get_settings().DATABASE_URL.strip()
    if not url.startswith("sqlite:///"):
        return

    db_path = url.replace("sqlite:///", "", 1)
    if db_path and db_path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)


def _create_tables(db: Session | None) -> None:
    """Create SQLAlchemy tables on either an injected session or settings DB."""
    if db is None:
        create_db_and_tables()
        return
    Base.metadata.create_all(bind=db.get_bind())


def _demo_merchant() -> Merchant:
    return Merchant(
        id=TECHBAZAAR_PROFILE.merchant_id,
        name=TECHBAZAAR_PROFILE.name,
        category=TECHBAZAAR_PROFILE.category,
        monthly_gmv=TECHBAZAAR_PROFILE.monthly_gmv_paise,
    )


def _demo_policy() -> MerchantPolicy:
    return MerchantPolicy(
        id=TECHBAZAAR_POLICY_ID,
        merchant_id=TECHBAZAAR_PROFILE.merchant_id,
        max_experiment_exposure_pct=0.10,
        max_discount_pct=0.15,
        min_margin_pct=0.05,
        max_concurrent_experiments=3,
        max_experiment_duration_hours=168,
        min_sample_size=30,
        max_financial_exposure=50000,
        allowed_interventions=[
            "payment_method_config",
            "offer_discount",
            "partial_payment",
            "expiry_config",
        ],
    )


def _payment_attempt_from_event(evt: BaselinePaymentEvent) -> PaymentAttempt:
    return PaymentAttempt(
        id=evt.id,
        merchant_id=evt.merchant_id,
        customer_ref=evt.customer_ref,
        amount=evt.amount,
        currency=evt.currency,
        payment_method=evt.payment_method,
        status=evt.status,
        failure_reason=evt.failure_reason,
        device_type=evt.device_type,
        segment=evt.segment,
        source=evt.source,
        created_at=evt.created_at,
        completed_at=evt.completed_at,
        is_simulated=evt.is_simulated,
    )


def _payment_attempts_from_events(
    events: Iterable[BaselinePaymentEvent],
) -> list[PaymentAttempt]:
    return [_payment_attempt_from_event(evt) for evt in events]


def _chunks(values: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _existing_payment_attempt_ids(db: Session, ids: Sequence[str]) -> set[str]:
    """Return existing payment-attempt IDs without exceeding SQLite bind limits."""
    existing: set[str] = set()
    for chunk in _chunks(ids, PAYMENT_ATTEMPT_ID_CHUNK_SIZE):
        if not chunk:
            continue
        existing.update(
            db.scalars(select(PaymentAttempt.id).where(PaymentAttempt.id.in_(chunk))).all()
        )
    return existing


def _cleanup_task21b_revision_markers(db: Session, merchant_id: str) -> None:
    """Delete only this merchant's Task 21B internal revision markers.

    Task 21B hashes the merchant scope before embedding it in generic operation
    ledger keys. Reproducing that deterministic prefix here keeps destructive
    demo reset tenant-scoped without importing product services into the seed
    script or deleting another merchant's append/detection history.
    """
    scope = hashlib.sha256(merchant_id.encode("utf-8")).hexdigest()
    for marker in ("data_append", "detection_pass"):
        prefix = f"merchant:{scope}:{marker}:"
        db.query(OperationExecution).filter(
            OperationExecution.operation_key.like(prefix + "%")
        ).delete(synchronize_session=False)


def cleanup_merchant_data(db: Session, merchant_id: str = "merchant_techbazaar") -> None:
    """Safely delete all existing demo records for the merchant in FK-safe order.

    This is intentionally destructive and is used only by ``seed_demo`` /
    ``reset_demo``. Production bootstrap never calls it. Task 21B revision and
    detector-pass markers are deleted too so the replacement baseline starts as
    a genuinely unconsumed historical revision.
    """
    db.query(PaymentAttempt).filter(PaymentAttempt.merchant_id == merchant_id).delete(
        synchronize_session=False
    )
    db.query(PolicyDecision).filter(PolicyDecision.merchant_id == merchant_id).delete(
        synchronize_session=False
    )
    db.query(AuditEvent).filter(AuditEvent.merchant_id == merchant_id).delete(
        synchronize_session=False
    )

    exp_ids = db.scalars(select(Experiment.id).where(Experiment.merchant_id == merchant_id)).all()
    if exp_ids:
        db.query(ExperimentResult).filter(ExperimentResult.experiment_id.in_(exp_ids)).delete(
            synchronize_session=False
        )
        db.query(ExperimentAssignment).filter(
            ExperimentAssignment.experiment_id.in_(exp_ids)
        ).delete(synchronize_session=False)
        db.query(RazorpayResource).filter(RazorpayResource.experiment_id.in_(exp_ids)).delete(
            synchronize_session=False
        )
        for exp_id in exp_ids:
            db.query(OperationExecution).filter(
                OperationExecution.operation_key.like(f"experiment:{exp_id}:%")
            ).delete(synchronize_session=False)

    _cleanup_task21b_revision_markers(db, merchant_id)

    db.query(Experiment).filter(Experiment.merchant_id == merchant_id).delete(
        synchronize_session=False
    )
    db.query(Hypothesis).filter(Hypothesis.merchant_id == merchant_id).delete(
        synchronize_session=False
    )
    db.query(Opportunity).filter(Opportunity.merchant_id == merchant_id).delete(
        synchronize_session=False
    )
    db.query(MerchantPolicy).filter(MerchantPolicy.merchant_id == merchant_id).delete(
        synchronize_session=False
    )
    db.query(Merchant).filter(Merchant.id == merchant_id).delete(synchronize_session=False)
    db.commit()


def _summarize_events(events: Sequence[BaselinePaymentEvent]) -> dict:
    total_attempts = len(events)
    captured_count = sum(1 for event in events if event.status == "captured")
    failed_count = sum(1 for event in events if event.status == "failed")
    abandoned_count = sum(1 for event in events if event.status == "abandoned")
    overall_conversion = captured_count / total_attempts if total_attempts > 0 else 0.0

    segment_stats = {}
    for segment in TECHBAZAAR_PROFILE.segments:
        segment_events = [event for event in events if event.segment == segment.name]
        segment_total = len(segment_events)
        segment_captured = sum(1 for event in segment_events if event.status == "captured")
        segment_rate = segment_captured / segment_total if segment_total > 0 else 0.0
        segment_stats[segment.name] = {
            "total": segment_total,
            "captured": segment_captured,
            "rate": segment_rate,
        }

    return {
        "merchant_name": TECHBAZAAR_PROFILE.name,
        "merchant_id": TECHBAZAAR_PROFILE.merchant_id,
        "total_attempts": total_attempts,
        "captured": captured_count,
        "failed": failed_count,
        "abandoned": abandoned_count,
        "overall_conversion": overall_conversion,
        "segments": segment_stats,
    }


def _generate_events(seed: int, days: int) -> list[BaselinePaymentEvent]:
    return generate_baseline_events(
        profile=TECHBAZAAR_PROFILE,
        seed=seed,
        days=days,
    )


def ensure_demo_baseline(
    db: Session | None = None, seed: int = DEFAULT_DEMO_SEED, days: int = DEFAULT_DEMO_DAYS
) -> dict:
    """Ensure canonical TechBazaar baseline rows exist without deleting state.

    The helper is idempotent. It creates missing tables, merchant, merchant
    policy and deterministic baseline payment attempts. Existing opportunities,
    hypotheses, experiments, audit events, Razorpay resource rows, operation
    ledger rows and experimental payment attempts are left untouched.
    """
    if db is None:
        _ensure_sqlite_parent_dir()
    _create_tables(db)

    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    events = _generate_events(seed=seed, days=days)
    event_ids = [event.id for event in events]

    try:
        merchant = db.get(Merchant, TECHBAZAAR_PROFILE.merchant_id)
        merchant_created = merchant is None
        if merchant is None:
            db.add(_demo_merchant())

        policy = db.scalar(
            select(MerchantPolicy).where(
                MerchantPolicy.merchant_id == TECHBAZAAR_PROFILE.merchant_id
            )
        )
        policy_created = policy is None
        if policy is None:
            db.add(_demo_policy())

        existing_attempt_ids = _existing_payment_attempt_ids(db, event_ids)
        missing_events = [event for event in events if event.id not in existing_attempt_ids]
        if missing_events:
            db.add_all(_payment_attempts_from_events(missing_events))

        db.commit()

        summary = _summarize_events(events)
        summary.update(
            {
                "merchant_created": merchant_created,
                "policy_created": policy_created,
                "baseline_attempts_total": len(events),
                "baseline_attempts_existing": len(events) - len(missing_events),
                "baseline_attempts_inserted": len(missing_events),
            }
        )
        return summary
    except Exception:
        db.rollback()
        raise
    finally:
        if should_close:
            db.close()


def seed_demo(
    db: Session | None = None, seed: int = DEFAULT_DEMO_SEED, days: int = DEFAULT_DEMO_DAYS
) -> dict:
    """Reset and seed TechBazaar merchant, policy, and baseline events."""
    if db is None:
        _ensure_sqlite_parent_dir()
    _create_tables(db)

    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    try:
        # Step 1: Idempotent destructive cleanup of existing merchant data.
        cleanup_merchant_data(db, merchant_id=TECHBAZAAR_PROFILE.merchant_id)

        # Step 2: Create Merchant and MerchantPolicy records.
        db.add(_demo_merchant())
        db.add(_demo_policy())

        # Step 3: Generate and insert deterministic baseline payment events.
        events = _generate_events(seed=seed, days=days)
        db.add_all(_payment_attempts_from_events(events))
        db.commit()

        # Step 4: Compute summary stats from the canonical event set.
        return _summarize_events(events)
    except Exception:
        db.rollback()
        raise
    finally:
        if should_close:
            db.close()


def print_demo_summary(summary: dict) -> None:
    print(f"Merchant: {summary['merchant_name']}")
    print(f"Merchant ID: {summary['merchant_id']}")
    print(f"Payment attempts: {summary['total_attempts']}")
    print(f"Captured: {summary['captured']}")
    print(f"Failed: {summary['failed']}")
    print(f"Abandoned: {summary['abandoned']}")
    print(f"Baseline conversion: {summary['overall_conversion']:.1%}")
    print("Segments:")
    for seg_name, stats in summary["segments"].items():
        print(f"  {seg_name}: {stats['captured']}/{stats['total']} ({stats['rate']:.1%})")


def main() -> None:
    summary = seed_demo()

    print("DEMO SEED: PASS")
    print_demo_summary(summary)


if __name__ == "__main__":
    main()
