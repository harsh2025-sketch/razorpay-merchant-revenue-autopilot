"""Task 21A merchant onboarding and initial CSV ingestion tests."""

from __future__ import annotations

import os
import tempfile

_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP.name}")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.base import Base  # noqa: E402
from app.db.models import Merchant, MerchantPolicy, PaymentAttempt  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.services.onboarding import (  # noqa: E402
    MerchantAlreadyHasDataError,
    ingest_initial_csv,
)


CANONICAL_CSV = b"""external_id,amount_paise,status,created_at,segment,payment_method,failure_reason,device_type,currency\npay_1,129900,captured,2026-08-01T10:00:00Z,android_budget,upi,,android,INR\npay_2,249900,failed,2026-08-01T10:05:00Z,android_budget,card,authentication_failed,android,INR\npay_3,49900,abandoned,2026-08-01T10:10:00Z,web_general,upi,,web,INR\n"""


@pytest.fixture
def db_session(tmp_path):
    db_file = tmp_path / "test_onboarding.db"
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


def _onboard(client: TestClient, *, csv_bytes: bytes = CANONICAL_CSV):
    return client.post(
        "/api/v1/onboarding/merchants/with-csv",
        data={
            "name": "Acme Commerce",
            "category": "retail",
            "monthly_gmv_paise": "250000000",
        },
        files={"file": ("payments.csv", csv_bytes, "text/csv")},
    )


def test_onboarding_registers_merchant_policy_and_real_payment_history(
    client, db_session
):
    response = _onboard(client)

    assert response.status_code == 201, response.text
    payload = response.json()
    merchant_id = payload["merchant_id"]

    assert payload["name"] == "Acme Commerce"
    assert payload["data_source"] == "merchant_csv"
    assert payload["rows_imported"] == 3
    assert payload["historical_observations"] == 3
    assert payload["real_observations"] == 3
    assert payload["simulated_observations"] == 0
    assert payload["segment_count"] == 2

    merchant = db_session.get(Merchant, merchant_id)
    assert merchant is not None
    assert merchant.name == "Acme Commerce"
    assert merchant.monthly_gmv == 250000000

    policy = (
        db_session.query(MerchantPolicy)
        .filter(MerchantPolicy.merchant_id == merchant_id)
        .one()
    )
    assert policy.max_experiment_exposure_pct == 0.10
    assert set(policy.allowed_interventions) == {
        "payment_method_config",
        "offer_discount",
        "partial_payment",
        "expiry_config",
    }

    rows = (
        db_session.query(PaymentAttempt)
        .filter(PaymentAttempt.merchant_id == merchant_id)
        .order_by(PaymentAttempt.created_at)
        .all()
    )
    assert len(rows) == 3
    assert all(row.is_simulated is False for row in rows)
    assert all(row.source == "merchant_csv" for row in rows)
    assert {row.status for row in rows} == {"captured", "failed", "abandoned"}
    assert {row.segment for row in rows} == {"android_budget", "web_general"}


def test_invalid_csv_rolls_back_merchant_registration_atomically(client, db_session):
    bad_csv = b"""external_id,amount_paise,status,created_at,segment,payment_method\npay_1,not-money,captured,2026-08-01T10:00:00Z,android_budget,upi\n"""

    response = _onboard(client, csv_bytes=bad_csv)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "CSV_VALIDATION_FAILED"
    assert db_session.query(Merchant).count() == 0
    assert db_session.query(MerchantPolicy).count() == 0
    assert db_session.query(PaymentAttempt).count() == 0


def test_missing_required_csv_column_is_rejected_without_partial_rows(client, db_session):
    missing_segment = b"""external_id,amount_paise,status,created_at,payment_method\npay_1,10000,captured,2026-08-01T10:00:00Z,upi\n"""

    response = _onboard(client, csv_bytes=missing_segment)

    assert response.status_code == 422
    assert "segment" in response.json()["detail"]["message"]
    assert db_session.query(Merchant).count() == 0
    assert db_session.query(PaymentAttempt).count() == 0


def test_duplicate_external_ids_are_rejected(client, db_session):
    duplicate = b"""external_id,amount_paise,status,created_at,segment,payment_method\npay_1,10000,captured,2026-08-01T10:00:00Z,android_budget,upi\npay_1,12000,failed,2026-08-01T10:01:00Z,web_general,card\n"""

    response = _onboard(client, csv_bytes=duplicate)

    assert response.status_code == 422
    assert "duplicate external_id" in response.json()["detail"]["message"]
    assert db_session.query(Merchant).count() == 0
    assert db_session.query(PaymentAttempt).count() == 0


def test_data_status_is_scoped_to_the_requested_merchant(client, db_session):
    response = _onboard(client)
    merchant_id = response.json()["merchant_id"]

    other = Merchant(id="other_merchant", name="Other Merchant")
    db_session.add(other)
    db_session.add(
        PaymentAttempt(
            id="other_attempt",
            merchant_id=other.id,
            amount=5000,
            currency="INR",
            payment_method="upi",
            status="captured",
            segment="other_segment",
            source="merchant_csv",
            is_simulated=False,
        )
    )
    db_session.commit()

    status = client.get(f"/api/v1/onboarding/merchants/{merchant_id}/data-status")
    assert status.status_code == 200
    assert status.json()["historical_observations"] == 3
    assert status.json()["real_observations"] == 3
    assert status.json()["segment_count"] == 2


def test_demo_source_resolves_existing_techbazaar_without_copying_data(client, db_session):
    demo = Merchant(id="merchant_techbazaar", name="TechBazaar Electronics")
    db_session.add(demo)
    db_session.add(
        PaymentAttempt(
            id="demo_attempt",
            merchant_id=demo.id,
            amount=10000,
            currency="INR",
            payment_method="upi",
            status="captured",
            segment="android_budget",
            source="baseline_simulator",
            is_simulated=True,
        )
    )
    db_session.commit()

    response = client.get("/api/v1/onboarding/demo")

    assert response.status_code == 200
    payload = response.json()
    assert payload["merchant_id"] == "merchant_techbazaar"
    assert payload["name"] == "TechBazaar Electronics"
    assert payload["data_source"] == "demo"
    assert payload["historical_observations"] == 1
    assert db_session.query(Merchant).filter(Merchant.id == "merchant_techbazaar").count() == 1


def test_initial_ingestion_refuses_to_replay_existing_history(db_session):
    merchant = Merchant(id="merchant_existing", name="Existing Merchant")
    db_session.add(merchant)
    db_session.add(
        PaymentAttempt(
            id="existing_attempt",
            merchant_id=merchant.id,
            amount=10000,
            currency="INR",
            payment_method="upi",
            status="captured",
            segment="android_budget",
            source="merchant_csv",
            is_simulated=False,
        )
    )
    db_session.flush()

    with pytest.raises(MerchantAlreadyHasDataError):
        ingest_initial_csv(db_session, merchant_id=merchant.id, content=CANONICAL_CSV)
