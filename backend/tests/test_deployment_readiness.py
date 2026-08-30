"""Focused release-engineering tests for production deployment readiness."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.db.models import AuditEvent, Experiment, Hypothesis, Merchant, MerchantPolicy, Opportunity, PaymentAttempt
from app.simulation.merchant import TECHBAZAAR_PROFILE

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from bootstrap_demo import bootstrap_demo, safe_error_message  # noqa: E402
from smoke_deployment import READ_ONLY_CHECKS, SmokeFailure, run_smoke  # noqa: E402


@pytest.fixture()
def empty_sqlite_session(tmp_path):
    """Yield an empty SQLite DB; bootstrap must create missing tables itself."""
    db_file = tmp_path / "bootstrap.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_bootstrap_creates_fresh_sqlite_baseline_and_is_idempotent(empty_sqlite_session):
    first = bootstrap_demo(db=empty_sqlite_session)

    assert first["merchant_created"] is True
    assert first["policy_created"] is True
    assert first["baseline_attempts_inserted"] == first["total_attempts"]
    assert _count(empty_sqlite_session, Merchant) == 1
    assert _count(empty_sqlite_session, MerchantPolicy) == 1
    assert _count(empty_sqlite_session, PaymentAttempt) == first["total_attempts"]

    second = bootstrap_demo(db=empty_sqlite_session)

    assert second["merchant_created"] is False
    assert second["policy_created"] is False
    assert second["baseline_attempts_inserted"] == 0
    assert second["baseline_attempts_existing"] == second["total_attempts"]
    assert _count(empty_sqlite_session, Merchant) == 1
    assert _count(empty_sqlite_session, MerchantPolicy) == 1
    assert _count(empty_sqlite_session, PaymentAttempt) == first["total_attempts"]


def test_bootstrap_preserves_existing_lifecycle_rows(empty_sqlite_session):
    bootstrap_demo(db=empty_sqlite_session)
    opportunity = Opportunity(
        id="opp_existing_lifecycle",
        merchant_id=TECHBAZAAR_PROFILE.merchant_id,
        type="checkout_drop",
        segment="android_mid",
        severity=0.75,
        detected_metric="conversion_rate",
        detected_value=0.4,
        baseline_value=0.5,
        evidence={"observable": True},
        status="detected",
    )
    hypothesis = Hypothesis(
        id="hyp_existing_lifecycle",
        opportunity_id=opportunity.id,
        merchant_id=TECHBAZAAR_PROFILE.merchant_id,
        ai_model="offline-test",
        hypothesis_text="Existing lifecycle row must survive bootstrap.",
        intervention_type="payment_method_config",
        intervention_params={"enabled_methods": ["upi"]},
        confidence="medium",
        reasoning_summary="Persisted before bootstrap rerun.",
        evidence_refs=["observable"],
        status="proposed",
    )
    experiment = Experiment(
        id="exp_existing_lifecycle",
        merchant_id=TECHBAZAAR_PROFILE.merchant_id,
        hypothesis_id=hypothesis.id,
        opportunity_id=opportunity.id,
        name="Existing lifecycle experiment",
        segment="android_mid",
        intervention_type="payment_method_config",
        control_config={"enabled_methods": ["upi", "card"]},
        treatment_config={"enabled_methods": ["upi"]},
        traffic_split_treatment_pct=0.10,
        primary_metric="conversion_rate",
        guardrail_metrics=["captured_gmv_paise"],
        min_sample_per_variant=30,
        max_duration_hours=168,
        status="running",
    )
    audit = AuditEvent(
        id="audit_existing_lifecycle",
        merchant_id=TECHBAZAAR_PROFILE.merchant_id,
        event_type="EXISTING_LIFECYCLE_EVENT",
        entity_type="experiment",
        entity_id=experiment.id,
        data={"safe": True},
        actor="test",
    )
    empty_sqlite_session.add(opportunity)
    empty_sqlite_session.commit()
    empty_sqlite_session.add(hypothesis)
    empty_sqlite_session.commit()
    empty_sqlite_session.add(experiment)
    empty_sqlite_session.add(audit)
    empty_sqlite_session.commit()

    bootstrap_demo(db=empty_sqlite_session)

    assert empty_sqlite_session.get(Opportunity, opportunity.id) is not None
    assert empty_sqlite_session.get(Hypothesis, hypothesis.id) is not None
    persisted_experiment = empty_sqlite_session.get(Experiment, experiment.id)
    assert persisted_experiment is not None
    assert persisted_experiment.status == "running"
    assert empty_sqlite_session.get(AuditEvent, audit.id) is not None


def test_bootstrap_source_has_no_external_integration_calls():
    source = (SCRIPTS_DIR / "bootstrap_demo.py").read_text(encoding="utf-8")

    assert "RazorpayClient" not in source
    assert "from openai" not in source.lower()
    assert "advance_autopilot" not in source
    assert "run_batch(" not in source
    assert "reset_demo(" not in source


def test_bootstrap_error_output_redacts_secret_shapes():
    database_url = "postgresql://demo_user" + ":db_password" + "@example.supabase.co/db"
    openai_like = "sk" + "-secretvalue"
    razorpay_like = "rzp" + "_live_secretvalue"
    message = safe_error_message(
        RuntimeError(
            f"could not connect to {database_url} with {openai_like} and {razorpay_like}"
        )
    )

    assert "db_password" not in message
    assert openai_like not in message
    assert razorpay_like not in message
    assert "[redacted]" in message


def test_smoke_script_validates_expected_read_only_shapes():
    payloads = {
        "/health": {"status": "ok", "service": "merchant-revenue-autopilot"},
        f"/api/v1/merchants/{TECHBAZAAR_PROFILE.merchant_id}/overview": {
            "merchant": {
                "merchant_id": TECHBAZAAR_PROFILE.merchant_id,
                "name": TECHBAZAAR_PROFILE.name,
            },
            "metrics": {},
            "segment_metrics": [],
            "payment_method_metrics": [],
            "autopilot_status": {},
        },
        f"/api/v1/merchants/{TECHBAZAAR_PROFILE.merchant_id}/opportunities": [],
        f"/api/v1/merchants/{TECHBAZAAR_PROFILE.merchant_id}/audit?limit=5": [],
    }
    requested_paths: list[str] = []

    def fake_fetch(_base_url: str, path: str):
        requested_paths.append(path)
        return payloads[path]

    run_smoke("https://backend.example.com", fetch_json=fake_fetch)

    assert requested_paths == [path for _name, path, _validator in READ_ONLY_CHECKS]
    assert all("/autopilot/step" not in path for path in requested_paths)
    assert all("/run" not in path for path in requested_paths)


def test_smoke_script_rejects_wrong_merchant_identity():
    payloads = {
        "/health": {"status": "ok", "service": "merchant-revenue-autopilot"},
        f"/api/v1/merchants/{TECHBAZAAR_PROFILE.merchant_id}/overview": {
            "merchant": {"merchant_id": "other_merchant", "name": "Other"},
            "metrics": {},
            "segment_metrics": [],
            "payment_method_metrics": [],
            "autopilot_status": {},
        },
    }

    def fake_fetch(_base_url: str, path: str):
        return payloads[path]

    with pytest.raises(SmokeFailure, match="merchant_techbazaar"):
        run_smoke("https://backend.example.com", fetch_json=fake_fetch)
