"""Tests for Task 15: deterministic Autopilot orchestration service.

Everything runs offline. The two external boundaries reuse the fakes already
established by the Task 08/13 suites (fake OpenAI structured output, fake
Razorpay client); no live API, no API key, no network access.

Service requirements covered, in order:

1.  no opportunity -> detection step
2.  opportunity without hypothesis -> diagnosis step
3.  hypothesis without experiment -> planning step
4.  proposed experiment -> policy step
5.  rejected experiment -> visible POLICY_REJECTED stop (no invented replan)
6.  approved experiment -> real resource deployment
7.  offer_discount -> safe DEPLOYMENT_BLOCKED (Task 13 stays fail-closed)
8.  running experiment with remaining samples -> one runtime batch
9.  sample target reached -> statistical evaluation
10. ROLLBACK result -> resource rollback
11. KEEP result -> terminal COMPLETED
12. INCONCLUSIVE result -> terminal COMPLETED
13. one call performs at most one major transition
14. repeated calls are idempotent where the domain layer is idempotent
15. merchant isolation
16. missing merchant raises a clear service error
17. no policy math duplicated
18. no statistics duplicated
19. no direct Razorpay API calls
20. no hidden causal model import

Plus Task 17A's read models: overview exposes the Task 07 segment and
payment-method breakdowns, and ``get_autopilot_cycle`` rebuilds one
opportunity's complete persisted lifecycle read-only (every partial stage
included), never committing or mutating anything.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.api import schemas
from app.db.base import Base
from app.db.models import (
    AuditEvent,
    Experiment,
    ExperimentResult,
    Hypothesis,
    Merchant,
    MerchantPolicy,
    Opportunity,
    PaymentAttempt,
    PolicyDecision,
    RazorpayResource,
)
from app.engines.metrics import get_payment_method_metrics, get_segment_metrics
from app.services import autopilot
from app.services.audit import record_audit_event, verify_merchant_audit_chain
from app.services.autopilot import (
    ACTION_BLOCKED,
    ACTION_DEPLOY,
    ACTION_DIAGNOSE,
    ACTION_DONE,
    ACTION_EVALUATE,
    ACTION_PLAN,
    ACTION_POLICY,
    ACTION_ROLLBACK,
    ACTION_RUN_BATCH,
    ACTION_STOP,
    ExperimentNotFoundError,
    HypothesisNotFoundError,
    InvalidTransitionError,
    MerchantNotFoundError,
    OpportunityNotFoundError,
    STEP_COMPLETED,
    STEP_DEPLOYMENT_BLOCKED,
    STEP_EXPERIMENT_BATCH_RUN,
    STEP_EXPERIMENT_EVALUATED,
    STEP_EXPERIMENT_PLANNED,
    STEP_HYPOTHESIS_PROPOSED,
    STEP_NO_ACTION,
    STEP_OPPORTUNITY_DETECTED,
    STEP_POLICY_APPROVED,
    STEP_POLICY_REJECTED,
    STEP_RESOURCE_DEPLOYED,
    STEP_RESOURCE_ROLLED_BACK,
    advance_autopilot,
    autopilot_status,
    overview,
    resolve_transition,
)
from app.simulation.generator import generate_baseline_events
from app.simulation.merchant import TECHBAZAAR_PROFILE
from tests.test_diagnosis_engine import MOCK_MODEL_RESPONSE, FakeOpenAIClient, make_evidence
from tests.test_experiment_executor import FakeRazorpayClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
SERVICE_PATH = BACKEND_DIR / "app" / "services" / "autopilot.py"
ROUTER_PATH = BACKEND_DIR / "app" / "api" / "router.py"
SCHEMAS_PATH = BACKEND_DIR / "app" / "api" / "schemas.py"

MERCHANT = TECHBAZAAR_PROFILE.merchant_id
OTHER_MERCHANT = "merchant_other_shoes"
SEGMENT = "android_budget"
ALL_INTERVENTIONS = [
    "payment_method_config",
    "offer_discount",
    "partial_payment",
    "expiry_config",
]
#: Canonical semantic configs, identical to what the Task 09 planner emits.
CANONICAL_CONFIGS = {
    "payment_method_config": (
        {"payment_methods": "merchant_default"},
        {"payment_methods": {"card": False, "upi": True}},
    ),
    "offer_discount": ({"offer": None}, {"discount_pct": 0.10}),
    "partial_payment": (
        {"accept_partial": False},
        {"accept_partial": True, "first_min_partial_amount_pct": 0.25},
    ),
    "expiry_config": ({"expiry_hours": "merchant_default"}, {"expiry_hours": 48}),
}
HYPOTHESIS_PARAMS = {
    "payment_method_config": {"card": False, "upi": True},
    "offer_discount": {"discount_pct": 0.10},
    "partial_payment": {"accept_partial": True, "first_min_partial_amount_pct": 0.25},
    "expiry_config": {"expiry_hours": 48},
}

_BASE_TIME = datetime(2026, 8, 1, tzinfo=timezone.utc)
_resource_sequence = iter(range(1, 10_000))


# ---------------------------------------------------------------------------
# Fixtures and factories
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session(tmp_path):
    db_file = tmp_path / "test_autopilot.db"
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


def make_merchant(
    db: Session,
    merchant_id: str = MERCHANT,
    *,
    min_sample_size: int = 10,
    exposure_cap: float = 0.50,
    allowed_interventions: list[str] | None = None,
) -> Merchant:
    merchant = Merchant(
        id=merchant_id,
        name=f"Merchant {merchant_id}",
        category="consumer_electronics",
        monthly_gmv=500_000_000,
    )
    db.add(merchant)
    db.add(
        MerchantPolicy(
            merchant_id=merchant_id,
            max_experiment_exposure_pct=exposure_cap,
            max_discount_pct=0.15,
            min_margin_pct=0.05,
            max_concurrent_experiments=3,
            max_experiment_duration_hours=168,
            min_sample_size=min_sample_size,
            max_financial_exposure=50_000,
            allowed_interventions=(
                list(ALL_INTERVENTIONS)
                if allowed_interventions is None
                else list(allowed_interventions)
            ),
        )
    )
    db.flush()
    return merchant


def seed_baseline(db: Session, *, days: int = 7) -> int:
    """Deterministic observable history, so the detector has real data."""
    events = generate_baseline_events(
        profile=TECHBAZAAR_PROFILE, seed=20260827, days=days
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
    return len(events)


def make_opportunity(
    db: Session, merchant_id: str = MERCHANT, **overrides
) -> Opportunity:
    payload: dict = {
        "merchant_id": merchant_id,
        "type": "segment_conversion_divergence",
        "segment": SEGMENT,
        "severity": 0.07,
        "detected_metric": "conversion_rate",
        "detected_value": 0.472,
        "baseline_value": 0.586,
        "evidence": make_evidence(),
        "status": "detected",
        "created_at": _BASE_TIME,
    }
    payload.update(overrides)
    opportunity = Opportunity(**payload)
    db.add(opportunity)
    db.flush()
    return opportunity


def make_hypothesis(
    db: Session,
    opportunity: Opportunity,
    *,
    intervention_type: str = "payment_method_config",
    status: str = "proposed",
) -> Hypothesis:
    hypothesis = Hypothesis(
        opportunity_id=opportunity.id,
        merchant_id=opportunity.merchant_id,
        ai_model="gpt-4.1-mini",
        hypothesis_text="Checkout completion improves when payment methods are tuned.",
        intervention_type=intervention_type,
        intervention_params=dict(HYPOTHESIS_PARAMS[intervention_type]),
        confidence="medium",
        reasoning_summary="Observable segment gap plus payment-method variation.",
        evidence_refs=["segment_conversion_rate", "comparison_conversion_rate"],
        status=status,
    )
    db.add(hypothesis)
    db.flush()
    return hypothesis


def make_experiment(
    db: Session,
    *,
    opportunity: Opportunity | None = None,
    hypothesis: Hypothesis | None = None,
    intervention_type: str | None = None,
    status: str = "proposed",
    min_sample: int = 10,
    traffic: float = 0.5,
    treatment_config: dict | None = None,
) -> Experiment:
    if hypothesis is None:
        if opportunity is None:
            opportunity = make_opportunity(db)
        hypothesis = make_hypothesis(
            db,
            opportunity,
            intervention_type=intervention_type or "payment_method_config",
        )
    if opportunity is None:
        opportunity = db.get(Opportunity, hypothesis.opportunity_id)
    control, treatment = CANONICAL_CONFIGS[hypothesis.intervention_type]
    experiment = Experiment(
        merchant_id=hypothesis.merchant_id,
        hypothesis_id=hypothesis.id,
        opportunity_id=opportunity.id,
        name=f"{opportunity.segment}-{hypothesis.intervention_type}",
        segment=opportunity.segment,
        intervention_type=hypothesis.intervention_type,
        control_config=dict(control),
        treatment_config=dict(treatment_config or treatment),
        traffic_split_treatment_pct=traffic,
        primary_metric="conversion_rate",
        guardrail_metrics=["captured_gmv"],
        min_sample_per_variant=min_sample,
        max_duration_hours=72,
        status=status,
    )
    db.add(experiment)
    db.flush()
    return experiment


def add_policy_decision(
    db: Session,
    experiment: Experiment,
    *,
    decision: str = "APPROVE",
    violations: list[str] | None = None,
) -> PolicyDecision:
    row = PolicyDecision(
        experiment_id=experiment.id,
        merchant_id=experiment.merchant_id,
        decision=decision,
        violations=list(violations or []),
        original_params={},
        final_params={} if decision == "APPROVE" else None,
        evaluated_at=_BASE_TIME + timedelta(hours=1),
    )
    db.add(row)
    db.flush()
    return row


def add_result(
    db: Session,
    experiment: Experiment,
    *,
    decision: str,
    samples: int = 10,
) -> ExperimentResult:
    row = ExperimentResult(
        experiment_id=experiment.id,
        control_count=samples,
        treatment_count=samples,
        control_conversions=5,
        treatment_conversions=8 if decision == "KEEP" else 2,
        control_rate=0.5,
        treatment_rate=0.8 if decision == "KEEP" else 0.2,
        absolute_lift=0.3 if decision == "KEEP" else -0.3,
        relative_lift=0.6,
        p_value=0.01 if decision != "INCONCLUSIVE" else 0.9,
        confidence_interval_lower=0.0,
        confidence_interval_upper=0.6,
        is_significant=decision != "INCONCLUSIVE",
        decision=decision,
        decided_at=_BASE_TIME + timedelta(hours=2),
    )
    db.add(row)
    db.flush()
    return row


def add_resource(
    db: Session, experiment: Experiment, *, status: str = "active"
) -> RazorpayResource:
    resource = RazorpayResource(
        experiment_id=experiment.id,
        variant="treatment",
        resource_type="payment_link",
        razorpay_id=f"plink_test_{next(_resource_sequence)}",
        config={},
        status=status,
    )
    db.add(resource)
    db.flush()
    return resource


def add_attempts(
    db: Session, experiment: Experiment, *, variant: str, count: int, captured: int
) -> None:
    for index in range(count):
        db.add(
            PaymentAttempt(
                id=f"craft_{experiment.id}_{variant}_{index}",
                merchant_id=experiment.merchant_id,
                customer_ref=f"craft_{variant}_{index}",
                amount=10_000,
                currency="INR",
                payment_method="upi",
                status="captured" if index < captured else "failed",
                segment=experiment.segment,
                experiment_id=experiment.id,
                variant=variant,
            )
        )
    db.flush()


def fake_openai() -> FakeOpenAIClient:
    return FakeOpenAIClient(payload=MOCK_MODEL_RESPONSE)


def persisted(db: Session, experiment: Experiment) -> Experiment:
    db.flush()
    db.expire_all()
    return db.get(Experiment, experiment.id)


# ---------------------------------------------------------------------------
# 1-3. Detection, diagnosis, planning
# ---------------------------------------------------------------------------


def test_missing_opportunity_runs_detection(db_session):
    make_merchant(db_session)
    assert seed_baseline(db_session) > 0
    assert db_session.query(Opportunity).count() == 0

    step = advance_autopilot(db_session, MERCHANT)

    assert step.step == STEP_OPPORTUNITY_DETECTED
    assert step.merchant_id == MERCHANT
    assert step.entity_type == "opportunity"
    assert step.status == autopilot.STATE_HYPOTHESIS_PENDING
    assert step.next_action == ACTION_DIAGNOSE
    opportunity = db_session.get(Opportunity, step.entity_id)
    assert opportunity is not None
    assert opportunity.merchant_id == MERCHANT
    # Merchant-visible explanation only: no invented money, no causal claim.
    assert "revenue lost" not in step.message.lower()
    assert "hidden" not in step.message.lower()


def test_detection_without_candidates_is_no_action(db_session):
    make_merchant(db_session)

    step = advance_autopilot(db_session, MERCHANT)

    assert step.step == STEP_NO_ACTION
    assert step.status == autopilot.STATE_IDLE
    assert step.next_action == autopilot.ACTION_DETECT
    assert db_session.query(Opportunity).count() == 0
    assert db_session.query(AuditEvent).count() == 0


def test_opportunity_without_hypothesis_runs_diagnosis(db_session):
    make_merchant(db_session)
    opportunity = make_opportunity(db_session)

    step = advance_autopilot(db_session, MERCHANT, openai_client=fake_openai())

    assert step.step == STEP_HYPOTHESIS_PROPOSED
    assert step.entity_type == "hypothesis"
    assert step.next_action == ACTION_PLAN
    hypothesis = db_session.get(Hypothesis, step.entity_id)
    assert hypothesis is not None
    assert hypothesis.opportunity_id == opportunity.id
    assert hypothesis.intervention_type == "payment_method_config"


def test_hypothesis_without_experiment_runs_planner(db_session):
    make_merchant(db_session)
    opportunity = make_opportunity(db_session)
    hypothesis = make_hypothesis(db_session, opportunity)

    step = advance_autopilot(db_session, MERCHANT)

    assert step.step == STEP_EXPERIMENT_PLANNED
    assert step.entity_type == "experiment"
    assert step.status == autopilot.STATE_POLICY_REVIEW_PENDING
    assert step.next_action == ACTION_POLICY
    experiment = db_session.get(Experiment, step.entity_id)
    assert experiment is not None
    assert experiment.hypothesis_id == hypothesis.id
    assert experiment.status == "proposed"


# ---------------------------------------------------------------------------
# 4-5. Policy
# ---------------------------------------------------------------------------


def test_proposed_experiment_runs_policy(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session)

    step = advance_autopilot(db_session, MERCHANT)

    assert step.step == STEP_POLICY_APPROVED
    assert step.entity_id == experiment.id
    assert step.status == autopilot.STATE_DEPLOYMENT_PENDING
    assert step.next_action == ACTION_DEPLOY
    assert persisted(db_session, experiment).status == "approved"
    assert db_session.query(PolicyDecision).filter_by(decision="APPROVE").count() == 1


def test_rejected_experiment_stops_at_policy_rejected(db_session):
    make_merchant(db_session, exposure_cap=0.02)
    experiment = make_experiment(
        db_session,
        intervention_type="offer_discount",
        treatment_config={"discount_pct": 0.30},
        traffic=0.5,
    )

    step = advance_autopilot(db_session, MERCHANT)

    assert step.step == STEP_POLICY_REJECTED
    assert step.status == autopilot.STATE_POLICY_REJECTED
    assert step.next_action == ACTION_STOP
    assert "DISCOUNT_LIMIT_EXCEEDED" in step.message
    assert persisted(db_session, experiment).status == "rejected"

    # No silent re-planning in P0: the rejection simply stays visible.
    again = advance_autopilot(db_session, MERCHANT)
    assert again.step == STEP_POLICY_REJECTED
    assert again.entity_id == experiment.id
    assert db_session.query(Hypothesis).count() == 1
    assert db_session.query(Experiment).count() == 1


def test_policy_rejection_never_reaches_deployment(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="rejected")
    add_policy_decision(
        db_session, experiment, decision="REJECT", violations=["DURATION_EXCEEDED"]
    )
    client = FakeRazorpayClient()

    step = advance_autopilot(db_session, MERCHANT, razorpay_client=client)

    assert step.step == STEP_POLICY_REJECTED
    assert step.next_action == ACTION_STOP
    assert client.create_calls == []
    assert db_session.query(RazorpayResource).count() == 0


# ---------------------------------------------------------------------------
# 6-7. Deployment
# ---------------------------------------------------------------------------


def test_approved_experiment_deploys_real_resource(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="approved")
    add_policy_decision(db_session, experiment, decision="APPROVE")
    client = FakeRazorpayClient()

    step = advance_autopilot(db_session, MERCHANT, razorpay_client=client)

    assert step.step == STEP_RESOURCE_DEPLOYED
    assert step.status == autopilot.STATE_RUNNING
    assert step.next_action == ACTION_RUN_BATCH
    assert len(client.create_calls) == 1
    resource = db_session.query(RazorpayResource).one()
    assert resource.status == "active"
    assert resource.razorpay_id in step.message


def test_offer_discount_deployment_is_blocked_not_fatal(db_session):
    make_merchant(db_session)
    experiment = make_experiment(
        db_session, intervention_type="offer_discount", status="approved"
    )
    add_policy_decision(db_session, experiment, decision="APPROVE")
    client = FakeRazorpayClient()

    step = advance_autopilot(db_session, MERCHANT, razorpay_client=client)

    # Task 13 fails closed and is not weakened: no Offer id is invented.
    assert step.step == STEP_DEPLOYMENT_BLOCKED
    assert step.status == autopilot.STATE_DEPLOYMENT_BLOCKED
    assert step.next_action == ACTION_BLOCKED
    assert "offer" in step.message.lower()
    assert client.create_calls == []
    assert db_session.query(RazorpayResource).count() == 0
    # Blocked is a stable state, not a one-off crash.
    assert (
        advance_autopilot(db_session, MERCHANT, razorpay_client=client).step
        == STEP_DEPLOYMENT_BLOCKED
    )
    assert client.create_calls == []


# ---------------------------------------------------------------------------
# 8-9. Runtime and evaluation
# ---------------------------------------------------------------------------


def test_running_experiment_with_remaining_samples_runs_one_batch(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="running", min_sample=200)
    add_policy_decision(db_session, experiment, decision="APPROVE")
    add_resource(db_session, experiment)
    add_attempts(db_session, experiment, variant="control", count=10, captured=5)
    add_attempts(db_session, experiment, variant="treatment", count=2, captured=1)
    before = sum(autopilot.variant_counts(db_session, experiment.id))

    step = advance_autopilot(db_session, MERCHANT, runtime_batch_size=50)

    assert step.step == STEP_EXPERIMENT_BATCH_RUN
    control, treatment = autopilot.variant_counts(db_session, experiment.id)
    # Exactly one batch ran, and the horizon is still out of reach.
    assert control + treatment == before + 50
    assert step.status == autopilot.STATE_RUNNING
    assert step.next_action == ACTION_RUN_BATCH


def test_batch_that_completes_the_horizon_moves_to_evaluation(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="running", min_sample=10)
    add_policy_decision(db_session, experiment, decision="APPROVE")
    add_resource(db_session, experiment)
    add_attempts(db_session, experiment, variant="control", count=10, captured=5)
    add_attempts(db_session, experiment, variant="treatment", count=2, captured=1)

    step = advance_autopilot(db_session, MERCHANT, runtime_batch_size=500)

    assert step.step == STEP_EXPERIMENT_BATCH_RUN
    control, treatment = autopilot.variant_counts(db_session, experiment.id)
    assert control >= 10 and treatment >= 10
    # A batch that closes the horizon reports the follow-up it just unlocked.
    assert step.status == autopilot.STATE_EVALUATION_PENDING
    assert step.next_action == ACTION_EVALUATE


def test_sample_target_reached_runs_statistics(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="running", min_sample=10)
    add_policy_decision(db_session, experiment, decision="APPROVE")
    add_resource(db_session, experiment)
    add_attempts(db_session, experiment, variant="control", count=10, captured=3)
    add_attempts(db_session, experiment, variant="treatment", count=10, captured=8)

    step = advance_autopilot(db_session, MERCHANT)

    assert step.step == STEP_EXPERIMENT_EVALUATED
    result = db_session.query(ExperimentResult).one()
    assert result.decision == "KEEP"
    assert "KEEP" in step.message
    assert persisted(db_session, experiment).status == "completed"


# ---------------------------------------------------------------------------
# 10-12. Decisions
# ---------------------------------------------------------------------------


def test_rollback_decision_cancels_active_resource(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="completed", min_sample=10)
    resource = add_resource(db_session, experiment)
    add_result(db_session, experiment, decision="ROLLBACK")
    client = FakeRazorpayClient()

    step = advance_autopilot(db_session, MERCHANT, razorpay_client=client)

    assert step.step == STEP_RESOURCE_ROLLED_BACK
    assert step.status == autopilot.STATE_COMPLETED
    assert step.next_action == ACTION_DONE
    assert client.cancel_calls == [resource.razorpay_id]
    db_session.expire_all()
    assert db_session.get(RazorpayResource, resource.id).status == "cancelled"


def test_rollback_decision_without_resource_completes(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="completed", min_sample=10)
    add_result(db_session, experiment, decision="ROLLBACK")
    client = FakeRazorpayClient()

    step = advance_autopilot(db_session, MERCHANT, razorpay_client=client)

    assert step.step == STEP_COMPLETED
    assert "no treatment resource was deployed" in step.message
    assert client.cancel_calls == []


def test_keep_decision_is_terminal_without_cancellation(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="completed", min_sample=10)
    resource = add_resource(db_session, experiment)
    add_result(db_session, experiment, decision="KEEP")
    client = FakeRazorpayClient()

    step = advance_autopilot(db_session, MERCHANT, razorpay_client=client)

    assert step.step == STEP_COMPLETED
    assert step.status == autopilot.STATE_COMPLETED
    assert step.next_action == ACTION_DONE
    assert client.cancel_calls == []
    db_session.expire_all()
    assert db_session.get(RazorpayResource, resource.id).status == "active"


def test_inconclusive_decision_is_terminal(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="completed", min_sample=10)
    add_resource(db_session, experiment)
    add_result(db_session, experiment, decision="INCONCLUSIVE")
    client = FakeRazorpayClient()

    step = advance_autopilot(db_session, MERCHANT, razorpay_client=client)

    assert step.step == STEP_COMPLETED
    assert "INCONCLUSIVE" in step.message
    assert client.cancel_calls == []
    assert db_session.query(ExperimentResult).count() == 1


# ---------------------------------------------------------------------------
# 13-14. One transition per call, idempotency
# ---------------------------------------------------------------------------


def test_single_call_never_advances_two_transitions(db_session):
    make_merchant(db_session)
    seed_baseline(db_session)

    seen: list[str] = []
    for _ in range(6):
        step = advance_autopilot(
            db_session,
            MERCHANT,
            openai_client=fake_openai(),
            razorpay_client=FakeRazorpayClient(),
        )
        seen.append(step.step)
        db_session.expire_all()
        if step.step == STEP_OPPORTUNITY_DETECTED:
            assert db_session.query(Opportunity).count() >= 1
            assert db_session.query(Hypothesis).count() == 0
        elif step.step == STEP_HYPOTHESIS_PROPOSED:
            assert db_session.query(Hypothesis).count() == 1
            assert db_session.query(Experiment).count() == 0
        elif step.step == STEP_EXPERIMENT_PLANNED:
            assert db_session.query(Experiment).count() == 1
            assert db_session.query(PolicyDecision).count() == 0
        elif step.step == STEP_POLICY_APPROVED:
            assert db_session.query(RazorpayResource).count() == 0

    assert seen[:4] == [
        STEP_OPPORTUNITY_DETECTED,
        STEP_HYPOTHESIS_PROPOSED,
        STEP_EXPERIMENT_PLANNED,
        STEP_POLICY_APPROVED,
    ]
    assert seen[4] in {STEP_RESOURCE_DEPLOYED, STEP_DEPLOYMENT_BLOCKED}


def test_terminal_step_is_repeatable_and_writes_nothing(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="completed", min_sample=10)
    add_result(db_session, experiment, decision="KEEP")
    events_before = db_session.query(AuditEvent).count()
    attempts_before = db_session.query(PaymentAttempt).count()

    first = advance_autopilot(db_session, MERCHANT)
    second = advance_autopilot(db_session, MERCHANT)

    assert first == second
    assert first.step == STEP_COMPLETED
    assert db_session.query(AuditEvent).count() == events_before
    assert db_session.query(PaymentAttempt).count() == attempts_before


def test_repeated_detection_is_suppressed_by_the_detector(db_session):
    make_merchant(db_session)
    seed_baseline(db_session)

    first = autopilot.run_detection(db_session, MERCHANT)
    second = autopilot.run_detection(db_session, MERCHANT)

    assert first
    assert [row.id for row in second] == [row.id for row in first]
    assert db_session.query(Opportunity).count() == len(first)


def test_repeated_diagnosis_and_planning_return_the_same_entities(db_session):
    make_merchant(db_session)
    opportunity = make_opportunity(db_session)

    hypothesis = autopilot.diagnose(db_session, opportunity.id, client=fake_openai())
    again = autopilot.diagnose(db_session, opportunity.id, client=fake_openai())
    assert hypothesis.id == again.id
    assert db_session.query(Hypothesis).count() == 1

    experiment = autopilot.plan(db_session, hypothesis.id)
    again_plan = autopilot.plan(db_session, hypothesis.id)
    assert experiment.id == again_plan.id
    assert db_session.query(Experiment).count() == 1

    decision = autopilot.authorize_experiment(db_session, experiment.id)
    again_decision = autopilot.authorize_experiment(db_session, experiment.id)
    assert decision.id == again_decision.id
    assert db_session.query(PolicyDecision).count() == 1


# ---------------------------------------------------------------------------
# 15-16. Merchant isolation and clear errors
# ---------------------------------------------------------------------------


def test_merchant_isolation(db_session):
    make_merchant(db_session, MERCHANT)
    make_merchant(db_session, OTHER_MERCHANT)
    mine = make_opportunity(db_session, MERCHANT)
    theirs = make_opportunity(db_session, OTHER_MERCHANT, severity=0.9)
    their_experiment = make_experiment(
        db_session, opportunity=theirs, hypothesis=make_hypothesis(db_session, theirs)
    )
    add_policy_decision(db_session, their_experiment)
    db_session.flush()

    assert [row.id for row in autopilot.list_opportunities(db_session, MERCHANT)] == [
        mine.id
    ]
    assert [row.id for row in autopilot.list_opportunities(db_session, OTHER_MERCHANT)] == [
        theirs.id
    ]
    assert autopilot.focus_opportunity(db_session, MERCHANT).id == mine.id

    with pytest.raises(OpportunityNotFoundError):
        autopilot.get_opportunity(db_session, theirs.id, merchant_id=MERCHANT)
    with pytest.raises(ExperimentNotFoundError):
        autopilot.get_experiment(db_session, their_experiment.id, merchant_id=MERCHANT)

    # Advancing my merchant never borrows the other merchant's pipeline.
    step = advance_autopilot(db_session, MERCHANT, openai_client=fake_openai())
    assert step.merchant_id == MERCHANT
    assert step.entity_id != theirs.id
    assert step.step == STEP_HYPOTHESIS_PROPOSED
    assert db_session.get(Hypothesis, step.entity_id).merchant_id == MERCHANT


def test_opportunities_are_ordered_by_relevance_then_recency(db_session):
    make_merchant(db_session)
    older_low = make_opportunity(db_session, severity=0.05, created_at=_BASE_TIME)
    stale = make_opportunity(
        db_session,
        severity=0.05,
        segment="desktop_mid",
        created_at=_BASE_TIME - timedelta(days=1),
    )
    high = make_opportunity(
        db_session, severity=0.5, segment="ios_premium", created_at=_BASE_TIME
    )

    ordered = autopilot.list_opportunities(db_session, MERCHANT)

    assert [row.id for row in ordered] == [high.id, older_low.id, stale.id]


def test_missing_merchant_raises_clear_service_errors(db_session):
    with pytest.raises(MerchantNotFoundError, match="ghost_merchant"):
        advance_autopilot(db_session, "ghost_merchant")
    with pytest.raises(MerchantNotFoundError):
        overview(db_session, "ghost_merchant")
    with pytest.raises(MerchantNotFoundError):
        autopilot_status(db_session, "ghost_merchant")
    with pytest.raises(MerchantNotFoundError):
        autopilot.list_opportunities(db_session, "ghost_merchant")
    with pytest.raises(MerchantNotFoundError):
        autopilot.merchant_audit_history(db_session, "ghost_merchant")
    with pytest.raises(HypothesisNotFoundError):
        autopilot.plan(db_session, "no-such-hypothesis")


def test_invalid_transitions_are_explicit(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="proposed")

    with pytest.raises(InvalidTransitionError, match="proposed"):
        autopilot.run_batch(db_session, experiment.id, batch_size=10)

    hypothesis = db_session.get(Hypothesis, experiment.hypothesis_id)
    hypothesis.status = "dismissed"
    db_session.flush()
    with pytest.raises(InvalidTransitionError, match="dismissed"):
        autopilot.plan(db_session, hypothesis.id)


def test_missing_merchant_policy_is_reported_clearly(db_session):
    merchant = Merchant(id="merchant_without_policy", name="No policy")
    db_session.add(merchant)
    opportunity = make_opportunity(db_session, merchant.id)
    make_experiment(db_session, opportunity=opportunity)
    experiment = db_session.query(Experiment).one()

    with pytest.raises(autopilot.MerchantPolicyNotConfiguredError):
        autopilot.authorize_experiment(db_session, experiment.id)


# ---------------------------------------------------------------------------
# Aggregate read models
# ---------------------------------------------------------------------------


def test_status_tracks_the_lifecycle(db_session):
    make_merchant(db_session)

    idle = autopilot_status(db_session, MERCHANT)
    assert idle["state"] == autopilot.STATE_IDLE
    assert idle["next_action"] == autopilot.ACTION_DETECT
    assert idle["opportunity_count"] == 0
    assert idle["experiment_count"] == 0
    assert idle["latest_experiment_id"] is None
    assert idle["audit_chain_valid"] is True
    assert idle["progress"] is None

    make_opportunity(db_session)
    diagnosing = autopilot_status(db_session, MERCHANT)
    assert diagnosing["state"] == autopilot.STATE_HYPOTHESIS_PENDING
    assert diagnosing["next_action"] == ACTION_DIAGNOSE
    assert diagnosing["latest_opportunity_id"] is not None
    assert diagnosing["progress"] is None

    hypothesis = make_hypothesis(
        db_session,
        make_opportunity(db_session, segment="ios_premium", severity=0.9),
    )
    experiment = make_experiment(
        db_session, hypothesis=hypothesis, status="completed"
    )
    add_result(db_session, experiment, decision="KEEP")
    completed = autopilot_status(db_session, MERCHANT)
    assert completed["latest_experiment_status"] == "completed"
    assert completed["latest_statistical_decision"] == "KEEP"
    assert completed["latest_decision"] is None
    assert completed["state"] == autopilot.STATE_COMPLETED
    assert completed["next_action"] == ACTION_DONE
    assert completed["experiment_count"] == 1
    assert completed["active_experiment_count"] == 0


def test_overview_reuses_the_metric_engine_and_invents_nothing(db_session):
    make_merchant(db_session)
    rows = seed_baseline(db_session)

    payload = overview(db_session, MERCHANT)

    assert payload["metrics"]["attempts"] == rows
    assert payload["attempted_gmv_paise"] > 0
    assert payload["captured_gmv_paise"] > 0
    assert payload["captured_gmv_paise"] <= payload["attempted_gmv_paise"]
    assert payload["audit_chain_valid"] is True
    assert payload["latest_experiment"] is None
    assert payload["merchant"]["merchant_id"] == MERCHANT
    text = str(payload).lower()
    for forbidden in ("lost", "recoverable", "expected", "causal", "hidden"):
        assert forbidden not in text


def test_autopilot_records_history_through_the_audit_service(db_session):
    make_merchant(db_session)
    seed_baseline(db_session)

    advance_autopilot(db_session, MERCHANT)
    advance_autopilot(db_session, MERCHANT, openai_client=fake_openai())

    events = autopilot.merchant_audit_history(db_session, MERCHANT)
    assert [event.event_type for event in events if event.entity_type == "hypothesis"] == [
        "AI_DIAGNOSIS_CREATED",
        "HYPOTHESIS_PROPOSED",
    ]
    assert any(event.event_type == "OPPORTUNITY_DETECTED" for event in events)
    assert verify_merchant_audit_chain(db_session, MERCHANT) is True


def test_experiment_audit_history_requires_a_known_experiment(db_session):
    with pytest.raises(ExperimentNotFoundError):
        autopilot.experiment_audit_history(db_session, "nope")


# ---------------------------------------------------------------------------
# Task 17A: frontend readiness read models
# ---------------------------------------------------------------------------


def test_overview_exposes_task07_segment_and_payment_method_breakdowns(db_session):
    make_merchant(db_session)
    rows = seed_baseline(db_session)

    payload = overview(db_session, MERCHANT)

    # Plain observable dataclasses/results from the existing Task 07 engine,
    # passed through unchanged rather than recomputed here.
    assert payload["segment_metrics"] == get_segment_metrics(db_session, MERCHANT)
    assert payload["payment_method_metrics"] == get_payment_method_metrics(
        db_session, MERCHANT
    )

    segments = payload["segment_metrics"]
    assert {row.segment for row in segments} == {
        "android_mid",
        "android_budget",
        "web_general",
        "repeat_buyer",
        "ios_premium",
    }
    assert sum(row.attempts for row in segments) == payload["metrics"]["attempts"] == rows
    for row in segments:
        assert row.gmv_paise >= row.captured_gmv_paise > 0

    methods = payload["payment_method_metrics"]
    assert [row.payment_method for row in methods] == [
        "upi",
        "card",
        "netbanking",
        "wallet",
    ]
    assert sum(row.attempts for row in methods) == rows


def test_get_autopilot_cycle_reports_every_partial_stage(db_session):
    make_merchant(db_session)
    opportunity = make_opportunity(db_session)

    only = autopilot.get_autopilot_cycle(db_session, opportunity.id)
    assert only["opportunity"].id == opportunity.id
    for stage in (
        "hypothesis",
        "experiment",
        "policy_decision",
        "razorpay_resource",
        "progress",
        "result",
    ):
        assert only[stage] is None, stage
    assert only["merchant_policy"].merchant_id == MERCHANT
    assert only["audit_events"] == []
    assert only["audit_chain_valid"] is True

    hypothesis = make_hypothesis(db_session, opportunity)
    diagnosed = autopilot.get_autopilot_cycle(db_session, opportunity.id)
    assert diagnosed["hypothesis"].id == hypothesis.id
    assert diagnosed["experiment"] is None

    experiment = make_experiment(db_session, hypothesis=hypothesis)
    planned = autopilot.get_autopilot_cycle(db_session, opportunity.id)
    assert planned["experiment"].id == experiment.id
    assert planned["policy_decision"] is None
    assert planned["razorpay_resource"] is None
    assert planned["progress"]["experiment_id"] == experiment.id

    decision = add_policy_decision(db_session, experiment)
    approved = autopilot.get_autopilot_cycle(db_session, opportunity.id)
    assert approved["policy_decision"].id == decision.id

    resource = add_resource(db_session, experiment)
    deployed = autopilot.get_autopilot_cycle(db_session, opportunity.id)
    assert deployed["razorpay_resource"].id == resource.id

    add_attempts(db_session, experiment, variant="control", count=10, captured=5)
    add_attempts(db_session, experiment, variant="treatment", count=10, captured=5)
    running = autopilot.get_autopilot_cycle(db_session, opportunity.id)
    assert running["progress"]["control_attempts"] == 10
    assert running["progress"]["sample_target_reached"] is True
    assert running["result"] is None

    result = add_result(db_session, experiment, decision="KEEP")
    completed = autopilot.get_autopilot_cycle(db_session, opportunity.id)
    assert completed["result"].decision == "KEEP"
    assert completed["result"].id == result.id


def test_get_autopilot_cycle_prefers_latest_rows_and_the_treatment_resource(db_session):
    make_merchant(db_session)
    opportunity = make_opportunity(db_session)
    older = make_hypothesis(db_session, opportunity)
    newer = make_hypothesis(db_session, opportunity)
    older.created_at = _BASE_TIME
    newer.created_at = _BASE_TIME + timedelta(seconds=1)
    db_session.flush()
    experiment = make_experiment(db_session, hypothesis=newer, status="running")
    db_session.add(
        RazorpayResource(
            experiment_id=experiment.id,
            variant="control",
            resource_type="payment_link",
            razorpay_id="plink_control_only",
            config={"amount": 1},
            status="active",
        )
    )
    treatment = add_resource(db_session, experiment)
    db_session.flush()

    cycle = autopilot.get_autopilot_cycle(db_session, opportunity.id)

    # The latest hypothesis for the opportunity, the latest experiment for
    # that hypothesis, and the treatment resource - not the control one.
    assert cycle["hypothesis"].id == newer.id
    assert cycle["experiment"].id == experiment.id
    assert cycle["razorpay_resource"].variant == "treatment"
    assert cycle["razorpay_resource"].id == treatment.id
    assert cycle["razorpay_resource"].razorpay_id != "plink_control_only"


def test_get_autopilot_cycle_is_purely_read_only(db_session):
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="running", min_sample=10)
    add_policy_decision(db_session, experiment)
    add_resource(db_session, experiment)
    add_attempts(db_session, experiment, variant="control", count=10, captured=5)
    add_attempts(db_session, experiment, variant="treatment", count=10, captured=5)
    db_session.flush()
    tables = (
        Opportunity,
        Hypothesis,
        Experiment,
        PolicyDecision,
        RazorpayResource,
        PaymentAttempt,
        ExperimentResult,
        AuditEvent,
    )
    before = {table: db_session.query(table).count() for table in tables}
    status_before = db_session.get(Experiment, experiment.id).status

    cycle = autopilot.get_autopilot_cycle(db_session, experiment.opportunity_id)

    assert cycle["result"] is None
    assert {table: db_session.query(table).count() for table in tables} == before
    assert db_session.get(Experiment, experiment.id).status == status_before
    assert len(db_session.new) == 0 and len(db_session.dirty) == 0


def test_get_autopilot_cycle_missing_opportunity_raises(db_session):
    with pytest.raises(OpportunityNotFoundError):
        autopilot.get_autopilot_cycle(db_session, "no-such-opportunity")


def test_get_autopilot_cycle_reconstructs_after_a_fresh_session(db_session):
    """Browser refresh at the service boundary: commit, then a new Session."""
    make_merchant(db_session)
    opportunity = make_opportunity(db_session)
    hypothesis = make_hypothesis(db_session, opportunity)
    experiment = make_experiment(db_session, hypothesis=hypothesis, status="completed")
    decision = add_policy_decision(db_session, experiment)
    resource = add_resource(db_session, experiment)
    add_attempts(db_session, experiment, variant="control", count=10, captured=5)
    add_attempts(db_session, experiment, variant="treatment", count=10, captured=5)
    add_result(db_session, experiment, decision="KEEP")
    record_audit_event(
        db_session,
        merchant_id=MERCHANT,
        event_type="EXPERIMENT_COMPLETED",
        entity_type="experiment",
        entity_id=experiment.id,
        data={"decision": "KEEP"},
    )
    db_session.commit()

    fresh = sessionmaker(
        bind=db_session.get_bind(), autocommit=False, autoflush=False
    )()
    try:
        cycle = autopilot.get_autopilot_cycle(fresh, opportunity.id)
    finally:
        fresh.close()

    assert cycle["opportunity"].id == opportunity.id
    assert cycle["hypothesis"].id == hypothesis.id
    assert cycle["experiment"].id == experiment.id
    assert cycle["policy_decision"].id == decision.id
    assert cycle["merchant_policy"].merchant_id == MERCHANT
    assert cycle["razorpay_resource"].id == resource.id
    assert cycle["progress"]["control_attempts"] == 10
    assert cycle["progress"]["treatment_attempts"] == 10
    assert cycle["result"].decision == "KEEP"
    assert [event.event_type for event in cycle["audit_events"]] == [
        "EXPERIMENT_COMPLETED"
    ]
    assert cycle["audit_chain_valid"] is True

    # The strict response model accepts the reconstructed projection whole.
    payload = schemas.AutopilotCycleResponse.model_validate(cycle)
    assert payload.opportunity.id == opportunity.id
    assert payload.hypothesis.id == hypothesis.id
    assert payload.experiment.id == experiment.id
    assert payload.policy_decision.decision == "APPROVE"
    assert payload.merchant_policy.allowed_interventions == list(ALL_INTERVENTIONS)
    assert payload.razorpay_resource.razorpay_id == resource.razorpay_id
    assert payload.progress.sample_target_reached is True
    assert payload.result.decision == "KEEP"
    assert payload.audit_chain_valid is True


def test_get_autopilot_cycle_stays_within_one_merchant(db_session):
    make_merchant(db_session, MERCHANT)
    make_merchant(db_session, OTHER_MERCHANT)
    mine = make_opportunity(db_session, MERCHANT)
    my_experiment = make_experiment(db_session, opportunity=mine)
    theirs = make_opportunity(db_session, OTHER_MERCHANT, severity=0.9)
    their_experiment = make_experiment(db_session, opportunity=theirs)
    record_audit_event(
        db_session,
        merchant_id=OTHER_MERCHANT,
        event_type="EXPERIMENT_PLANNED",
        entity_type="experiment",
        entity_id=their_experiment.id,
        data={},
    )
    db_session.flush()

    my_cycle = autopilot.get_autopilot_cycle(db_session, mine.id)
    their_cycle = autopilot.get_autopilot_cycle(db_session, theirs.id)

    assert my_cycle["experiment"].id == my_experiment.id
    assert my_cycle["merchant_policy"].merchant_id == MERCHANT
    assert my_cycle["audit_events"] == []
    assert their_cycle["experiment"].id == their_experiment.id
    assert their_cycle["merchant_policy"].merchant_id == OTHER_MERCHANT
    assert [event.entity_id for event in their_cycle["audit_events"]] == [
        their_experiment.id
    ]


# ---------------------------------------------------------------------------
# 17-20. Boundaries of the orchestration source itself
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


@pytest.mark.parametrize("path", [SERVICE_PATH, ROUTER_PATH])
def test_no_policy_math_is_duplicated(path: Path):
    source = path.read_text(encoding="utf-8")

    # Merchant policy stays in Task 10: only the persisted-decision entry
    # point may be used, and never a re-derived threshold or violation code.
    assert "VIOLATION_" not in source
    assert "evaluate_policy(" not in source
    assert "max_discount_pct" not in source
    assert "min_margin" not in source
    assert "max_financial_exposure" not in source
    assert "max_experiment_exposure_pct" not in source


def test_public_policy_projection_names_limits_without_deriving_them():
    """Task 17A guard: the schema may *name* the persisted policy limits.

    ``MerchantPolicyPublicResponse`` is the one place allowed to spell these
    field names, and only as a read-only projection of the persisted row, so
    the frontend can show a proposed value against the configured maximum
    without parsing prose. Each limit is declared exactly once, no violation
    code or evaluation enters the API layer, and the orchestration service
    hands the row to that projection without reading a single limit itself.
    """
    schema_source = SCHEMAS_PATH.read_text(encoding="utf-8")
    assert "VIOLATION_" not in schema_source
    assert "evaluate_policy(" not in schema_source

    model_source = schema_source[
        schema_source.index("class MerchantPolicyPublicResponse") :
    ]
    model_source = model_source[: model_source.index("\nclass ")]
    for limit in (
        "max_experiment_exposure_pct",
        "max_discount_pct",
        "min_margin_pct",
        "max_financial_exposure",
    ):
        # Exactly one occurrence: the declared field of the public model.
        assert schema_source.count(limit) == 1, limit
        assert f"{limit}: " in model_source, limit

    service_source = SERVICE_PATH.read_text(encoding="utf-8")
    router_source = ROUTER_PATH.read_text(encoding="utf-8")
    for limit in (
        "max_experiment_exposure_pct",
        "max_discount_pct",
        "min_margin_pct",
        "max_financial_exposure",
    ):
        assert limit not in service_source, limit
        assert limit not in router_source, limit


def test_domain_engines_are_delegated_to_never_reimplemented():
    source = SERVICE_PATH.read_text(encoding="utf-8")
    modules = _imported_modules(SERVICE_PATH)

    # every lifecycle verb here is one call into an existing layer
    for delegated in (
        "run_opportunity_detection",
        "diagnose_opportunity",
        "plan_experiment",
        "evaluate_experiment_policy",
        "deploy_experiment_treatment",
        "execute_experiment_batch",
        "evaluate_experiment_results",
        "rollback_experiment_treatment",
        "verify_merchant_audit_chain",
    ):
        assert delegated in source, delegated
    assert {"app.engines.opportunities", "app.engines.diagnosis", "app.engines.planner"} <= modules
    # runtime traffic is requested through the Task 11 service facade
    assert "app.services.experiments" in modules
    assert "app.simulation" not in " ".join(modules)


@pytest.mark.parametrize("path", [SERVICE_PATH, ROUTER_PATH, SCHEMAS_PATH])
def test_no_statistics_are_duplicated(path: Path):
    source = path.read_text(encoding="utf-8")

    assert "evaluate_conversion_experiment" not in source
    assert "math" not in _imported_modules(path)
    for forbidden in ("sqrt", "erfc", "alpha =", "practical_absolute_lift"):
        assert forbidden not in source
    # Significance may only ever be read back from the persisted result row.
    assert "p_value=" not in source
    assert "is_significant=" not in source


@pytest.mark.parametrize("path", [SERVICE_PATH, ROUTER_PATH])
def test_no_direct_razorpay_calls(path: Path):
    source = path.read_text(encoding="utf-8")

    # Razorpay is reachable only through the Task 13 executor service.
    for forbidden in (
        "create_payment_link",
        "cancel_payment_link",
        "RazorpayClient(",
        "httpx",
        "api.razorpay.com",
    ):
        assert forbidden not in source
    if path == SERVICE_PATH:
        assert "deploy_experiment_treatment" in source
        assert "rollback_experiment_treatment" in source
    else:
        assert "autopilot.deploy(" in source
        assert "autopilot.rollback(" in source


@pytest.mark.parametrize("path", [SERVICE_PATH, ROUTER_PATH, SCHEMAS_PATH])
def test_no_hidden_causal_model_import(path: Path):
    modules = _imported_modules(path)

    # Traffic may only be requested through the Task 11 runtime service, and
    # the model boundary only through the Task 08 diagnosis engine.
    forbidden_markers = ("simulation", "causal", "openai")
    offenders = {
        module
        for module in modules
        for marker in forbidden_markers
        if marker in module
    }
    assert not offenders, offenders


@pytest.mark.parametrize("path", [SERVICE_PATH, SCHEMAS_PATH])
def test_only_the_api_boundary_owns_commits(path: Path):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "commit", path
            assert node.func.attr != "rollback", path


def test_api_boundary_owns_every_commit_and_the_one_ledger_exception():
    source = ROUTER_PATH.read_text(encoding="utf-8")

    # Two commit sites and one rollback site, all inside the shared helpers, so
    # no route can commit on its own: the happy path, plus the deliberate
    # exception that keeps Task 13's operation ledger across an external
    # failure. Reads never commit because read_view passes write=False.
    assert source.count("db.commit()") == 2
    assert source.count("db.rollback()") == 1

    run_body = source[source.index("def _run(") : source.index("def read_view(")]
    assert run_body.count("db.commit()") == 1
    assert run_body.count("db.rollback()") == 1
    assert "_preserve_external_ledger(db)" in run_body

    ledger_body = source[
        source.index("def _preserve_external_ledger(") : source.index("def _run(")
    ]
    assert ledger_body.count("db.commit()") == 1
    # The preserved-ledger path never rolls back and never replaces the error.
    assert "db.rollback()" not in ledger_body
    assert "raise" not in ledger_body


def test_service_decides_transitions_from_persisted_state(db_session):
    make_merchant(db_session)
    opportunity = make_opportunity(db_session)
    hypothesis = make_hypothesis(db_session, opportunity)

    assert resolve_transition(db_session, MERCHANT).action == ACTION_PLAN

    experiment = make_experiment(db_session, hypothesis=hypothesis)
    assert resolve_transition(db_session, MERCHANT).action == ACTION_POLICY


    experiment.status = "approved"
    add_resource(db_session, experiment)
    add_policy_decision(db_session, experiment)
    db_session.flush()
    assert resolve_transition(db_session, MERCHANT).action == ACTION_RUN_BATCH

    add_attempts(db_session, experiment, variant="control", count=10, captured=5)
    add_attempts(db_session, experiment, variant="treatment", count=10, captured=5)
    db_session.flush()
    assert resolve_transition(db_session, MERCHANT).action == ACTION_EVALUATE


def test_inconsistent_lifecycle_state_is_not_deployed(db_session):
    """An APPROVE row without the matching status is reported, never acted on."""
    make_merchant(db_session)
    experiment = make_experiment(db_session, status="proposed")
    add_policy_decision(db_session, experiment, decision="APPROVE")
    client = FakeRazorpayClient()

    step = advance_autopilot(db_session, MERCHANT, razorpay_client=client)

    assert step.step == STEP_COMPLETED
    assert "no further Autopilot action" in step.message
    assert client.create_calls == []


def test_step_and_state_vocabulary_is_stable():
    assert autopilot.AUTOPILOT_STEPS == (
        "OPPORTUNITY_DETECTED",
        "HYPOTHESIS_PROPOSED",
        "EXPERIMENT_PLANNED",
        "POLICY_APPROVED",
        "POLICY_REJECTED",
        "RESOURCE_DEPLOYED",
        "DEPLOYMENT_BLOCKED",
        "EXPERIMENT_BATCH_RUN",
        "EXPERIMENT_EVALUATED",
        "RESOURCE_ROLLED_BACK",
        "COMPLETED",
        "NO_ACTION",
    )
    assert set(autopilot.STATE_BY_ACTION) == {
        ACTION_BLOCKED,
        ACTION_DEPLOY,
        ACTION_DIAGNOSE,
        ACTION_DONE,
        ACTION_EVALUATE,
        ACTION_PLAN,
        ACTION_POLICY,
        ACTION_ROLLBACK,
        ACTION_RUN_BATCH,
        ACTION_STOP,
        autopilot.ACTION_DETECT,
    }
