"""Tests for Task 05: Demo merchant and deterministic baseline generator."""

from datetime import datetime, timezone, timedelta
from pathlib import Path
import sys
import pytest
from sqlalchemy import create_engine, select, func, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Merchant, MerchantPolicy, PaymentAttempt
from app.simulation.merchant import TECHBAZAAR_PROFILE, MerchantProfile, SegmentProfile
from app.simulation.generator import generate_baseline_events, BaselinePaymentEvent

# Ensure scripts directory is importable
scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

from seed_demo import seed_demo
from reset_demo import reset_demo


@pytest.fixture
def temp_db_session(tmp_path):
    """Provide an isolated temporary SQLite database session for testing."""
    db_file = tmp_path / "test_autopilot.db"
    db_url = f"sqlite:///{db_file}"
    test_engine = create_engine(db_url, connect_args={"check_same_thread": False})

    @event.listens_for(test_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=test_engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        test_engine.dispose()


def test_1_same_seed_generates_identical_events():
    events1 = generate_baseline_events(seed=20260827)
    events2 = generate_baseline_events(seed=20260827)
    assert events1 == events2


def test_2_different_seed_produces_different_dataset():
    events1 = generate_baseline_events(seed=20260827)
    events2 = generate_baseline_events(seed=999999)
    assert events1 != events2


def test_3_exactly_five_expected_segments():
    events = generate_baseline_events(seed=20260827)
    segments = set(e.segment for e in events)
    expected = {"android_mid", "android_budget", "web_general", "repeat_buyer", "ios_premium"}
    assert segments == expected


def test_4_traffic_proportions_approximately_correct():
    events = generate_baseline_events(seed=20260827)
    total = len(events)
    counts = {}
    for e in events:
        counts[e.segment] = counts.get(e.segment, 0) + 1

    targets = {
        "android_budget": 0.35,
        "android_mid": 0.25,
        "web_general": 0.15,
        "ios_premium": 0.15,
        "repeat_buyer": 0.10,
    }

    for seg_name, target_prop in targets.items():
        actual_prop = counts[seg_name] / total
        assert abs(actual_prop - target_prop) < 0.05, f"Segment {seg_name} prop {actual_prop} far from target {target_prop}"


def test_5_amounts_within_configured_segment_ranges():
    events = generate_baseline_events(seed=20260827)
    ranges = {
        "android_budget": (50_000, 150_000),
        "android_mid": (100_000, 350_000),
        "web_general": (100_000, 800_000),
        "repeat_buyer": (200_000, 1_200_000),
        "ios_premium": (500_000, 2_500_000),
    }

    for e in events:
        min_p, max_p = ranges[e.segment]
        assert min_p <= e.amount <= max_p, f"Amount {e.amount} out of range for {e.segment}"


def test_6_currency_is_always_inr():
    events = generate_baseline_events(seed=20260827)
    assert all(e.currency == "INR" for e in events)


def test_7_statuses_only_valid_values():
    events = generate_baseline_events(seed=20260827)
    valid_statuses = {"captured", "failed", "abandoned"}
    assert set(e.status for e in events).issubset(valid_statuses)


def test_8_failure_reason_only_for_failed_events():
    events = generate_baseline_events(seed=20260827)
    allowed_reasons = {
        "authentication_failed",
        "bank_declined",
        "insufficient_funds",
        "network_error",
        "payment_timeout",
        "unknown",
    }

    for e in events:
        if e.status == "failed":
            assert e.failure_reason in allowed_reasons, f"Unexpected failure reason: {e.failure_reason}"
        else:
            assert e.failure_reason is None, f"Non-failed event had failure reason: {e.failure_reason}"


def test_9_payment_methods_only_valid_values():
    events = generate_baseline_events(seed=20260827)
    valid_methods = {"upi", "card", "netbanking", "wallet"}
    assert set(e.payment_method for e in events).issubset(valid_methods)


def test_10_device_mappings_are_valid():
    events = generate_baseline_events(seed=20260827)
    for e in events:
        if e.segment in ("android_mid", "android_budget"):
            assert e.device_type == "android"
        elif e.segment == "ios_premium":
            assert e.device_type == "ios"
        elif e.segment == "web_general":
            assert e.device_type == "web"
        elif e.segment == "repeat_buyer":
            assert e.device_type in ("android", "ios", "web")


def test_11_timestamps_are_timezone_aware():
    events = generate_baseline_events(seed=20260827)
    for e in events:
        assert e.created_at.tzinfo is not None
        if e.completed_at is not None:
            assert e.completed_at.tzinfo is not None


def test_12_timestamps_span_intended_30_day_window():
    events = generate_baseline_events(seed=20260827)
    created_times = [e.created_at for e in events]
    min_time = min(created_times)
    max_time = max(created_times)

    anchor_dt = datetime.fromisoformat(TECHBAZAAR_PROFILE.anchor_timestamp_iso)
    if anchor_dt.tzinfo is None:
        anchor_dt = anchor_dt.replace(tzinfo=timezone.utc)

    start_date = anchor_dt.date() - timedelta(days=29)
    start_dt = datetime(start_date.year, start_date.month, start_date.day, 0, 0, 0, tzinfo=timezone.utc)

    assert min_time >= start_dt
    assert max_time <= anchor_dt
    time_span = max_time - min_time
    assert time_span.days >= 28


def test_13_event_ids_are_unique():
    events = generate_baseline_events(seed=20260827)
    ids = [e.id for e in events]
    assert len(ids) == len(set(ids))


def test_14_customer_refs_are_synthetic():
    events = generate_baseline_events(seed=20260827)
    for e in events:
        assert e.customer_ref is not None
        assert e.customer_ref.startswith("cust_")


def test_15_repeat_buyer_contains_reused_customer_refs():
    events = generate_baseline_events(seed=20260827)
    repeat_buyer_refs = [e.customer_ref for e in events if e.segment == "repeat_buyer"]
    unique_refs = set(repeat_buyer_refs)
    assert len(repeat_buyer_refs) > len(unique_refs), "repeat_buyer should contain reused customer refs"


def test_16_no_event_contains_intervention_causal_metadata():
    events = generate_baseline_events(seed=20260827)
    event_dict = events[0].__dict__
    forbidden_terms = [
        "experiment_id",
        "variant",
        "causal",
        "intervention",
        "uplift",
        "treatment",
        "best_intervention",
    ]
    for key in event_dict.keys():
        for term in forbidden_terms:
            assert term not in key, f"Forbidden term {term} found in event field {key}"


def test_17_generated_captured_rates_reasonably_close_to_targets():
    events = generate_baseline_events(seed=20260827)
    target_rates = {
        "android_mid": 0.51,
        "android_budget": 0.45,
        "web_general": 0.48,
        "repeat_buyer": 0.65,
        "ios_premium": 0.72,
    }

    for seg_name, target in target_rates.items():
        seg_events = [e for e in events if e.segment == seg_name]
        captured = sum(1 for e in seg_events if e.status == "captured")
        actual_rate = captured / len(seg_events)
        assert abs(actual_rate - target) < 0.08, f"Segment {seg_name} conversion {actual_rate:.3f} far from target {target}"


def test_18_total_event_volume_around_6000():
    events = generate_baseline_events(seed=20260827)
    assert 5500 <= len(events) <= 6500


def test_19_seed_demo_writes_to_temporary_db(temp_db_session):
    summary = seed_demo(db=temp_db_session, seed=20260827)
    assert summary["merchant_id"] == "merchant_techbazaar"

    merchant = temp_db_session.scalar(select(Merchant).where(Merchant.id == "merchant_techbazaar"))
    assert merchant is not None
    assert merchant.name == "TechBazaar Electronics"

    policy = temp_db_session.scalar(select(MerchantPolicy).where(MerchantPolicy.merchant_id == "merchant_techbazaar"))
    assert policy is not None
    assert policy.max_experiment_exposure_pct == 0.10

    attempts_count = temp_db_session.scalar(
        select(func.count()).select_from(PaymentAttempt).where(PaymentAttempt.merchant_id == "merchant_techbazaar")
    )
    assert 5500 <= attempts_count <= 6500


def test_20_running_seed_logic_twice_does_not_double_payment_attempts(temp_db_session):
    seed_demo(db=temp_db_session, seed=20260827)
    count1 = temp_db_session.scalar(
        select(func.count()).select_from(PaymentAttempt).where(PaymentAttempt.merchant_id == "merchant_techbazaar")
    )

    seed_demo(db=temp_db_session, seed=20260827)
    count2 = temp_db_session.scalar(
        select(func.count()).select_from(PaymentAttempt).where(PaymentAttempt.merchant_id == "merchant_techbazaar")
    )

    assert count1 == count2


def test_21_reset_produces_exact_deterministic_baseline_again(temp_db_session):
    summary1 = seed_demo(db=temp_db_session, seed=20260827)
    summary2 = reset_demo(db=temp_db_session, seed=20260827)

    assert summary1["total_attempts"] == summary2["total_attempts"]
    assert summary1["captured"] == summary2["captured"]
    assert summary1["failed"] == summary2["failed"]
    assert summary1["abandoned"] == summary2["abandoned"]
    assert summary1["overall_conversion"] == summary2["overall_conversion"]
