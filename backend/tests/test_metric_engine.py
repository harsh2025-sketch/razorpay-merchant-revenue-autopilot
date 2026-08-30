"""Tests for Task 07: metric engine."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Merchant, PaymentAttempt
from app.engines.metrics import (
    get_overall_metrics,
    get_segment_metrics,
    get_payment_method_metrics,
    get_failure_reason_counts,
    get_amount_bucket_metrics,
    BUCKET_DEFINITIONS,
)


@pytest.fixture
def temp_db_session(tmp_path):
    db_file = tmp_path / "test_metrics.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})

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


def _create_merchant(db, merchant_id="merchant_test"):
    m = Merchant(id=merchant_id, name="Test Merchant")
    db.add(m)
    db.flush()
    return m


def _add_attempt(
    db,
    merchant_id,
    amount=100000,
    status="captured",
    segment="android_mid",
    payment_method="upi",
    failure_reason=None,
    customer_ref="cust_000001",
):
    pa = PaymentAttempt(
        merchant_id=merchant_id,
        amount=amount,
        status=status,
        segment=segment,
        payment_method=payment_method,
        failure_reason=failure_reason,
        customer_ref=customer_ref,
        currency="INR",
    )
    db.add(pa)
    db.flush()
    return pa


# ---------------------------------------------------------------------------
# Overall metrics
# ---------------------------------------------------------------------------

def test_overall_attempts_captured_failed_abandoned(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempt(db, "m1", status="captured", amount=100000)
    _add_attempt(db, "m1", status="captured", amount=200000)
    _add_attempt(db, "m1", status="failed", amount=150000, failure_reason="bank_declined")
    _add_attempt(db, "m1", status="abandoned", amount=120000)
    db.commit()

    metrics = get_overall_metrics(db, "m1")
    assert metrics.attempts == 4
    assert metrics.captured == 2
    assert metrics.failed == 1
    assert metrics.abandoned == 1


def test_conversion_rate_correct(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempt(db, "m1", status="captured")
    _add_attempt(db, "m1", status="captured")
    _add_attempt(db, "m1", status="failed", failure_reason="bank_declined")
    _add_attempt(db, "m1", status="failed", failure_reason="network_error")
    db.commit()

    metrics = get_overall_metrics(db, "m1")
    assert metrics.conversion_rate == pytest.approx(0.5)


def test_abandoned_included_in_denominator(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempt(db, "m1", status="captured")
    _add_attempt(db, "m1", status="abandoned")
    _add_attempt(db, "m1", status="abandoned")
    db.commit()

    metrics = get_overall_metrics(db, "m1")
    # 1 captured out of 3 total
    assert metrics.attempts == 3
    assert metrics.conversion_rate == pytest.approx(1 / 3)


def test_empty_merchant_returns_none_conversion(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "empty_m")
    db.commit()

    metrics = get_overall_metrics(db, "empty_m")
    assert metrics.attempts == 0
    assert metrics.captured == 0
    assert metrics.failed == 0
    assert metrics.abandoned == 0
    assert metrics.conversion_rate is None

    seg_metrics = get_segment_metrics(db, "empty_m")
    assert seg_metrics == []

    pm_metrics = get_payment_method_metrics(db, "empty_m")
    assert pm_metrics == []

    failure_counts = get_failure_reason_counts(db, "empty_m")
    assert failure_counts == {}

    bucket_metrics = get_amount_bucket_metrics(db, "empty_m")
    # Either [] or valid bucket structures with zero attempts
    # We choose valid bucket structures with zero attempts
    if len(bucket_metrics) == 0:
        assert bucket_metrics == []
    else:
        assert len(bucket_metrics) == len(BUCKET_DEFINITIONS)
        for bm in bucket_metrics:
            assert bm.attempts == 0
            assert bm.captured == 0
            assert bm.conversion_rate is None


# ---------------------------------------------------------------------------
# Segment metrics
# ---------------------------------------------------------------------------

def test_segment_metrics_grouped_correctly(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    # segment a: 2 captured, 1 failed
    _add_attempt(db, "m1", status="captured", segment="android_mid", amount=100000)
    _add_attempt(db, "m1", status="captured", segment="android_mid", amount=200000)
    _add_attempt(db, "m1", status="failed", segment="android_mid", amount=150000, failure_reason="bank_declined")
    # segment b: 1 captured
    _add_attempt(db, "m1", status="captured", segment="ios_premium", amount=500000)
    db.commit()

    seg_metrics = get_segment_metrics(db, "m1")
    assert len(seg_metrics) == 2
    # Find android_mid
    android = next(s for s in seg_metrics if s.segment == "android_mid")
    assert android.attempts == 3
    assert android.captured == 2
    assert android.failed == 1
    assert android.abandoned == 0
    assert android.conversion_rate == pytest.approx(2 / 3)

    ios = next(s for s in seg_metrics if s.segment == "ios_premium")
    assert ios.attempts == 1
    assert ios.captured == 1


def test_segment_ordering_deterministic(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempt(db, "m1", segment="web_general", status="captured")
    _add_attempt(db, "m1", segment="android_budget", status="captured")
    _add_attempt(db, "m1", segment="android_mid", status="captured")
    _add_attempt(db, "m1", segment="ios_premium", status="captured")
    _add_attempt(db, "m1", segment="repeat_buyer", status="captured")
    db.commit()

    seg_metrics = get_segment_metrics(db, "m1")
    segments = [s.segment for s in seg_metrics]
    assert segments == sorted(segments)


def test_captured_gmv_correct(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempt(db, "m1", segment="android_mid", status="captured", amount=100000)
    _add_attempt(db, "m1", segment="android_mid", status="captured", amount=200000)
    _add_attempt(db, "m1", segment="android_mid", status="failed", amount=500000, failure_reason="bank_declined")
    db.commit()

    seg_metrics = get_segment_metrics(db, "m1")
    android = next(s for s in seg_metrics if s.segment == "android_mid")
    assert android.captured_gmv_paise == 300000
    assert android.gmv_paise == 800000


def test_attempted_gmv_correct(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempt(db, "m1", segment="android_mid", status="captured", amount=100000)
    _add_attempt(db, "m1", segment="android_mid", status="failed", amount=200000, failure_reason="bank_declined")
    _add_attempt(db, "m1", segment="android_mid", status="abandoned", amount=300000)
    db.commit()

    seg_metrics = get_segment_metrics(db, "m1")
    android = seg_metrics[0]
    assert android.gmv_paise == 600000
    assert android.captured_gmv_paise == 100000


def test_aov_based_only_on_captured(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempt(db, "m1", segment="android_mid", status="captured", amount=100000)
    _add_attempt(db, "m1", segment="android_mid", status="captured", amount=300000)
    _add_attempt(db, "m1", segment="android_mid", status="failed", amount=1000000, failure_reason="bank_declined")
    db.commit()

    seg_metrics = get_segment_metrics(db, "m1")
    android = seg_metrics[0]
    # AOV = (100k+300k)/2 = 200k
    assert android.average_captured_order_value_paise == pytest.approx(200000.0)


def test_zero_capture_aov_returns_none(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempt(db, "m1", segment="android_mid", status="failed", amount=100000, failure_reason="bank_declined")
    _add_attempt(db, "m1", segment="android_mid", status="abandoned", amount=200000)
    db.commit()

    seg_metrics = get_segment_metrics(db, "m1")
    android = seg_metrics[0]
    assert android.captured == 0
    assert android.average_captured_order_value_paise is None


# ---------------------------------------------------------------------------
# Payment method metrics
# ---------------------------------------------------------------------------

def test_payment_method_success_rates_correct(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    # upi: 2 captured, 1 failed => 2/3
    _add_attempt(db, "m1", payment_method="upi", status="captured")
    _add_attempt(db, "m1", payment_method="upi", status="captured")
    _add_attempt(db, "m1", payment_method="upi", status="failed", failure_reason="bank_declined")
    # card: 1 captured, 1 failed => 0.5
    _add_attempt(db, "m1", payment_method="card", status="captured")
    _add_attempt(db, "m1", payment_method="card", status="failed", failure_reason="bank_declined")
    db.commit()

    pm_metrics = get_payment_method_metrics(db, "m1")
    upi = next(m for m in pm_metrics if m.payment_method == "upi")
    assert upi.attempts == 3
    assert upi.captured == 2
    assert upi.success_rate == pytest.approx(2 / 3)

    card = next(m for m in pm_metrics if m.payment_method == "card")
    assert card.success_rate == pytest.approx(0.5)


def test_segment_filter_for_payment_method_metrics(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempt(db, "m1", segment="android_mid", payment_method="upi", status="captured")
    _add_attempt(db, "m1", segment="android_mid", payment_method="card", status="failed", failure_reason="bank_declined")
    _add_attempt(db, "m1", segment="ios_premium", payment_method="upi", status="captured")
    _add_attempt(db, "m1", segment="ios_premium", payment_method="upi", status="captured")
    db.commit()

    all_pm = get_payment_method_metrics(db, "m1")
    assert len(all_pm) == 2  # upi and card

    android_pm = get_payment_method_metrics(db, "m1", segment="android_mid")
    # Only android_mid
    assert len(android_pm) == 2
    upi_android = next(m for m in android_pm if m.payment_method == "upi")
    assert upi_android.attempts == 1

    ios_pm = get_payment_method_metrics(db, "m1", segment="ios_premium")
    assert len(ios_pm) == 1
    assert ios_pm[0].payment_method == "upi"
    assert ios_pm[0].attempts == 2


def test_payment_method_ordering_deterministic(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    # Add in random order
    _add_attempt(db, "m1", payment_method="wallet", status="captured")
    _add_attempt(db, "m1", payment_method="netbanking", status="captured")
    _add_attempt(db, "m1", payment_method="card", status="captured")
    _add_attempt(db, "m1", payment_method="upi", status="captured")
    _add_attempt(db, "m1", payment_method="unknown_method", status="captured")
    db.commit()

    pm_metrics = get_payment_method_metrics(db, "m1")
    order = [m.payment_method for m in pm_metrics]
    # Expected: upi, card, netbanking, wallet, then unknown sorted
    assert order == ["upi", "card", "netbanking", "wallet", "unknown_method"]


# ---------------------------------------------------------------------------
# Failure reason counts
# ---------------------------------------------------------------------------

def test_failure_reason_counts_include_only_failed(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempt(db, "m1", status="failed", failure_reason="bank_declined")
    _add_attempt(db, "m1", status="failed", failure_reason="bank_declined")
    _add_attempt(db, "m1", status="failed", failure_reason="network_error")
    _add_attempt(db, "m1", status="captured", failure_reason=None)
    _add_attempt(db, "m1", status="abandoned", failure_reason=None)
    db.commit()

    counts = get_failure_reason_counts(db, "m1")
    assert counts == {"bank_declined": 2, "network_error": 1}


def test_captured_abandoned_failure_reasons_do_not_appear(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    # Even if we manually set failure_reason on captured (should not happen), it should not be counted
    # Our function filters status == failed, so captured/abandoned should not appear
    _add_attempt(db, "m1", status="captured", failure_reason="bank_declined")
    _add_attempt(db, "m1", status="abandoned", failure_reason="network_error")
    _add_attempt(db, "m1", status="failed", failure_reason="insufficient_funds")
    db.commit()

    counts = get_failure_reason_counts(db, "m1")
    assert counts == {"insufficient_funds": 1}
    assert "bank_declined" not in counts
    assert "network_error" not in counts


# ---------------------------------------------------------------------------
# Amount buckets
# ---------------------------------------------------------------------------

def test_amount_bucket_boundaries_correct(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    # Bucket 0: 0-100000 exclusive upper => 0, 50000, 99999 should be in first bucket
    _add_attempt(db, "m1", amount=0, status="captured")
    _add_attempt(db, "m1", amount=50000, status="captured")
    _add_attempt(db, "m1", amount=99999, status="failed", failure_reason="bank_declined")
    # Bucket 1: 100000-300000
    _add_attempt(db, "m1", amount=100000, status="captured")
    _add_attempt(db, "m1", amount=299999, status="captured")
    # Bucket 2: 300000-500000
    _add_attempt(db, "m1", amount=300000, status="captured")
    # Bucket 3: 500000-1000000
    _add_attempt(db, "m1", amount=500000, status="captured")
    _add_attempt(db, "m1", amount=999999, status="failed", failure_reason="bank_declined")
    # Bucket 4: 1000000+
    _add_attempt(db, "m1", amount=1000000, status="captured")
    _add_attempt(db, "m1", amount=2500000, status="captured")
    db.commit()

    buckets = get_amount_bucket_metrics(db, "m1")
    assert len(buckets) == 5
    # Check ordering ascending
    assert buckets[0].min_amount_paise == 0
    assert buckets[-1].max_amount_paise is None

    # Verify counts
    assert buckets[0].attempts == 3
    assert buckets[0].captured == 2
    assert buckets[1].attempts == 2
    assert buckets[1].captured == 2
    assert buckets[2].attempts == 1
    assert buckets[3].attempts == 2
    assert buckets[4].attempts == 2


def test_other_merchants_rows_do_not_leak(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _create_merchant(db, "m2")
    _add_attempt(db, "m1", status="captured", amount=100000)
    _add_attempt(db, "m1", status="failed", amount=100000, failure_reason="bank_declined")
    _add_attempt(db, "m2", status="captured", amount=999999)
    _add_attempt(db, "m2", status="captured", amount=999999)
    _add_attempt(db, "m2", status="captured", amount=999999)
    db.commit()

    m1_overall = get_overall_metrics(db, "m1")
    assert m1_overall.attempts == 2
    assert m1_overall.captured == 1

    m1_seg = get_segment_metrics(db, "m1")
    assert len(m1_seg) == 1
    assert m1_seg[0].attempts == 2

    m1_pm = get_payment_method_metrics(db, "m1")
    # All our adds used default upi, so 2 attempts for m1
    assert sum(pm.attempts for pm in m1_pm) == 2

    m1_failures = get_failure_reason_counts(db, "m1")
    assert m1_failures == {"bank_declined": 1}

    m1_buckets = get_amount_bucket_metrics(db, "m1")
    # m1 has 2 attempts in 100k bucket
    bucket_100k = next(b for b in m1_buckets if b.min_amount_paise == 100000)
    assert bucket_100k.attempts == 2

    m2_overall = get_overall_metrics(db, "m2")
    assert m2_overall.attempts == 3
    assert m2_overall.captured == 3
