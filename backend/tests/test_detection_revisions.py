"""Task 21B detector-revision and evidence-boundary regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import (
    Experiment,
    Hypothesis,
    Merchant,
    OperationExecution,
    Opportunity,
    PaymentAttempt,
)
from app.engines.opportunities import (
    detect_segment_conversion_opportunities,
    run_opportunity_detection,
)
from app.services.incremental_data import (
    DETECTION_PASS_OPERATION_TYPE,
    detection_readiness,
)


@pytest.fixture
def db_session(tmp_path):
    db_file = tmp_path / "test_detection_revisions.db"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _merchant(db, merchant_id: str = "merchant_revision") -> Merchant:
    row = Merchant(id=merchant_id, name="Revision Merchant")
    db.add(row)
    db.flush()
    return row


def _historical_attempts(
    db,
    *,
    merchant_id: str,
    segment: str,
    total: int,
    captured: int,
    start: datetime,
) -> None:
    for index in range(total):
        status = "captured" if index < captured else "failed"
        db.add(
            PaymentAttempt(
                id=f"hist_{segment}_{index:04d}",
                merchant_id=merchant_id,
                customer_ref=f"hist_customer_{segment}_{index:04d}",
                amount=10000,
                currency="INR",
                payment_method="upi",
                status=status,
                failure_reason=None if status == "captured" else "bank_declined",
                segment=segment,
                source="merchant_csv",
                experiment_id=None,
                created_at=start + timedelta(seconds=index),
                is_simulated=False,
            )
        )
    db.flush()


def test_zero_opportunity_revision_is_consumed_once(db_session):
    merchant = _merchant(db_session)
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    _historical_attempts(
        db_session,
        merchant_id=merchant.id,
        segment="segment_a",
        total=100,
        captured=50,
        start=start,
    )
    _historical_attempts(
        db_session,
        merchant_id=merchant.id,
        segment="segment_b",
        total=100,
        captured=50,
        start=start + timedelta(hours=1),
    )

    assert detection_readiness(db_session, merchant.id).ready is True

    first = run_opportunity_detection(
        db_session,
        merchant.id,
        min_segment_attempts=50,
        min_absolute_gap=0.08,
    )
    second = run_opportunity_detection(
        db_session,
        merchant.id,
        min_segment_attempts=50,
        min_absolute_gap=0.08,
    )

    assert first == []
    assert second == []
    assert detection_readiness(db_session, merchant.id).ready is False
    assert (
        db_session.query(OperationExecution)
        .filter(OperationExecution.operation_type == DETECTION_PASS_OPERATION_TYPE)
        .count()
        == 1
    )

    # A genuinely new historical observation changes the revision token and
    # unlocks exactly one future pass. This also covers ingestion paths outside
    # the dashboard service while preserving append-only semantics.
    db_session.add(
        PaymentAttempt(
            id="hist_new_revision",
            merchant_id=merchant.id,
            customer_ref="new_customer",
            amount=10000,
            currency="INR",
            payment_method="upi",
            status="captured",
            segment="segment_a",
            source="merchant_csv",
            experiment_id=None,
            created_at=start + timedelta(days=1),
            is_simulated=False,
        )
    )
    db_session.flush()
    readiness = detection_readiness(db_session, merchant.id)
    assert readiness.ready is True
    assert readiness.reason == "NEW_DATA"


def test_experiment_rows_do_not_change_historical_opportunity_evidence(db_session):
    merchant = _merchant(db_session, "merchant_isolation")
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    _historical_attempts(
        db_session,
        merchant_id=merchant.id,
        segment="android_budget",
        total=100,
        captured=30,
        start=start,
    )
    _historical_attempts(
        db_session,
        merchant_id=merchant.id,
        segment="ios_premium",
        total=100,
        captured=80,
        start=start + timedelta(hours=1),
    )

    opportunity = Opportunity(
        id="experiment_source_opp",
        merchant_id=merchant.id,
        type="segment_conversion_divergence",
        segment="android_budget",
        severity=0.2,
        detected_metric="conversion_rate",
        detected_value=0.3,
        baseline_value=0.8,
        evidence={},
        status="resolved",
    )
    db_session.add(opportunity)
    db_session.flush()
    hypothesis = Hypothesis(
        id="experiment_source_hyp",
        opportunity_id=opportunity.id,
        merchant_id=merchant.id,
        hypothesis_text="test isolation",
        intervention_type="payment_method_config",
        intervention_params={},
        evidence_refs=[],
        status="proposed",
    )
    db_session.add(hypothesis)
    db_session.flush()
    experiment = Experiment(
        id="experiment_isolation",
        merchant_id=merchant.id,
        hypothesis_id=hypothesis.id,
        opportunity_id=opportunity.id,
        name="isolation experiment",
        segment="android_budget",
        intervention_type="payment_method_config",
        control_config={},
        treatment_config={"payment_methods": {"upi": True}},
        traffic_split_treatment_pct=0.1,
        primary_metric="conversion_rate",
        guardrail_metrics=[],
        min_sample_per_variant=10,
        max_duration_hours=24,
        status="running",
    )
    db_session.add(experiment)
    db_session.flush()

    # If these rows leaked into historical evidence, android_budget would look
    # dramatically stronger than its actual 30% historical conversion rate.
    for index in range(300):
        db_session.add(
            PaymentAttempt(
                id=f"exp_attempt_{index:04d}",
                merchant_id=merchant.id,
                customer_ref=f"exp_customer_{index:04d}",
                amount=10000,
                currency="INR",
                payment_method="upi",
                status="captured",
                segment="android_budget",
                source="experiment_runtime",
                experiment_id=experiment.id,
                variant="treatment",
                created_at=start + timedelta(days=2, seconds=index),
                is_simulated=True,
            )
        )
    db_session.flush()

    detected = detect_segment_conversion_opportunities(
        db_session,
        merchant.id,
        min_segment_attempts=50,
        min_absolute_gap=0.08,
        max_results=5,
    )

    android = next(row for row in detected if row.segment == "android_budget")
    assert android.segment_attempts == 100
    assert android.segment_conversion_rate == pytest.approx(0.30)
    assert android.comparison_attempts == 100
    assert android.comparison_conversion_rate == pytest.approx(0.80)
    assert android.evidence["payment_method_metrics"]["upi"]["attempts"] == 100
