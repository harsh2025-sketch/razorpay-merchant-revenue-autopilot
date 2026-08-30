"""Tests for the database foundation and core domain models (Task 02)."""

import os
import tempfile

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

# Point DATABASE_URL at a temporary SQLite file *before* any app imports
# that might create the engine.
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp.name}"

from app.db.base import Base  # noqa: E402
from app.db.models import (  # noqa: E402
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
from app.db.session import normalize_database_url  # noqa: E402


EXPECTED_TABLES = {
    "merchants",
    "merchant_policies",
    "payment_attempts",
    "opportunities",
    "hypotheses",
    "experiments",
    "experiment_assignments",
    "experiment_results",
    "policy_decisions",
    "razorpay_resources",
    "audit_events",
    "operation_executions",
}


def test_postgresql_database_url_normalization_uses_psycopg_driver():
    raw = "postgresql://demo_user@db.example.com:5432/postgres?sslmode=require"
    normalized = normalize_database_url(raw)

    assert normalized.startswith("postgresql+psycopg://")
    assert normalized.endswith("/postgres?sslmode=require")
    assert normalize_database_url("postgres://demo_user@host/db") == (
        "postgresql+psycopg://demo_user@host/db"
    )
    assert normalize_database_url("postgresql+psycopg://demo_user@host/db") == (
        "postgresql+psycopg://demo_user@host/db"
    )
    assert normalize_database_url("sqlite:///./data/autopilot.db") == (
        "sqlite:///./data/autopilot.db"
    )


@pytest.fixture(scope="module")
def engine():
    """Create a temporary SQLite engine with FK enforcement enabled."""
    eng = create_engine(f"sqlite:///{_tmp.name}", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()
    os.unlink(_tmp.name)


@pytest.fixture()
def session(engine):
    """Yield a session bound to the temporary engine."""
    session_cls = sessionmaker(bind=engine)
    sess = session_cls()
    try:
        yield sess
    finally:
        sess.close()


# --------------------------------------------------------------- Tests


def test_all_12_tables_created(engine):
    """All exactly 12 expected tables exist in the schema."""
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    assert EXPECTED_TABLES.issubset(actual_tables), (
        f"Missing tables: {EXPECTED_TABLES - actual_tables}"
    )


def test_no_extra_tables(engine):
    """No table outside the required 12 has been created."""
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    extras = actual_tables - EXPECTED_TABLES
    # SQLite internal tables are fine; ignore them.
    extras = {t for t in extras if not t.startswith("sqlite_")}
    assert extras == set(), f"Unexpected extra tables: {extras}"


def test_merchant_insert_and_fetch(session):
    """A Merchant can be inserted and fetched."""
    merchant = Merchant(name="TestShop", category="electronics", monthly_gmv=500000)
    session.add(merchant)
    session.commit()
    session.refresh(merchant)

    fetched = session.get(Merchant, merchant.id)
    assert fetched is not None
    assert fetched.name == "TestShop"
    assert fetched.category == "electronics"
    assert fetched.monthly_gmv == 500000
    assert fetched.created_at is not None


def test_merchant_policy_defaults(session):
    """MerchantPolicy defaults are correctly applied."""
    merchant = Merchant(name="PolicyShop")
    session.add(merchant)
    session.commit()

    policy = MerchantPolicy(merchant_id=merchant.id)
    session.add(policy)
    session.commit()
    session.refresh(policy)

    assert policy.max_experiment_exposure_pct == 0.10
    assert policy.max_discount_pct == 0.15
    assert policy.min_margin_pct == 0.05
    assert policy.max_concurrent_experiments == 3
    assert policy.max_experiment_duration_hours == 168
    assert policy.min_sample_size == 30
    assert policy.max_financial_exposure == 50000
    assert policy.allowed_interventions == []


def test_opportunity_json_evidence_roundtrip(session):
    """Opportunity with JSON evidence round-trips correctly."""
    merchant = Merchant(name="JSONShop")
    session.add(merchant)
    session.commit()

    evidence = {
        "drop_rate": 0.12,
        "window_days": 30,
        "segments": ["mobile", "repeat"],
    }
    opp = Opportunity(
        merchant_id=merchant.id,
        type="checkout_drop",
        severity=0.8,
        detected_metric="success_rate",
        detected_value=0.78,
        baseline_value=0.90,
        evidence=evidence,
    )
    session.add(opp)
    session.commit()
    session.refresh(opp)

    fetched = session.get(Opportunity, opp.id)
    assert fetched is not None
    assert fetched.evidence == evidence
    assert isinstance(fetched.evidence["segments"], list)


def test_experiment_assignment_unique_constraint(session):
    """ExperimentAssignment unique constraint rejects duplicate
    (experiment_id, customer_ref)."""
    merchant = Merchant(name="AssignShop")
    session.add(merchant)
    session.commit()

    opp = Opportunity(
        merchant_id=merchant.id,
        type="checkout_drop",
        severity=0.5,
        detected_metric="success_rate",
    )
    session.add(opp)
    session.commit()

    hyp = Hypothesis(
        opportunity_id=opp.id,
        merchant_id=merchant.id,
        hypothesis_text="Test",
        intervention_type="discount",
        intervention_params={"pct": 5},
    )
    session.add(hyp)
    session.commit()

    exp = Experiment(
        merchant_id=merchant.id,
        hypothesis_id=hyp.id,
        opportunity_id=opp.id,
        name="exp1",
        segment="mobile",
        intervention_type="discount",
        traffic_split_treatment_pct=50.0,
        primary_metric="success_rate",
        min_sample_per_variant=10,
        max_duration_hours=48,
    )
    session.add(exp)
    session.commit()

    a1 = ExperimentAssignment(
        experiment_id=exp.id, customer_ref="cust_001", variant="treatment"
    )
    session.add(a1)
    session.commit()

    a2 = ExperimentAssignment(
        experiment_id=exp.id, customer_ref="cust_001", variant="control"
    )
    session.add(a2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_operation_execution_unique_key(session):
    """OperationExecution unique operation_key rejects duplicates."""
    op1 = OperationExecution(
        operation_key="op-create-order-001",
        operation_type="create_order",
        request_payload_hash="abc123",
        status="completed",
    )
    session.add(op1)
    session.commit()

    op2 = OperationExecution(
        operation_key="op-create-order-001",
        operation_type="create_order",
        request_payload_hash="abc123",
        status="pending",
    )
    session.add(op2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_experiment_result_unique_experiment_id(session):
    """ExperimentResult enforces unique experiment_id."""
    merchant = Merchant(name="ResultShop")
    session.add(merchant)
    session.commit()

    opp = Opportunity(
        merchant_id=merchant.id,
        type="checkout_drop",
        severity=0.5,
        detected_metric="success_rate",
    )
    session.add(opp)
    session.commit()

    hyp = Hypothesis(
        opportunity_id=opp.id,
        merchant_id=merchant.id,
        hypothesis_text="Test",
        intervention_type="discount",
        intervention_params={"pct": 5},
    )
    session.add(hyp)
    session.commit()

    exp = Experiment(
        merchant_id=merchant.id,
        hypothesis_id=hyp.id,
        opportunity_id=opp.id,
        name="exp_res",
        segment="mobile",
        intervention_type="discount",
        traffic_split_treatment_pct=50.0,
        primary_metric="success_rate",
        min_sample_per_variant=10,
        max_duration_hours=48,
    )
    session.add(exp)
    session.commit()

    r1 = ExperimentResult(experiment_id=exp.id)
    session.add(r1)
    session.commit()

    r2 = ExperimentResult(experiment_id=exp.id)
    session.add(r2)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_foreign_key_definitions_valid(engine):
    """Foreign key columns reference the correct target tables."""
    inspector = inspect(engine)

    fk_checks = {
        "merchant_policies": {"merchants"},
        "payment_attempts": {"merchants", "experiments"},
        "opportunities": {"merchants"},
        "hypotheses": {"opportunities", "merchants"},
        "experiments": {"merchants", "hypotheses", "opportunities"},
        "experiment_assignments": {"experiments"},
        "experiment_results": {"experiments"},
        "policy_decisions": {"experiments", "merchants"},
        "razorpay_resources": {"experiments"},
        "audit_events": {"merchants"},
    }

    for table_name, expected_targets in fk_checks.items():
        fks = inspector.get_foreign_keys(table_name)
        actual_targets = {fk["referred_table"] for fk in fks}
        assert actual_targets == expected_targets, (
            f"Table '{table_name}': expected FK targets {expected_targets}, "
            f"got {actual_targets}"
        )


def test_foreign_key_enforcement(session):
    """An invalid FK insert fails (SQLite with PRAGMA foreign_keys=ON)."""
    # PaymentAttempt requires a valid merchant_id.
    pa = PaymentAttempt(
        merchant_id="nonexistent-merchant-id",
        amount=10000,
        status="created",
    )
    session.add(pa)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    # Verify the session is usable after rollback.
    count = session.execute(
        text("SELECT COUNT(*) FROM payment_attempts")
    ).scalar()
    assert isinstance(count, int)


def test_all_model_classes_importable():
    """All 12 model classes are importable from app.db.models."""
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

    models = [
        Merchant,
        MerchantPolicy,
        PaymentAttempt,
        Opportunity,
        Hypothesis,
        Experiment,
        ExperimentAssignment,
        ExperimentResult,
        PolicyDecision,
        RazorpayResource,
        AuditEvent,
        OperationExecution,
    ]
    assert len(models) == 12
