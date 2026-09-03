"""Regression tests for judge-visible merchant data and lifecycle boundaries."""

from datetime import timezone

from app.db.models import PaymentAttempt
from app.engines.metrics import (
    get_amount_bucket_metrics,
    get_failure_reason_counts,
    get_overall_metrics,
    get_payment_method_metrics,
    get_segment_metrics,
)
from app.services.autopilot import gmv_totals
from app.simulation.runner import RUNTIME_ANCHOR, run_experiment_batch
from tests.test_experiment_runtime import db_session, make_experiment


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def test_experiment_runtime_rows_never_change_historical_merchant_metrics(db_session):
    experiment = make_experiment(
        db_session,
        status="approved",
        segment="android_mid",
        traffic_split=0.50,
        min_sample=50,
    )
    merchant_id = experiment.merchant_id

    db_session.add_all(
        [
            PaymentAttempt(
                id="historical_captured",
                merchant_id=merchant_id,
                customer_ref="historical_customer_1",
                amount=100_000,
                currency="INR",
                payment_method="upi",
                status="captured",
                segment="android_mid",
                experiment_id=None,
                variant=None,
                is_simulated=False,
            ),
            PaymentAttempt(
                id="historical_failed",
                merchant_id=merchant_id,
                customer_ref="historical_customer_2",
                amount=200_000,
                currency="INR",
                payment_method="card",
                status="failed",
                failure_reason="bank_declined",
                segment="android_mid",
                experiment_id=None,
                variant=None,
                is_simulated=False,
            ),
        ]
    )
    db_session.flush()

    run_experiment_batch(db_session, experiment.id, batch_size=40)
    experimental_count = (
        db_session.query(PaymentAttempt)
        .filter(PaymentAttempt.experiment_id == experiment.id)
        .count()
    )
    assert experimental_count > 0

    overall = get_overall_metrics(db_session, merchant_id)
    assert overall.attempts == 2
    assert overall.captured == 1
    assert overall.failed == 1
    assert overall.conversion_rate == 0.5

    segments = get_segment_metrics(db_session, merchant_id)
    assert len(segments) == 1
    assert segments[0].segment == "android_mid"
    assert segments[0].attempts == 2
    assert segments[0].captured_gmv_paise == 100_000
    assert segments[0].gmv_paise == 300_000

    methods = get_payment_method_metrics(db_session, merchant_id)
    assert sum(row.attempts for row in methods) == 2
    assert {row.payment_method: row.attempts for row in methods} == {"upi": 1, "card": 1}

    failure_counts = get_failure_reason_counts(db_session, merchant_id)
    assert failure_counts == {"bank_declined": 1}

    buckets = get_amount_bucket_metrics(db_session, merchant_id)
    assert sum(row.attempts for row in buckets) == 2

    attempted_gmv, captured_gmv = gmv_totals(db_session, merchant_id)
    assert attempted_gmv == 300_000
    assert captured_gmv == 100_000


def test_simulator_clock_does_not_backdate_experiment_lifecycle(db_session):
    experiment = make_experiment(
        db_session,
        status="approved",
        segment="android_mid",
        traffic_split=0.50,
        min_sample=50,
    )
    created_at = _as_utc(experiment.created_at)

    run_experiment_batch(db_session, experiment.id, batch_size=20)

    assert experiment.started_at is not None
    started_at = _as_utc(experiment.started_at)
    assert started_at >= created_at
    assert started_at != RUNTIME_ANCHOR

    experimental_rows = (
        db_session.query(PaymentAttempt)
        .filter(PaymentAttempt.experiment_id == experiment.id)
        .all()
    )
    assert experimental_rows
    assert all(_as_utc(row.created_at).date() == RUNTIME_ANCHOR.date() for row in experimental_rows)
