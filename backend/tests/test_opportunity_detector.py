"""Tests for Task 07: opportunity detector."""

import ast
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Merchant, PaymentAttempt, Opportunity
from app.engines.opportunities import (
    detect_segment_conversion_opportunities,
    persist_detected_opportunities,
    run_opportunity_detection,
)


@pytest.fixture
def temp_db_session(tmp_path):
    db_file = tmp_path / "test_opp.db"
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


def _add_attempts(
    db,
    merchant_id,
    segment,
    captured,
    total,
    payment_method="upi",
    failure_reason="bank_declined",
    amount=100000,
):
    """Helper to add total attempts with captured successes and rest failed."""
    for i in range(total):
        status = "captured" if i < captured else "failed"
        fr = None if status == "captured" else failure_reason
        pa = PaymentAttempt(
            merchant_id=merchant_id,
            amount=amount,
            status=status,
            segment=segment,
            payment_method=payment_method,
            failure_reason=fr,
            customer_ref=f"cust_{segment}_{i}_{total}",
            currency="INR",
        )
        db.add(pa)
    db.flush()


def test_clearly_underperforming_segment_is_detected(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    # segment bad: 30% conversion, 200 attempts
    _add_attempts(db, "m1", "android_budget", captured=60, total=200)
    # segment good: 70% conversion, 200 attempts
    _add_attempts(db, "m1", "ios_premium", captured=140, total=200)
    db.commit()

    detected = detect_segment_conversion_opportunities(
        db, "m1", min_segment_attempts=100, min_absolute_gap=0.08, max_results=3
    )
    # Should detect android_budget as underperforming vs ios_premium complement
    segments = [d.segment for d in detected]
    assert "android_budget" in segments
    # Ensure detected conversion rates are correct
    det = next(d for d in detected if d.segment == "android_budget")
    assert det.segment_conversion_rate == pytest.approx(0.3)
    assert det.comparison_conversion_rate == pytest.approx(0.7)
    assert det.absolute_gap == pytest.approx(0.4)


def test_comparison_baseline_is_complement_not_overall_inclusive(temp_db_session):
    """Construct dataset where inclusive vs complement distinction is obvious."""
    db = temp_db_session
    _create_merchant(db, "m1")
    # Create 3 segments:
    # A: 50% conversion, 100 attempts (50 captured)
    # B: 50% conversion, 100 attempts
    # C: 0% conversion, 100 attempts (clearly underperforming)
    # Overall inclusive conversion = (50+50+0)/300 = 33.3%
    # If we compare C vs overall inclusive: gap = 33.3% - 0% = 33.3% -> would be detected
    # But we want complement comparison: C vs A+B = (50+50)/200 = 50% => gap 50%
    # To make distinction obvious, create case where overall inclusive would HIDE underperformance?
    # Actually better: make A high, B high, C medium but still below complement but above overall?
    # Let's do:
    # A: 80% conversion, 400 attempts (320 captured)
    # B: 0% conversion, 100 attempts
    # Overall inclusive = 320/500 = 64%
    # For B, inclusive gap = 64% - 0% = 64%
    # Complement gap = 80% - 0% = 80% -> both would detect, not distinguishing.
    # To prove complement, we check that comparison_rate is computed as non-target.
    _add_attempts(db, "m1", "android_mid", captured=80, total=100)  # 80%
    _add_attempts(db, "m1", "ios_premium", captured=80, total=100)  # 80%
    _add_attempts(db, "m1", "android_budget", captured=20, total=100)  # 20% -> underperforming
    db.commit()

    detected = detect_segment_conversion_opportunities(
        db, "m1", min_segment_attempts=50, min_absolute_gap=0.08, max_results=5
    )
    # Find android_budget
    det = next((d for d in detected if d.segment == "android_budget"), None)
    assert det is not None
    # Complement should be (80+80)/200 = 80%, not overall (180/300=60%)
    assert det.comparison_conversion_rate == pytest.approx(0.8)
    assert det.comparison_attempts == 200
    # Overall inclusive would be 60%, so if code used overall, comparison would be 60%
    assert det.comparison_conversion_rate != pytest.approx(0.6)


def test_high_performing_segment_is_not_detected(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempts(db, "m1", "android_budget", captured=30, total=100)  # 30%
    _add_attempts(db, "m1", "ios_premium", captured=90, total=100)  # 90% high performer
    db.commit()

    detected = detect_segment_conversion_opportunities(
        db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5
    )
    # ios_premium is high performing, should NOT be detected as opportunity
    segments = [d.segment for d in detected]
    assert "ios_premium" not in segments
    assert "android_budget" in segments


def test_segment_below_min_sample_skipped(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempts(db, "m1", "android_budget", captured=5, total=20)  # below threshold
    _add_attempts(db, "m1", "ios_premium", captured=80, total=100)
    db.commit()

    detected = detect_segment_conversion_opportunities(
        db, "m1", min_segment_attempts=100, min_absolute_gap=0.05, max_results=5
    )
    assert len(detected) == 0


def test_complement_below_min_sample_skipped(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    # Target segment has enough, but complement does not
    _add_attempts(db, "m1", "android_budget", captured=20, total=100)
    _add_attempts(db, "m1", "ios_premium", captured=5, total=10)  # complement only 10
    db.commit()

    detected = detect_segment_conversion_opportunities(
        db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5
    )
    # android_budget complement is only 10, so should be skipped
    # ios_premium itself is below min, also skipped
    assert len(detected) == 0


def test_gap_below_threshold_skipped(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempts(db, "m1", "android_budget", captured=45, total=100)  # 45%
    _add_attempts(db, "m1", "ios_premium", captured=50, total=100)  # 50% gap 5%
    db.commit()

    detected = detect_segment_conversion_opportunities(
        db, "m1", min_segment_attempts=50, min_absolute_gap=0.08, max_results=5
    )
    # Gap 5% < 8% threshold, so no detection
    assert len(detected) == 0


def test_severity_is_deterministic(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempts(db, "m1", "android_budget", captured=30, total=100)
    _add_attempts(db, "m1", "ios_premium", captured=80, total=100)
    db.commit()

    d1 = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)
    d2 = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)
    assert len(d1) == len(d2)
    for a, b in zip(d1, d2):
        assert a.severity == b.severity
        assert a.segment == b.segment


def test_severity_between_0_and_1(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempts(db, "m1", "android_budget", captured=10, total=200)
    _add_attempts(db, "m1", "ios_premium", captured=180, total=200)
    db.commit()

    detected = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)
    for d in detected:
        assert 0.0 <= d.severity <= 1.0


def test_results_sorted_by_severity_descending(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    # Create 3 underperforming segments with different gaps
    _add_attempts(db, "m1", "android_budget", captured=10, total=100)  # 10% vs others high
    _add_attempts(db, "m1", "android_mid", captured=30, total=100)  # 30%
    _add_attempts(db, "m1", "web_general", captured=50, total=100)  # 50%
    _add_attempts(db, "m1", "ios_premium", captured=90, total=100)  # 90% high baseline
    _add_attempts(db, "m1", "repeat_buyer", captured=90, total=100)  # 90%
    db.commit()

    detected = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=10)
    # Check sorted by severity descending
    severities = [d.severity for d in detected]
    assert severities == sorted(severities, reverse=True)
    # Tie-break deterministically by segment name
    # For same severity, ensure alphabetical
    # We'll create a scenario with equal severity
    # But at least check that sorting is deterministic


def test_max_results_enforced(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempts(db, "m1", "android_budget", captured=10, total=100)
    _add_attempts(db, "m1", "android_mid", captured=20, total=100)
    _add_attempts(db, "m1", "web_general", captured=30, total=100)
    _add_attempts(db, "m1", "ios_premium", captured=90, total=100)
    _add_attempts(db, "m1", "repeat_buyer", captured=90, total=100)
    db.commit()

    detected = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=2)
    assert len(detected) <= 2


def test_evidence_contains_only_observable_metrics(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempts(db, "m1", "android_budget", captured=20, total=100)
    _add_attempts(db, "m1", "ios_premium", captured=80, total=100)
    db.commit()

    detected = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)
    assert len(detected) > 0
    for d in detected:
        ev = d.evidence
        # Must contain observable fields
        assert "segment" in ev
        assert "segment_attempts" in ev
        assert "segment_captured" in ev
        assert "segment_conversion_rate" in ev
        assert "comparison_attempts" in ev
        assert "comparison_captured" in ev
        assert "comparison_conversion_rate" in ev
        assert "absolute_gap" in ev
        assert "payment_method_metrics" in ev
        assert "failure_reasons" in ev

        # Must NOT contain forbidden causal fields
        forbidden = [
            "expected_lift",
            "treatment_effect",
            "hidden_problem",
            "best_intervention",
            "causal_label",
            "simulate_outcome",
            "causal_model_fingerprint",
            "best treatment",
            "hidden",
            "expected intervention",
        ]
        ev_str = str(ev).lower()
        for term in ["expected_lift", "treatment_effect", "hidden_problem", "best_intervention", "causal_label"]:
            assert term not in ev_str

        # Evidence should be based only on PaymentAttempt data, not contain intervention suggestions
        assert "intervention" not in ev_str or "allowed_interventions" not in ev_str


def test_evidence_includes_target_segment_payment_method_metrics(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    # android_budget with mixed payment methods
    for i in range(60):
        pa = PaymentAttempt(
            merchant_id="m1",
            amount=100000,
            status="failed" if i < 40 else "captured",
            segment="android_budget",
            payment_method="upi" if i % 2 == 0 else "card",
            failure_reason="bank_declined" if i < 40 else None,
            customer_ref=f"cust_ab_{i}",
            currency="INR",
        )
        db.add(pa)
    _add_attempts(db, "m1", "ios_premium", captured=80, total=100)
    db.commit()

    detected = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)
    det = next(d for d in detected if d.segment == "android_budget")
    assert "payment_method_metrics" in det.evidence
    pm_metrics = det.evidence["payment_method_metrics"]
    # Should contain upi and card
    assert "upi" in pm_metrics
    assert "card" in pm_metrics
    # Check structure
    for method, metrics in pm_metrics.items():
        assert "attempts" in metrics
        assert "captured" in metrics
        assert "success_rate" in metrics


def test_evidence_includes_failure_reason_counts(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    # android_budget with failures
    for i in range(100):
        status = "captured" if i < 20 else "failed"
        fr = None if status == "captured" else ("bank_declined" if i % 2 == 0 else "network_error")
        pa = PaymentAttempt(
            merchant_id="m1",
            amount=100000,
            status=status,
            segment="android_budget",
            payment_method="upi",
            failure_reason=fr,
            customer_ref=f"cust_ab_{i}",
            currency="INR",
        )
        db.add(pa)
    _add_attempts(db, "m1", "ios_premium", captured=80, total=100)
    db.commit()

    detected = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)
    det = next(d for d in detected if d.segment == "android_budget")
    assert "failure_reasons" in det.evidence
    fr = det.evidence["failure_reasons"]
    assert "bank_declined" in fr or "network_error" in fr


def test_detector_does_not_persist_by_itself(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempts(db, "m1", "android_budget", captured=20, total=100)
    _add_attempts(db, "m1", "ios_premium", captured=80, total=100)
    db.commit()

    detected = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)
    # After detection, no Opportunity should exist in DB
    opps = db.query(Opportunity).filter(Opportunity.merchant_id == "m1").all()
    assert len(opps) == 0
    # Now persist
    persisted = persist_detected_opportunities(db, "m1", detected)
    db.commit()
    opps_after = db.query(Opportunity).filter(Opportunity.merchant_id == "m1").all()
    assert len(opps_after) == len(persisted)


def test_persistence_creates_opportunity_orm_records_correctly(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempts(db, "m1", "android_budget", captured=20, total=100)
    _add_attempts(db, "m1", "ios_premium", captured=80, total=100)
    db.commit()

    detected = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)
    persisted = persist_detected_opportunities(db, "m1", detected)
    db.commit()

    assert len(persisted) > 0
    for opp in persisted:
        assert opp.merchant_id == "m1"
        assert opp.type == "segment_conversion_divergence"
        assert opp.segment is not None
        assert opp.severity >= 0 and opp.severity <= 1
        assert opp.detected_metric == "conversion_rate"
        assert opp.detected_value is not None
        assert opp.baseline_value is not None
        assert opp.evidence is not None
        assert opp.status == "detected"


def test_duplicate_active_opportunity_is_not_created_twice(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempts(db, "m1", "android_budget", captured=20, total=100)
    _add_attempts(db, "m1", "ios_premium", captured=80, total=100)
    db.commit()

    detected = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)
    persisted1 = persist_detected_opportunities(db, "m1", detected)
    db.commit()
    count1 = db.query(Opportunity).filter(Opportunity.merchant_id == "m1").count()

    # Run again
    detected2 = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)
    persisted2 = persist_detected_opportunities(db, "m1", detected2)
    db.commit()
    count2 = db.query(Opportunity).filter(Opportunity.merchant_id == "m1").count()

    assert count1 == count2
    # persisted2 should return existing records
    assert len(persisted1) == len(persisted2)
    assert persisted1[0].id == persisted2[0].id


def test_previously_resolved_opportunity_may_allow_new_one(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _add_attempts(db, "m1", "android_budget", captured=20, total=100)
    _add_attempts(db, "m1", "ios_premium", captured=80, total=100)
    db.commit()

    detected = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)
    persisted = persist_detected_opportunities(db, "m1", detected)
    db.commit()
    assert len(persisted) == 1
    opp = persisted[0]

    # Mark as resolved
    opp.status = "resolved"
    db.commit()

    # The low-level detector can still recompute a fresh result if explicitly
    # called; Task 21B's revision gate lives in run_opportunity_detection.
    detected2 = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)
    persisted2 = persist_detected_opportunities(db, "m1", detected2)
    db.commit()

    count = db.query(Opportunity).filter(Opportunity.merchant_id == "m1").count()
    # Should have 2 now: one resolved, one new detected
    assert count == 2
    # Latest should be detected
    active = db.query(Opportunity).filter(Opportunity.merchant_id == "m1", Opportunity.status == "detected").all()
    assert len(active) == 1


def test_merchant_isolation_works(temp_db_session):
    db = temp_db_session
    _create_merchant(db, "m1")
    _create_merchant(db, "m2")
    _add_attempts(db, "m1", "android_budget", captured=20, total=100)
    _add_attempts(db, "m1", "ios_premium", captured=80, total=100)
    _add_attempts(db, "m2", "android_budget", captured=80, total=100)
    _add_attempts(db, "m2", "ios_premium", captured=80, total=100)
    db.commit()

    detected_m1 = detect_segment_conversion_opportunities(db, "m1", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)
    detected_m2 = detect_segment_conversion_opportunities(db, "m2", min_segment_attempts=50, min_absolute_gap=0.05, max_results=5)

    assert len(detected_m1) > 0
    assert len(detected_m2) == 0  # m2 has equal conversion, no underperformer


def test_no_hidden_causal_import_reference():
    # Static AST test that proves metrics.py and opportunities.py do NOT import causal_model
    # and do not mention forbidden terms simulate_outcome, causal_model_fingerprint
    base_path = Path(__file__).resolve().parent.parent / "app" / "engines"
    for file_name in ["metrics.py", "opportunities.py"]:
        file_path = base_path / file_name
        assert file_path.exists(), f"{file_name} does not exist"
        source = file_path.read_text(encoding="utf-8")
        # Check forbidden terms per task spec
        assert "simulate_outcome" not in source, f"{file_name} contains forbidden 'simulate_outcome'"
        assert "causal_model_fingerprint" not in source, f"{file_name} contains forbidden fingerprint"
        # AST check for imports - must not import app.simulation.causal_model
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "causal_model" not in alias.name, f"{file_name} imports causal_model: {alias.name}"
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    assert "causal_model" not in node.module, f"{file_name} imports from causal_model: {node.module}"


def test_canonical_techbazaar_seeded_baseline_yields_opportunity(tmp_path):
    """Use Task 05 seed logic and verify one historical revision is not replayed."""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from app.db.base import Base
    import sys
    from pathlib import Path

    scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    from seed_demo import seed_demo

    db_file = tmp_path / "test_techbazaar.db"
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
        seed_demo(db=session, seed=20260827)
        detected = detect_segment_conversion_opportunities(
            session,
            merchant_id="merchant_techbazaar",
            min_segment_attempts=100,
            min_absolute_gap=0.08,
            max_results=3,
        )
        assert len(detected) >= 1, "Expected at least one opportunity in canonical baseline"

        for d in detected:
            assert d.segment in {"android_mid", "android_budget", "web_general", "repeat_buyer", "ios_premium"}
            assert d.absolute_gap >= 0.08
            ev = d.evidence
            assert "segment_attempts" in ev
            assert "payment_method_metrics" in ev
            assert "failure_reasons" in ev
            assert "expected_lift" not in str(ev).lower()

        # Use the orchestrated helper once so the detector-pass marker is
        # established for this exact historical revision.
        persisted = run_opportunity_detection(session, "merchant_techbazaar")
        session.commit()
        assert len(persisted) >= 1

        for opp in persisted:
            opp.status = "resolved"
        session.commit()

        # Task 21B invariant: unchanged historical evidence is consumed and
        # cannot be manufactured into another fresh opportunity pass.
        opps_via_run = run_opportunity_detection(session, "merchant_techbazaar")
        session.commit()
        assert opps_via_run == []

    finally:
        session.close()
        engine.dispose()
