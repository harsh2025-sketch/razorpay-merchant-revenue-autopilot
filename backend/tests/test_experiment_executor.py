"""Tests for Task 13 Razorpay experiment executor + idempotent rollback.

All tests are offline. Real Razorpay traffic is replaced by a fake client;
the only external dependency used is the deterministic seeded baseline
generator for the full-chain test.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import (
    Experiment,
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
from app.engines.diagnosis import diagnose_opportunity
from app.engines.opportunities import run_opportunity_detection
from app.engines.planner import plan_experiment
from app.engines.policy import evaluate_experiment_policy
from app.services.executor import (
    DESCRIPTION,
    ExperimentExecutionAuthorizationError,
    ExperimentExecutionConfigurationError,
    ExperimentExecutionError,
    ExperimentExecutionStateError,
    TEST_AMOUNT_PAISE,
    compute_expire_by,
    deploy_experiment_treatment,
    rollback_experiment_treatment,
)
from app.services.idempotency import IdempotencyInProgressError
from app.services.razorpay import (
    RazorpayAuthenticationError,
    RazorpayBadRequestError,
    RazorpayServerError,
)
from app.simulation.generator import generate_baseline_events
from app.simulation.merchant import TECHBAZAAR_PROFILE
from tests.test_diagnosis_engine import MOCK_MODEL_RESPONSE, FakeOpenAIClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
EXECUTOR_PATH = BACKEND_DIR / "app" / "services" / "executor.py"
IDEMPOTENCY_PATH = BACKEND_DIR / "app" / "services" / "idempotency.py"

ALL_INTERVENTIONS = [
    "payment_method_config",
    "offer_discount",
    "partial_payment",
    "expiry_config",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session(tmp_path):
    db_file = tmp_path / "test_executor.db"
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


# ---------------------------------------------------------------------------
# Fake Razorpay client
# ---------------------------------------------------------------------------


class FakeRazorpayClient:
    """Offline Razorpay client stub that records create/cancel calls."""

    def __init__(self, *, create_error=None, cancel_error=None):
        self.create_calls: list[dict] = []
        self.cancel_calls: list[str] = []
        self.create_error = create_error
        self.cancel_error = cancel_error

    def create_payment_link(self, **kwargs):
        self.create_calls.append(kwargs)
        if self.create_error is not None:
            raise self.create_error
        link_id = f"plink_{len(self.create_calls)}"
        return {
            "id": link_id,
            "status": "created",
            "reference_id": kwargs.get("reference_id", ""),
            "amount": kwargs.get("amount", TEST_AMOUNT_PAISE),
        }

    def cancel_payment_link(self, payment_link_id):
        self.cancel_calls.append(payment_link_id)
        if self.cancel_error is not None:
            raise self.cancel_error
        return {"id": payment_link_id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _make_merchant(db, merchant_id: str) -> Merchant:
    merchant = db.get(Merchant, merchant_id)
    if merchant is None:
        merchant = Merchant(
            id=merchant_id,
            name=f"Merchant {merchant_id}",
            category="electronics",
            monthly_gmv=500000,
        )
        db.add(merchant)
        db.flush()
    return merchant


def _default_configs(intervention_type: str, treatment_config: dict | None):
    if intervention_type == "payment_method_config":
        control = {"payment_methods": "merchant_default"}
        treatment = treatment_config or {"payment_methods": {"card": False, "upi": True}}
    elif intervention_type == "offer_discount":
        control = {"offer": None}
        treatment = treatment_config or {"discount_pct": 0.05}
    elif intervention_type == "partial_payment":
        control = {"accept_partial": False}
        treatment = treatment_config or {
            "accept_partial": True,
            "first_min_partial_amount_pct": 0.25,
        }
    elif intervention_type == "expiry_config":
        control = {"expiry_hours": "merchant_default"}
        treatment = treatment_config or {"expiry_hours": 4}
    else:  # pragma: no cover
        raise AssertionError(f"unexpected type {intervention_type}")
    return control, treatment


def create_experiment(
    db,
    *,
    intervention_type: str = "payment_method_config",
    status: str = "approved",
    treatment_config: dict | None = None,
    policy_decision: str | None = "APPROVE",
    merchant_id: str = "merchant_executor",
    decision_merchant_id: str | None = None,
    experiment_id: str | None = None,
) -> Experiment:
    merchant = _make_merchant(db, merchant_id)
    control, treatment = _default_configs(intervention_type, treatment_config)
    opp_resolution = f"opportunity_{experiment_id or merchant_id}_{intervention_type}"
    hyp_resolution = f"hypothesis_{experiment_id or merchant_id}_{intervention_type}"
    opportunity = Opportunity(
        id=opp_resolution,
        merchant_id=merchant.id,
        type="segment_conversion_divergence",
        segment="android_budget",
        severity=0.1,
        detected_metric="conversion_rate",
        detected_value=0.47,
        baseline_value=0.58,
        evidence={},
        status="detected",
    )
    hypothesis = Hypothesis(
        id=hyp_resolution,
        opportunity_id=opportunity.id,
        merchant_id=merchant.id,
        hypothesis_text="test hypothesis",
        intervention_type=intervention_type,
        intervention_params={},
        status="proposed",
        evidence_refs=[],
    )
    experiment = Experiment(
        id=experiment_id,
        merchant_id=merchant.id,
        hypothesis_id=hypothesis.id,
        opportunity_id=opportunity.id,
        name=f"exp-{intervention_type}",
        segment="android_budget",
        intervention_type=intervention_type,
        control_config=control,
        treatment_config=treatment,
        traffic_split_treatment_pct=0.10,
        primary_metric="conversion_rate",
        guardrail_metrics=[],
        min_sample_per_variant=50,
        max_duration_hours=72,
        status=status,
    )
    db.add(opportunity)
    db.flush()
    db.add(hypothesis)
    db.flush()
    db.add(experiment)
    db.flush()
    if policy_decision is not None:
        decision_merchant = merchant
        if decision_merchant_id is not None:
            decision_merchant = _make_merchant(db, decision_merchant_id)
        decision = PolicyDecision(
            experiment_id=experiment.id,
            merchant_id=decision_merchant.id,
            decision=policy_decision,
            violations=[],
            original_params={},
            final_params={},
            evaluated_at=datetime.now(timezone.utc),
        )
        db.add(decision)
        db.flush()
    return experiment


def _make_operation(db, experiment, operation_key, status, resource_id=None, hash_value="abc"):
    op = OperationExecution(
        operation_key=operation_key,
        operation_type="deploy_treatment",
        request_payload_hash=hash_value,
        status=status,
        razorpay_resource_id=resource_id,
        response_json=None,
    )
    db.add(op)
    db.flush()
    return op


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


def test_missing_experiment_raises(db_session):
    with pytest.raises(ExperimentExecutionError):
        deploy_experiment_treatment(
            db_session, "does-not-exist", razorpay_client=FakeRazorpayClient()
        )


def test_proposed_experiment_cannot_deploy(db_session):
    exp = create_experiment(db_session, status="proposed", policy_decision=None)
    fake = FakeRazorpayClient()
    with pytest.raises(ExperimentExecutionAuthorizationError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls == []


def test_rejected_experiment_cannot_deploy(db_session):
    exp = create_experiment(db_session, status="rejected", policy_decision="REJECT")
    fake = FakeRazorpayClient()
    with pytest.raises(ExperimentExecutionAuthorizationError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls == []


def test_approved_experiment_without_policy_decision_cannot_deploy(db_session):
    exp = create_experiment(db_session, status="approved", policy_decision=None)
    fake = FakeRazorpayClient()
    with pytest.raises(ExperimentExecutionAuthorizationError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls == []


def test_reject_policy_decision_cannot_deploy(db_session):
    exp = create_experiment(db_session, status="approved", policy_decision="REJECT")
    fake = FakeRazorpayClient()
    with pytest.raises(ExperimentExecutionAuthorizationError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls == []


def test_approve_policy_decision_permits_deploy(db_session):
    exp = create_experiment(db_session, status="approved", policy_decision="APPROVE")
    fake = FakeRazorpayClient()
    resource = deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert resource is not None
    assert len(fake.create_calls) == 1


def test_some_other_policy_decision_cannot_deploy(db_session):
    exp = create_experiment(db_session, status="approved", policy_decision="HOLD")
    fake = FakeRazorpayClient()
    with pytest.raises(ExperimentExecutionAuthorizationError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls == []


def test_policy_decision_merchant_mismatch_cannot_deploy(db_session):
    exp = create_experiment(
        db_session,
        status="approved",
        policy_decision="APPROVE",
        decision_merchant_id="other-merchant",
    )
    fake = FakeRazorpayClient()
    with pytest.raises(ExperimentExecutionAuthorizationError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls == []


def test_running_experiment_with_prior_approve_permits_deploy(db_session):
    exp = create_experiment(db_session, status="running", policy_decision="APPROVE")
    fake = FakeRazorpayClient()
    resource = deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert resource is not None
    assert len(fake.create_calls) == 1


# ---------------------------------------------------------------------------
# Payment method mapping
# ---------------------------------------------------------------------------


def test_payment_method_one_create_call(db_session):
    exp = create_experiment(db_session)
    fake = FakeRazorpayClient()
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert len(fake.create_calls) == 1


def test_payment_methods_mapped_correctly(db_session):
    exp = create_experiment(
        db_session,
        treatment_config={"payment_methods": {"card": False, "upi": True}},
    )
    fake = FakeRazorpayClient()
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls[0]["payment_methods"] == {"card": False, "upi": True}


def test_deterministic_reference_id(db_session):
    exp = create_experiment(db_session, experiment_id="exp-ref-123")
    fake = FakeRazorpayClient()
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls[0]["reference_id"] == "mra_exp-ref-123_treatment_v1"


def test_amount_is_fixed_10000_paise(db_session):
    exp = create_experiment(db_session)
    fake = FakeRazorpayClient()
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls[0]["amount"] == 10000


def test_notifications_disabled(db_session):
    exp = create_experiment(db_session)
    fake = FakeRazorpayClient()
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls[0]["notify"] == {"sms": False, "email": False}


def test_description_is_safe_and_non_causal(db_session):
    exp = create_experiment(db_session)
    fake = FakeRazorpayClient()
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls[0]["description"] == DESCRIPTION


def test_razorpay_resource_persisted(db_session):
    exp = create_experiment(db_session)
    fake = FakeRazorpayClient()
    resource = deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert db_session.query(RazorpayResource).count() == 1
    assert resource.razorpay_id == "plink_1"
    assert resource.variant == "treatment"
    assert resource.resource_type == "payment_link"
    assert resource.status == "active"
    assert len(fake.create_calls) == 1


# ---------------------------------------------------------------------------
# Partial payment / expiry / offer_discount mapping
# ---------------------------------------------------------------------------


def test_partial_payment_mapping_correct(db_session):
    exp = create_experiment(
        db_session,
        intervention_type="partial_payment",
        treatment_config={
            "accept_partial": True,
            "first_min_partial_amount_pct": 0.25,
        },
    )
    fake = FakeRazorpayClient()
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    call = fake.create_calls[0]
    assert call["accept_partial"] is True
    assert call["first_min_partial_amount"] == 2500


def test_partial_payment_percentage_converted_to_paise(db_session):
    exp = create_experiment(
        db_session,
        intervention_type="partial_payment",
        treatment_config={
            "accept_partial": True,
            "first_min_partial_amount_pct": 0.50,
        },
    )
    fake = FakeRazorpayClient()
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls[0]["first_min_partial_amount"] == 5000


def test_invalid_partial_semantic_config_rejects_before_network(db_session):
    exp = create_experiment(
        db_session,
        intervention_type="partial_payment",
        treatment_config={"accept_partial": False},
    )
    fake = FakeRazorpayClient()
    with pytest.raises(ExperimentExecutionConfigurationError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls == []


def test_expiry_timestamp_correct_from_injected_utc_time(db_session):
    now = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
    exp = create_experiment(
        db_session,
        intervention_type="expiry_config",
        treatment_config={"expiry_hours": 4},
    )
    fake = FakeRazorpayClient()
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake, now=now)
    expected = int(now.timestamp()) + 4 * 3600
    assert fake.create_calls[0]["expire_by"] == expected


def test_expiry_is_future_timestamp(db_session):
    now = datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc)
    expire_by = compute_expire_by(expiry_hours=4, now=now)
    assert expire_by > int(now.timestamp())


def test_invalid_expiry_rejects_before_network(db_session):
    exp = create_experiment(
        db_session,
        intervention_type="expiry_config",
        treatment_config={"expiry_hours": 0},
    )
    fake = FakeRazorpayClient()
    with pytest.raises(ExperimentExecutionConfigurationError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls == []


def test_offer_discount_fails_closed(db_session):
    exp = create_experiment(
        db_session,
        intervention_type="offer_discount",
        status="approved",
        policy_decision="APPROVE",
    )
    fake = FakeRazorpayClient()
    with pytest.raises(ExperimentExecutionConfigurationError, match="Offer mapping"):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.create_calls == []


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_second_successful_deploy_returns_same_resource(db_session):
    exp = create_experiment(db_session)
    first_client = FakeRazorpayClient()
    resource1 = deploy_experiment_treatment(db_session, exp.id, razorpay_client=first_client)
    second_client = FakeRazorpayClient()
    resource2 = deploy_experiment_treatment(db_session, exp.id, razorpay_client=second_client)
    assert resource2.id == resource1.id
    assert len(first_client.create_calls) == 1
    assert second_client.create_calls == []


def test_second_successful_deploy_makes_zero_extra_create_calls(db_session):
    exp = create_experiment(db_session)
    fake = FakeRazorpayClient()
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert len(fake.create_calls) == 1


def test_operation_row_succeeded(db_session):
    exp = create_experiment(db_session)
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=FakeRazorpayClient())
    op = (
        db_session.query(OperationExecution)
        .filter(OperationExecution.operation_key == f"experiment:{exp.id}:deploy:treatment:v1")
        .one()
    )
    assert op.status == "succeeded"


def test_request_hash_stored(db_session):
    exp = create_experiment(db_session)
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=FakeRazorpayClient())
    op = db_session.query(OperationExecution).one()
    assert op.request_payload_hash
    assert len(op.request_payload_hash) == 64


def test_succeeded_operation_missing_resource_fails_closed(db_session):
    exp = create_experiment(db_session)
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=FakeRazorpayClient())
    resource = (
        db_session.query(RazorpayResource)
        .filter_by(experiment_id=exp.id, resource_type="payment_link", variant="treatment")
        .one()
    )
    db_session.delete(resource)
    db_session.flush()
    with pytest.raises(ExperimentExecutionStateError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=FakeRazorpayClient())


# ---------------------------------------------------------------------------
# Errors / ambiguous failures
# ---------------------------------------------------------------------------


def test_400_clear_api_failure_marks_failed(db_session):
    exp = create_experiment(db_session)
    fake = FakeRazorpayClient(
        create_error=RazorpayBadRequestError("bad request", status_code=400)
    )
    with pytest.raises(RazorpayBadRequestError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    op = db_session.query(OperationExecution).one()
    assert op.status == "failed"
    assert op.response_json == {"error": "definitive_api_failure", "status_code": 400}
    assert len(fake.create_calls) == 1


def test_auth_failure_marks_failed(db_session):
    exp = create_experiment(db_session)
    fake = FakeRazorpayClient(
        create_error=RazorpayAuthenticationError("unauthorized", status_code=401)
    )
    with pytest.raises(RazorpayAuthenticationError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    op = db_session.query(OperationExecution).one()
    assert op.status == "failed"
    assert op.response_json == {"error": "definitive_api_failure", "status_code": 401}


def test_network_timeout_remains_ambiguous_pending(db_session):
    exp = create_experiment(db_session)
    fake = FakeRazorpayClient(create_error=httpx.TimeoutException("timed out"))
    with pytest.raises(httpx.TimeoutException):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    op = db_session.query(OperationExecution).one()
    assert op.status == "pending"
    assert op.response_json == {"error": "ambiguous_network_failure"}


def test_5xx_remains_ambiguous_pending(db_session):
    exp = create_experiment(db_session)
    fake = FakeRazorpayClient(
        create_error=RazorpayServerError("upstream down", status_code=500)
    )
    with pytest.raises(RazorpayServerError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    op = db_session.query(OperationExecution).one()
    assert op.status == "pending"
    assert op.response_json == {"error": "ambiguous_network_failure"}


def test_ambiguous_retry_makes_no_second_create_call(db_session):
    exp = create_experiment(db_session)
    fake = FakeRazorpayClient(create_error=httpx.TimeoutException("timed out"))
    with pytest.raises(httpx.TimeoutException):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert len(fake.create_calls) == 1
    with pytest.raises(IdempotencyInProgressError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert len(fake.create_calls) == 1


def test_no_secrets_stored(db_session):
    secret = "super_secret_do_not_leak_42"
    exp = create_experiment(db_session)
    fake = FakeRazorpayClient(
        create_error=RazorpayBadRequestError(f"bad {secret}", status_code=400)
    )
    with pytest.raises(RazorpayBadRequestError):
        deploy_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    op = db_session.query(OperationExecution).one()
    assert secret not in str(op.response_json)
    assert secret not in op.request_payload_hash


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------


def _add_deployed_resource(db, experiment_id: str, razorpay_id: str = "plink_rollback"):
    resource = RazorpayResource(
        experiment_id=experiment_id,
        variant="treatment",
        resource_type="payment_link",
        razorpay_id=razorpay_id,
        config={},
        status="active",
    )
    db.add(resource)
    db.flush()
    return resource


def _add_result(db, experiment_id: str, decision: str) -> ExperimentResult:
    result = ExperimentResult(
        experiment_id=experiment_id,
        decision=decision,
        control_count=100,
        treatment_count=100,
        control_conversions=40,
        treatment_conversions=50,
    )
    db.add(result)
    db.flush()
    return result


def test_rollback_result_permits_cancel(db_session):
    exp = create_experiment(db_session)
    resource = _add_deployed_resource(db_session, exp.id)
    _add_result(db_session, exp.id, "ROLLBACK")
    fake = FakeRazorpayClient()
    cancelled = rollback_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert cancelled is resource
    assert resource.status == "cancelled"
    assert fake.cancel_calls == [resource.razorpay_id]


def test_keep_decision_does_not_permit_cancel(db_session):
    exp = create_experiment(db_session)
    resource = _add_deployed_resource(db_session, exp.id)
    _add_result(db_session, exp.id, "KEEP")
    fake = FakeRazorpayClient()
    with pytest.raises(ExperimentExecutionAuthorizationError):
        rollback_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.cancel_calls == []
    assert resource.status == "active"


def test_inconclusive_decision_does_not_permit_cancel(db_session):
    exp = create_experiment(db_session)
    resource = _add_deployed_resource(db_session, exp.id)
    _add_result(db_session, exp.id, "INCONCLUSIVE")
    fake = FakeRazorpayClient()
    with pytest.raises(ExperimentExecutionAuthorizationError):
        rollback_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert fake.cancel_calls == []
    assert resource.status == "active"


def test_second_rollback_does_not_cancel_twice(db_session):
    exp = create_experiment(db_session)
    _add_deployed_resource(db_session, exp.id)
    _add_result(db_session, exp.id, "ROLLBACK")
    first_client = FakeRazorpayClient()
    cancelled = rollback_experiment_treatment(db_session, exp.id, razorpay_client=first_client)
    assert cancelled.status == "cancelled"

    second_client = FakeRazorpayClient()
    again = rollback_experiment_treatment(db_session, exp.id, razorpay_client=second_client)
    assert again is cancelled
    assert len(first_client.cancel_calls) == 1
    assert second_client.cancel_calls == []


def test_rollback_operation_idempotency_persisted(db_session):
    exp = create_experiment(db_session)
    _add_deployed_resource(db_session, exp.id)
    _add_result(db_session, exp.id, "ROLLBACK")
    rollback_experiment_treatment(db_session, exp.id, razorpay_client=FakeRazorpayClient())
    op = (
        db_session.query(OperationExecution)
        .filter(OperationExecution.operation_key == f"experiment:{exp.id}:rollback:treatment:v1")
        .one()
    )
    assert op.status == "succeeded"


def test_rollback_without_resource_returns_none(db_session):
    exp = create_experiment(db_session)
    _add_result(db_session, exp.id, "ROLLBACK")
    fake = FakeRazorpayClient()
    result = rollback_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert result is None
    assert fake.cancel_calls == []


# ---------------------------------------------------------------------------
# Full chain
# ---------------------------------------------------------------------------


def test_full_chain_seed_to_deploy(db_session):
    db = db_session
    merchant = Merchant(
        id=TECHBAZAAR_PROFILE.merchant_id,
        name=TECHBAZAAR_PROFILE.name,
        category=TECHBAZAAR_PROFILE.category,
        monthly_gmv=TECHBAZAAR_PROFILE.monthly_gmv_paise,
    )
    db.add(merchant)
    db.add(
        MerchantPolicy(
            id="policy_techbazaar",
            merchant_id=merchant.id,
            max_experiment_exposure_pct=0.10,
            max_discount_pct=0.15,
            min_margin_pct=0.05,
            max_concurrent_experiments=3,
            max_experiment_duration_hours=168,
            min_sample_size=30,
            max_financial_exposure=50000,
            allowed_interventions=list(ALL_INTERVENTIONS),
        )
    )
    db.flush()

    events = generate_baseline_events(
        profile=TECHBAZAAR_PROFILE, seed=20260827, days=30
    )
    db.add_all(
        [
            PaymentAttempt(
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
            for evt in events
        ]
    )
    db.flush()

    opportunities = run_opportunity_detection(
        db, merchant.id, min_segment_attempts=100, min_absolute_gap=0.08, max_results=1
    )
    assert opportunities, "seeded baseline should produce an opportunity"
    opportunity = opportunities[0]

    hypothesis = diagnose_opportunity(
        db, opportunity.id, client=FakeOpenAIClient(payload=MOCK_MODEL_RESPONSE)
    )
    assert hypothesis.intervention_type == "payment_method_config"

    experiment = plan_experiment(db, hypothesis.id)
    decision = evaluate_experiment_policy(db, experiment.id)
    assert decision.decision == "APPROVE"

    client = FakeRazorpayClient()
    resource = deploy_experiment_treatment(db, experiment.id, razorpay_client=client)
    assert resource.experiment_id == experiment.id
    assert resource.variant == "treatment"
    assert resource.resource_type == "payment_link"
    assert len(client.create_calls) == 1
    assert db_session.query(RazorpayResource).count() == 1


def test_rollback_chain_deployed_to_cancelled(db_session):
    exp = create_experiment(db_session)
    deploy_experiment_treatment(db_session, exp.id, razorpay_client=FakeRazorpayClient())
    _add_result(db_session, exp.id, "ROLLBACK")
    fake = FakeRazorpayClient()
    resource = rollback_experiment_treatment(db_session, exp.id, razorpay_client=fake)
    assert resource is not None
    assert resource.status == "cancelled"
    assert fake.cancel_calls == [resource.razorpay_id]


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("path", [EXECUTOR_PATH, IDEMPOTENCY_PATH])
def test_no_openai_causal_simulation_statistics_or_policy_rerun(path):
    source = path.read_text(encoding="utf-8")
    modules = _imported_modules(path)
    forbidden_roots = ("openai", "causal", "simulation", "statistics")
    assert not any(root in mod for mod in modules for root in forbidden_roots), modules
    assert "evaluate_experiment_policy" not in source
    assert ".run_experiment_batch" not in source
    assert ".commit(" not in source


@pytest.mark.parametrize("path", [EXECUTOR_PATH, IDEMPOTENCY_PATH])
def test_no_commit_calls(path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "commit"
