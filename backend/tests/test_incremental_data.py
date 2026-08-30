"""Task 21B incremental evidence and demo-period regression tests."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP.name}")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.models import (  # noqa: E402
    Experiment,
    ExperimentResult,
    Merchant,
    MerchantPolicy,
    OperationExecution,
    Opportunity,
    PaymentAttempt,
)
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import cycles  # noqa: E402
from app.services.incremental_data import (  # noqa: E402
    DATA_APPEND_OPERATION_TYPE,
    ingest_incremental_csv,
)


INITIAL_CSV = b"""external_id,amount_paise,status,created_at,segment,payment_method,failure_reason,device_type,currency\npay_1,129900,captured,2026-08-01T10:00:00Z,android_budget,upi,,android,INR\npay_2,249900,failed,2026-08-01T10:05:00Z,android_budget,card,authentication_failed,android,INR\n"""

PARTIAL_DUPLICATE_CSV = b"""external_id,amount_paise,status,created_at,segment,payment_method,failure_reason,device_type,currency\npay_2,249900,failed,2026-08-01T10:05:00Z,android_budget,card,authentication_failed,android,INR\npay_3,49900,abandoned,2026-08-02T10:10:00Z,web_general,upi,,web,INR\n"""

ALL_DUPLICATE_CSV = b"""external_id,amount_paise,status,created_at,segment,payment_method,failure_reason,device_type,currency\npay_1,129900,captured,2026-08-01T10:00:00Z,android_budget,upi,,android,INR\npay_2,249900,failed,2026-08-01T10:05:00Z,android_budget,card,authentication_failed,android,INR\n"""

CONFLICTING_CSV = b"""external_id,amount_paise,status,created_at,segment,payment_method\npay_1,999999,captured,2026-08-01T10:00:00Z,android_budget,upi\npay_4,10000,captured,2026-08-03T10:00:00Z,android_budget,upi\n"""

NEW_ROW_CSV = b"""external_id,amount_paise,status,created_at,segment,payment_method\npay_new,10000,captured,2026-08-04T10:00:00Z,android_budget,upi\n"""


@pytest.fixture
def db_session(tmp_path):
    db_file = tmp_path / "test_incremental.db"
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


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


def _onboard(client: TestClient, *, name: str = "Acme Commerce", csv_bytes=INITIAL_CSV):
    return client.post(
        "/api/v1/onboarding/merchants/with-csv",
        data={"name": name, "category": "retail"},
        files={"file": ("payments.csv", csv_bytes, "text/csv")},
    )


def _append_csv(client: TestClient, merchant_id: str, content: bytes):
    return client.post(
        f"/api/v1/data/merchants/{merchant_id}/append-csv",
        files={"file": ("new-payments.csv", content, "text/csv")},
    )


def test_incremental_csv_appends_only_new_transactions(client, db_session):
    merchant_id = _onboard(client).json()["merchant_id"]

    response = _append_csv(client, merchant_id, PARTIAL_DUPLICATE_CSV)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["rows_received"] == 2
    assert payload["rows_appended"] == 1
    assert payload["rows_deduplicated"] == 1
    assert payload["historical_observations"] == 3
    assert payload["real_observations"] == 3
    rows = db_session.query(PaymentAttempt).filter_by(merchant_id=merchant_id).all()
    assert len(rows) == 3
    assert all(row.is_simulated is False for row in rows)
    revisions = (
        db_session.query(OperationExecution)
        .filter(OperationExecution.operation_type == DATA_APPEND_OPERATION_TYPE)
        .all()
    )
    assert len(revisions) == 1
    assert revisions[0].status == "succeeded"
    assert revisions[0].response_json["rows_appended"] == 1


def test_reupload_of_unchanged_transactions_is_a_noop_revision(client, db_session):
    merchant_id = _onboard(client).json()["merchant_id"]

    first = _append_csv(client, merchant_id, ALL_DUPLICATE_CSV)
    second = _append_csv(client, merchant_id, ALL_DUPLICATE_CSV)

    assert first.status_code == second.status_code == 200
    assert first.json()["rows_appended"] == 0
    assert first.json()["rows_deduplicated"] == 2
    assert second.json()["rows_appended"] == 0
    assert db_session.query(PaymentAttempt).filter_by(merchant_id=merchant_id).count() == 2
    assert (
        db_session.query(OperationExecution)
        .filter(OperationExecution.operation_type == DATA_APPEND_OPERATION_TYPE)
        .count()
        == 0
    )


def test_conflicting_duplicate_external_id_fails_atomically(client, db_session):
    merchant_id = _onboard(client).json()["merchant_id"]
    before = db_session.query(PaymentAttempt).filter_by(merchant_id=merchant_id).count()

    response = _append_csv(client, merchant_id, CONFLICTING_CSV)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "TRANSACTION_CONFLICT"
    assert db_session.query(PaymentAttempt).filter_by(merchant_id=merchant_id).count() == before
    assert (
        db_session.query(PaymentAttempt)
        .filter(PaymentAttempt.merchant_id == merchant_id)
        .filter(PaymentAttempt.internal_order_ref == "pay_4")
        .count()
        == 0
    )


def test_same_external_ids_are_isolated_per_merchant(client, db_session):
    first_id = _onboard(client, name="Merchant One").json()["merchant_id"]
    second_id = _onboard(client, name="Merchant Two").json()["merchant_id"]

    assert first_id != second_id
    assert db_session.query(PaymentAttempt).filter_by(merchant_id=first_id).count() == 2
    assert db_session.query(PaymentAttempt).filter_by(merchant_id=second_id).count() == 2
    assert {
        row.id for row in db_session.query(PaymentAttempt).filter_by(merchant_id=first_id)
    }.isdisjoint(
        {
            row.id
            for row in db_session.query(PaymentAttempt).filter_by(merchant_id=second_id)
        }
    )


def test_demo_periods_append_strictly_new_time_windows_and_ids(client, db_session):
    merchant = Merchant(id="merchant_techbazaar", name="TechBazaar Electronics")
    db_session.add(merchant)
    db_session.add(
        PaymentAttempt(
            id="pa_baseline_seed",
            merchant_id=merchant.id,
            amount=10000,
            currency="INR",
            payment_method="upi",
            status="captured",
            segment="android_budget",
            source="organic",
            experiment_id=None,
            created_at=datetime(2026, 8, 26, 23, 0, tzinfo=timezone.utc),
            is_simulated=True,
        )
    )
    db_session.commit()

    first = client.post("/api/v1/data/demo/next-period")
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["period_index"] == 2
    assert first_payload["rows_appended"] > 0

    period_two = (
        db_session.query(PaymentAttempt)
        .filter(PaymentAttempt.id.like("pa_demo_p002_%"))
        .all()
    )
    assert period_two
    period_two_ids = {row.id for row in period_two}
    period_two_max = max(row.created_at for row in period_two)
    assert min(row.created_at for row in period_two).date().isoformat() == "2026-08-27"

    second = client.post("/api/v1/data/demo/next-period")
    assert second.status_code == 200, second.text
    second_payload = second.json()
    assert second_payload["period_index"] == 3

    period_three = (
        db_session.query(PaymentAttempt)
        .filter(PaymentAttempt.id.like("pa_demo_p003_%"))
        .all()
    )
    assert period_three
    assert {row.id for row in period_three}.isdisjoint(period_two_ids)
    assert min(row.created_at for row in period_three) > period_two_max
    assert (
        db_session.query(OperationExecution)
        .filter(OperationExecution.operation_type == DATA_APPEND_OPERATION_TYPE)
        .count()
        == 2
    )


def _real_merchant_with_terminal_cycle(db_session, merchant_id: str = "merchant_real"):
    merchant = Merchant(id=merchant_id, name="Real Merchant")
    policy = MerchantPolicy(
        merchant_id=merchant_id,
        max_experiment_exposure_pct=0.10,
        max_discount_pct=0.15,
        min_margin_pct=0.05,
        max_concurrent_experiments=3,
        max_experiment_duration_hours=168,
        min_sample_size=30,
        max_financial_exposure=50000,
        allowed_interventions=["payment_method_config"],
    )
    db_session.add_all([merchant, policy])
    db_session.flush()
    db_session.add(
        PaymentAttempt(
            id="existing_real_attempt",
            merchant_id=merchant_id,
            internal_order_ref="seed_real",
            amount=10000,
            currency="INR",
            payment_method="upi",
            status="captured",
            segment="android_budget",
            source="merchant_csv",
            created_at=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
            is_simulated=False,
        )
    )
    opportunity = Opportunity(
        id="old_opp",
        merchant_id=merchant_id,
        type="segment_conversion_divergence",
        segment="android_budget",
        severity=0.2,
        detected_metric="conversion_rate",
        detected_value=0.4,
        baseline_value=0.6,
        evidence={},
        status="detected",
    )
    db_session.add(opportunity)
    db_session.flush()
    hypothesis_id = "old_hyp"
    from app.db.models import Hypothesis

    db_session.add(
        Hypothesis(
            id=hypothesis_id,
            opportunity_id=opportunity.id,
            merchant_id=merchant_id,
            hypothesis_text="test",
            intervention_type="payment_method_config",
            intervention_params={},
            evidence_refs=[],
            status="proposed",
        )
    )
    db_session.flush()
    experiment = Experiment(
        id="old_exp",
        merchant_id=merchant_id,
        hypothesis_id=hypothesis_id,
        opportunity_id=opportunity.id,
        name="old experiment",
        segment="android_budget",
        intervention_type="payment_method_config",
        control_config={},
        treatment_config={"payment_methods": {"upi": True}},
        traffic_split_treatment_pct=0.1,
        primary_metric="conversion_rate",
        guardrail_metrics=[],
        min_sample_per_variant=10,
        max_duration_hours=24,
        status="completed",
    )
    db_session.add(experiment)
    db_session.flush()
    db_session.add(
        ExperimentResult(
            experiment_id=experiment.id,
            control_count=10,
            treatment_count=10,
            control_conversions=5,
            treatment_conversions=5,
            decision="INCONCLUSIVE",
        )
    )
    db_session.flush()
    return merchant, opportunity


def test_rollover_does_not_redetect_when_data_did_not_advance(db_session, monkeypatch):
    merchant, old_opportunity = _real_merchant_with_terminal_cycle(db_session)
    called = False

    def forbidden_detector(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("unchanged data must not be scanned as a new pass")

    monkeypatch.setattr(cycles, "run_opportunity_detection", forbidden_detector)

    next_opportunity = cycles.start_new_cycle(db_session, merchant.id)

    assert next_opportunity is None
    assert called is False
    assert db_session.get(Opportunity, old_opportunity.id).status == "resolved"
    assert db_session.query(Opportunity).count() == 1


def test_new_append_enables_one_fresh_detection_pass(db_session, monkeypatch):
    merchant, old_opportunity = _real_merchant_with_terminal_cycle(db_session)
    ingest_incremental_csv(db_session, merchant_id=merchant.id, content=NEW_ROW_CSV)
    db_session.flush()
    calls = 0

    def fresh_detector(db, merchant_id, **_kwargs):
        nonlocal calls
        calls += 1
        fresh = Opportunity(
            id="fresh_opp",
            merchant_id=merchant_id,
            type="segment_conversion_divergence",
            segment="web_general",
            severity=0.3,
            detected_metric="conversion_rate",
            detected_value=0.3,
            baseline_value=0.6,
            evidence={"source": "updated observations"},
            status="detected",
        )
        db.add(fresh)
        db.flush()
        return [fresh]

    monkeypatch.setattr(cycles, "run_opportunity_detection", fresh_detector)

    next_opportunity = cycles.start_new_cycle(db_session, merchant.id)

    assert calls == 1
    assert next_opportunity is not None
    assert next_opportunity.id == "fresh_opp"
    assert db_session.get(Opportunity, old_opportunity.id).status == "resolved"
