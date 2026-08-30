"""Tests for Task 14: lifecycle audit trail + tamper-evident hash chain."""

from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

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
)
from app.engines.diagnosis import diagnose_opportunity, persist_hypothesis
from app.engines.opportunities import DetectedOpportunity, persist_detected_opportunities
from app.engines.planner import plan_experiment
from app.engines.policy import evaluate_experiment_policy
from app.engines.statistics import evaluate_experiment_results
from app.schemas.hypothesis import HypothesisProposal
from app.services.audit import (
    AI_DIAGNOSIS_CREATED,
    ACTOR_DETECTOR,
    ACTOR_SYSTEM,
    AUDIT_EVENT_TYPES,
    AuditError,
    EXPERIMENT_COMPLETED,
    EXPERIMENT_PLANNED,
    EXPERIMENT_ROLLED_BACK,
    EXPERIMENT_STARTED,
    HYPOTHESIS_PROPOSED,
    OPPORTUNITY_DETECTED,
    POLICY_APPROVED,
    POLICY_REJECTED,
    RAZORPAY_RESOURCE_CANCELLED,
    RAZORPAY_RESOURCE_CREATED,
    TREATMENT_PROMOTED,
    compute_event_hash,
    get_experiment_audit_history,
    get_merchant_audit_history,
    has_audit_event,
    record_audit_event,
    record_audit_event_once,
    verify_merchant_audit_chain,
)
from app.services.executor import (
    deploy_experiment_treatment,
    rollback_experiment_treatment,
)
from app.simulation.runner import run_experiment_batch
from tests.test_diagnosis_engine import (
    ALL_INTERVENTIONS,
    MOCK_MODEL_RESPONSE,
    FakeOpenAIClient,
    create_merchant,
    make_opportunity,
    make_proposal,
)
from tests.test_experiment_executor import FakeRazorpayClient, create_experiment
from tests.test_experiment_runtime import make_experiment as make_runtime_experiment

BACKEND_DIR = Path(__file__).resolve().parents[1]
AUDIT_PATH = BACKEND_DIR / "app" / "services" / "audit.py"


@pytest.fixture
def db_session(tmp_path):
    db_file = tmp_path / "test_audit.db"
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


def make_audit_merchant(db, merchant_id: str = "merchant_audit") -> Merchant:
    merchant = Merchant(id=merchant_id, name=f"Merchant {merchant_id}")
    db.add(merchant)
    db.flush()
    return merchant


def _detected(segment: str = "android_budget") -> DetectedOpportunity:
    return DetectedOpportunity(
        segment=segment,
        segment_attempts=200,
        segment_captured=60,
        segment_conversion_rate=0.3,
        comparison_attempts=200,
        comparison_captured=140,
        comparison_conversion_rate=0.7,
        absolute_gap=0.4,
        severity=0.2,
        evidence={"segment": segment},
    )


def _event_types(db, merchant_id: str, event_type: str) -> list[AuditEvent]:
    return (
        db.query(AuditEvent)
        .filter(AuditEvent.merchant_id == merchant_id)
        .filter(AuditEvent.event_type == event_type)
        .order_by(AuditEvent.created_at.asc(), AuditEvent.id.asc())
        .all()
    )


def _seed_proposed_experiment(
    db,
    *,
    merchant_id: str = "merchant_policy_audit",
    intervention_type: str = "offer_discount",
    treatment_config: dict | None = None,
    control_config: dict | None = None,
    traffic_split: float = 0.10,
    min_sample: int = 200,
    max_duration: int = 72,
    **policy_kwargs,
) -> Experiment:
    defaults = {
        "max_experiment_exposure_pct": 0.10,
        "max_discount_pct": 0.15,
        "min_margin_pct": 0.05,
        "max_concurrent_experiments": 3,
        "max_experiment_duration_hours": 168,
        "min_sample_size": 30,
        "max_financial_exposure": 50000,
        "allowed_interventions": list(ALL_INTERVENTIONS),
    }
    defaults.update(policy_kwargs)
    merchant = Merchant(id=merchant_id, name=f"Merchant {merchant_id}")
    db.add(merchant)
    db.add(MerchantPolicy(id=f"policy_{merchant_id}", merchant_id=merchant_id, **defaults))
    opp = Opportunity(
        merchant_id=merchant_id,
        type="segment_conversion_divergence",
        segment="android_budget",
        severity=0.1,
        detected_metric="conversion_rate",
        detected_value=0.47,
        baseline_value=0.58,
        evidence={},
        status="detected",
    )
    db.add(opp)
    db.flush()
    hyp = Hypothesis(
        opportunity_id=opp.id,
        merchant_id=merchant_id,
        hypothesis_text="test",
        intervention_type=intervention_type,
        intervention_params=treatment_config or {"discount_pct": 0.05},
        status="proposed",
        evidence_refs=["segment_conversion_rate"],
    )
    db.add(hyp)
    db.flush()
    if intervention_type == "offer_discount":
        control = control_config or {"offer": None}
        treatment = treatment_config or {"discount_pct": 0.05}
    elif intervention_type == "payment_method_config":
        control = control_config or {"payment_methods": "merchant_default"}
        treatment = treatment_config or {"payment_methods": {"upi": True, "card": False}}
    else:
        control = control_config or {}
        treatment = treatment_config or {}
    experiment = Experiment(
        merchant_id=merchant_id,
        hypothesis_id=hyp.id,
        opportunity_id=opp.id,
        name=f"{opp.segment}-{intervention_type}",
        segment=opp.segment,
        intervention_type=intervention_type,
        control_config=control,
        treatment_config=treatment,
        traffic_split_treatment_pct=traffic_split,
        primary_metric="conversion_rate",
        guardrail_metrics=["captured_gmv"],
        min_sample_per_variant=min_sample,
        max_duration_hours=max_duration,
        status="proposed",
    )
    db.add(experiment)
    db.flush()
    return experiment


def _seed_running_for_stats(db, merchant_id: str = "merchant_stats") -> Experiment:
    merchant = Merchant(id=merchant_id, name="stats")
    db.add(merchant)
    opp = Opportunity(
        merchant_id=merchant_id,
        type="x",
        severity=1,
        detected_metric="x",
        evidence={},
    )
    db.add(opp)
    db.flush()
    hyp = Hypothesis(
        opportunity_id=opp.id,
        merchant_id=merchant_id,
        hypothesis_text="x",
        intervention_type="x",
        intervention_params={},
        evidence_refs=[],
    )
    db.add(hyp)
    db.flush()
    experiment = Experiment(
        merchant_id=merchant_id,
        hypothesis_id=hyp.id,
        opportunity_id=opp.id,
        name="x",
        segment="x",
        intervention_type="x",
        control_config={},
        treatment_config={},
        traffic_split_treatment_pct=0.5,
        primary_metric="conversion_rate",
        guardrail_metrics=[],
        min_sample_per_variant=2,
        max_duration_hours=1,
        status="running",
    )
    db.add(experiment)
    db.flush()
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for i, (variant, status) in enumerate(
        [
            ("control", "captured"),
            ("control", "failed"),
            ("treatment", "captured"),
            ("treatment", "abandoned"),
        ]
    ):
        db.add(
            PaymentAttempt(
                merchant_id=merchant_id,
                experiment_id=experiment.id,
                variant=variant,
                status=status,
                amount=1,
                created_at=base + timedelta(seconds=i),
            )
        )
    db.flush()
    return experiment


# ===========================================================================
# 1-7. record_audit_event core
# ===========================================================================


def test_1_record_event_persists_audit_event(db_session):
    merchant = make_audit_merchant(db_session)
    event = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=OPPORTUNITY_DETECTED,
        entity_type="opportunity",
        entity_id="opp-1",
        data={"type": "segment_conversion_divergence", "segment": "android_budget", "severity": 0.2},
        actor=ACTOR_DETECTOR,
    )
    assert event.id is not None
    stored = db_session.get(AuditEvent, event.id)
    assert stored is not None
    assert stored.event_type == OPPORTUNITY_DETECTED
    assert stored.actor == ACTOR_DETECTOR
    assert stored.entity_type == "opportunity"
    assert stored.entity_id == "opp-1"
    assert stored.data["segment"] == "android_budget"


def test_2_record_event_does_not_commit(db_session, monkeypatch):
    merchant = make_audit_merchant(db_session)

    def boom():
        raise AssertionError("commit was called")

    monkeypatch.setattr(db_session, "commit", boom)
    record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=OPPORTUNITY_DETECTED,
    )
    assert db_session.query(AuditEvent).count() == 1
    db_session.rollback()
    assert db_session.query(AuditEvent).count() == 0


def test_3_timestamp_is_utc_aware(db_session):
    merchant = make_audit_merchant(db_session)
    event = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=OPPORTUNITY_DETECTED,
    )
    assert event.created_at is not None
    assert event.created_at.tzinfo is not None
    assert event.created_at.utcoffset() == timedelta(0)


def test_4_stable_event_type_required(db_session):
    merchant = make_audit_merchant(db_session)
    with pytest.raises(AuditError, match="event_type"):
        record_audit_event(db_session, merchant_id=merchant.id, event_type="")
    with pytest.raises(AuditError, match="event_type"):
        record_audit_event(db_session, merchant_id=merchant.id, event_type=None)  # type: ignore[arg-type]


def test_5_invalid_event_type_rejected(db_session):
    merchant = make_audit_merchant(db_session)
    with pytest.raises(AuditError, match="invalid event_type"):
        record_audit_event(
            db_session,
            merchant_id=merchant.id,
            event_type="NOT_A_REAL_EVENT",
        )
    assert TREATMENT_PROMOTED in AUDIT_EVENT_TYPES


def test_6_data_none_becomes_empty_dict(db_session):
    merchant = make_audit_merchant(db_session)
    event = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=OPPORTUNITY_DETECTED,
        data=None,
    )
    assert event.data == {}


def test_7_secret_looking_fields_sanitized(db_session):
    merchant = make_audit_merchant(db_session)
    event = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=EXPERIMENT_PLANNED,
        data={
            "api_key": "sk-secret-openai",
            "segment": "android_budget",
            "nested": {"key_secret": "rzp_test_hidden", "ok": 1},
        },
    )
    blob = json.dumps(event.data)
    assert "sk-secret-openai" not in blob
    assert "rzp_test_hidden" not in blob
    assert "api_key" not in event.data
    assert event.data["segment"] == "android_budget"
    assert event.data["nested"]["ok"] == 1
    assert "key_secret" not in event.data["nested"]


# ===========================================================================
# 8-9. once / coexistence
# ===========================================================================


def test_8_record_audit_event_once_does_not_duplicate(db_session):
    merchant = make_audit_merchant(db_session)
    first = record_audit_event_once(
        db_session,
        merchant_id=merchant.id,
        event_type=EXPERIMENT_PLANNED,
        entity_type="experiment",
        entity_id="exp-1",
        data={"segment": "android_budget"},
    )
    second = record_audit_event_once(
        db_session,
        merchant_id=merchant.id,
        event_type=EXPERIMENT_PLANNED,
        entity_type="experiment",
        entity_id="exp-1",
        data={"segment": "ios_premium"},
    )
    assert second.id == first.id
    assert db_session.query(AuditEvent).count() == 1
    assert has_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=EXPERIMENT_PLANNED,
        entity_type="experiment",
        entity_id="exp-1",
    )


def test_9_different_event_type_can_coexist(db_session):
    merchant = make_audit_merchant(db_session)
    record_audit_event_once(
        db_session,
        merchant_id=merchant.id,
        event_type=EXPERIMENT_PLANNED,
        entity_type="experiment",
        entity_id="exp-1",
    )
    record_audit_event_once(
        db_session,
        merchant_id=merchant.id,
        event_type=POLICY_APPROVED,
        entity_type="experiment",
        entity_id="exp-1",
    )
    assert db_session.query(AuditEvent).count() == 2


# ===========================================================================
# 10-15. history queries
# ===========================================================================


def test_10_merchant_history_chronological(db_session):
    merchant = make_audit_merchant(db_session)
    first = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=OPPORTUNITY_DETECTED,
        entity_id="a",
    )
    second = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=EXPERIMENT_PLANNED,
        entity_type="experiment",
        entity_id="b",
    )
    third = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=POLICY_APPROVED,
        entity_type="experiment",
        entity_id="b",
    )
    history = get_merchant_audit_history(db_session, merchant.id)
    assert [event.id for event in history] == [first.id, second.id, third.id]
    assert history[0].created_at <= history[1].created_at <= history[2].created_at


def test_11_history_merchant_isolation(db_session):
    merchant_a = make_audit_merchant(db_session, "merchant_a")
    merchant_b = make_audit_merchant(db_session, "merchant_b")
    record_audit_event(
        db_session,
        merchant_id=merchant_a.id,
        event_type=OPPORTUNITY_DETECTED,
        entity_id="a",
    )
    record_audit_event(
        db_session,
        merchant_id=merchant_b.id,
        event_type=EXPERIMENT_PLANNED,
        entity_type="experiment",
        entity_id="b",
    )
    history_a = get_merchant_audit_history(db_session, merchant_a.id)
    history_b = get_merchant_audit_history(db_session, merchant_b.id)
    assert len(history_a) == 1
    assert history_a[0].merchant_id == merchant_a.id
    assert history_a[0].event_type == OPPORTUNITY_DETECTED
    assert len(history_b) == 1
    assert history_b[0].merchant_id == merchant_b.id
    assert history_b[0].prev_hash is None


def test_12_experiment_history_correct(db_session):
    merchant = make_audit_merchant(db_session)
    record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=OPPORTUNITY_DETECTED,
        entity_type="opportunity",
        entity_id="opp-1",
    )
    record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=EXPERIMENT_PLANNED,
        entity_type="experiment",
        entity_id="exp-1",
    )
    record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=POLICY_APPROVED,
        entity_type="experiment",
        entity_id="exp-1",
    )
    record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=EXPERIMENT_PLANNED,
        entity_type="experiment",
        entity_id="exp-2",
    )
    history = get_experiment_audit_history(db_session, "exp-1")
    assert [event.event_type for event in history] == [
        EXPERIMENT_PLANNED,
        POLICY_APPROVED,
    ]
    assert all(event.entity_id == "exp-1" for event in history)


def test_13_invalid_limit_zero_rejected(db_session):
    merchant = make_audit_merchant(db_session)
    with pytest.raises(AuditError, match="limit"):
        get_merchant_audit_history(db_session, merchant.id, limit=0)
    with pytest.raises(AuditError, match="limit"):
        get_experiment_audit_history(db_session, "exp-1", limit=0)


def test_14_bool_limit_rejected(db_session):
    merchant = make_audit_merchant(db_session)
    with pytest.raises(AuditError, match="limit"):
        get_merchant_audit_history(db_session, merchant.id, limit=True)  # type: ignore[arg-type]
    with pytest.raises(AuditError, match="limit"):
        get_experiment_audit_history(db_session, "exp-1", limit=False)  # type: ignore[arg-type]


def test_15_limit_over_1000_rejected(db_session):
    merchant = make_audit_merchant(db_session)
    with pytest.raises(AuditError, match="limit"):
        get_merchant_audit_history(db_session, merchant.id, limit=1001)


# ===========================================================================
# 16-21. hash chain
# ===========================================================================


def test_16_first_prev_hash_is_none(db_session):
    merchant = make_audit_merchant(db_session)
    event = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=OPPORTUNITY_DETECTED,
    )
    assert event.prev_hash is None


def test_17_second_prev_hash_equals_first_hash(db_session):
    merchant = make_audit_merchant(db_session)
    first = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=OPPORTUNITY_DETECTED,
    )
    second = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=HYPOTHESIS_PROPOSED,
        entity_type="hypothesis",
        entity_id="h1",
    )
    assert second.prev_hash == first.event_hash


def test_18_hashes_are_64_char_sha256(db_session):
    merchant = make_audit_merchant(db_session)
    first = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=OPPORTUNITY_DETECTED,
        data={"segment": "android_budget"},
    )
    second = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=EXPERIMENT_PLANNED,
        entity_type="experiment",
        entity_id="e1",
    )
    for event in (first, second):
        assert event.event_hash is not None
        assert len(event.event_hash) == 64
        assert all(char in "0123456789abcdef" for char in event.event_hash)
    recomputed = compute_event_hash(
        merchant_id=first.merchant_id,
        event_type=first.event_type,
        entity_type=first.entity_type,
        entity_id=first.entity_id,
        data=first.data,
        actor=first.actor,
        prev_hash=first.prev_hash,
        created_at=first.created_at,
    )
    assert recomputed == first.event_hash
    assert first.id not in recomputed


def test_19_verify_chain_true(db_session):
    merchant = make_audit_merchant(db_session)
    record_audit_event(db_session, merchant_id=merchant.id, event_type=OPPORTUNITY_DETECTED)
    record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=EXPERIMENT_PLANNED,
        entity_type="experiment",
        entity_id="e1",
    )
    assert verify_merchant_audit_chain(db_session, merchant.id) is True
    assert verify_merchant_audit_chain(db_session, "merchant_missing") is True


def test_20_changing_event_data_makes_verification_false(db_session):
    merchant = make_audit_merchant(db_session)
    event = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=OPPORTUNITY_DETECTED,
        data={"segment": "android_budget"},
    )
    assert verify_merchant_audit_chain(db_session, merchant.id) is True
    event.data = {**dict(event.data), "tampered": True}
    db_session.flush()
    assert verify_merchant_audit_chain(db_session, merchant.id) is False


def test_21_changing_prev_hash_makes_verification_false(db_session):
    merchant = make_audit_merchant(db_session)
    record_audit_event(db_session, merchant_id=merchant.id, event_type=OPPORTUNITY_DETECTED)
    second = record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=EXPERIMENT_PLANNED,
        entity_type="experiment",
        entity_id="e1",
    )
    assert verify_merchant_audit_chain(db_session, merchant.id) is True
    second.prev_hash = "0" * 64
    db_session.flush()
    assert verify_merchant_audit_chain(db_session, merchant.id) is False


# ===========================================================================
# 22-40. lifecycle integration
# ===========================================================================


def test_22_new_opportunity_emits_opportunity_detected(db_session):
    merchant = make_audit_merchant(db_session, "m_opp")
    persisted = persist_detected_opportunities(db_session, merchant.id, [_detected()])
    events = _event_types(db_session, merchant.id, OPPORTUNITY_DETECTED)
    assert len(persisted) == 1
    assert len(events) == 1
    assert events[0].entity_type == "opportunity"
    assert events[0].entity_id == persisted[0].id
    assert events[0].actor == "detector"
    assert events[0].data["type"] == "segment_conversion_divergence"
    assert events[0].data["segment"] == "android_budget"


def test_23_duplicate_opportunity_does_not_duplicate_event(db_session):
    merchant = make_audit_merchant(db_session, "m_opp_dup")
    persist_detected_opportunities(db_session, merchant.id, [_detected()])
    persist_detected_opportunities(db_session, merchant.id, [_detected()])
    assert len(_event_types(db_session, merchant.id, OPPORTUNITY_DETECTED)) == 1


def test_24_new_hypothesis_emits_ai_diagnosis_created(db_session):
    merchant = create_merchant(db_session, "m_diag")
    opportunity = make_opportunity(db_session, merchant.id)
    hypothesis = diagnose_opportunity(
        db_session, opportunity.id, client=FakeOpenAIClient(MOCK_MODEL_RESPONSE)
    )
    events = _event_types(db_session, merchant.id, AI_DIAGNOSIS_CREATED)
    assert len(events) == 1
    assert events[0].entity_id == hypothesis.id
    assert events[0].actor == "ai"
    assert events[0].data["ai_model"]


def test_25_new_hypothesis_emits_hypothesis_proposed(db_session):
    merchant = create_merchant(db_session, "m_hyp")
    opportunity = make_opportunity(db_session, merchant.id)
    hypothesis = diagnose_opportunity(
        db_session, opportunity.id, client=FakeOpenAIClient(MOCK_MODEL_RESPONSE)
    )
    events = _event_types(db_session, merchant.id, HYPOTHESIS_PROPOSED)
    assert len(events) == 1
    assert events[0].entity_id == hypothesis.id
    assert events[0].data["intervention_type"] == hypothesis.intervention_type
    assert events[0].data["confidence"] == hypothesis.confidence


def test_26_duplicate_hypothesis_does_not_duplicate_events(db_session):
    merchant = create_merchant(db_session, "m_hyp_dup")
    opportunity = make_opportunity(db_session, merchant.id)
    diagnose_opportunity(
        db_session, opportunity.id, client=FakeOpenAIClient(MOCK_MODEL_RESPONSE)
    )
    diagnose_opportunity(
        db_session, opportunity.id, client=FakeOpenAIClient(MOCK_MODEL_RESPONSE)
    )
    assert len(_event_types(db_session, merchant.id, AI_DIAGNOSIS_CREATED)) == 1
    assert len(_event_types(db_session, merchant.id, HYPOTHESIS_PROPOSED)) == 1


def test_27_new_experiment_emits_experiment_planned(db_session):
    merchant = create_merchant(db_session, "m_plan", with_policy=False)
    opportunity = make_opportunity(db_session, merchant.id)
    hypothesis = persist_hypothesis(
        db_session,
        opportunity=opportunity,
        proposal=make_proposal(),
        ai_model="test-model",
    )
    experiment = plan_experiment(db_session, hypothesis.id)
    events = _event_types(db_session, merchant.id, EXPERIMENT_PLANNED)
    assert len(events) == 1
    assert events[0].entity_type == "experiment"
    assert events[0].entity_id == experiment.id
    assert events[0].actor == "planner"
    assert events[0].data["segment"] == experiment.segment
    assert events[0].data["intervention_type"] == experiment.intervention_type
    assert events[0].data["traffic_split_treatment_pct"] == experiment.traffic_split_treatment_pct


def test_28_duplicate_planning_does_not_duplicate_event(db_session):
    merchant = create_merchant(db_session, "m_plan_dup", with_policy=False)
    opportunity = make_opportunity(db_session, merchant.id)
    hypothesis = persist_hypothesis(
        db_session,
        opportunity=opportunity,
        proposal=make_proposal(),
        ai_model="test-model",
    )
    plan_experiment(db_session, hypothesis.id)
    plan_experiment(db_session, hypothesis.id)
    assert len(_event_types(db_session, merchant.id, EXPERIMENT_PLANNED)) == 1


def test_29_policy_approve_emits_policy_approved(db_session):
    experiment = _seed_proposed_experiment(db_session, merchant_id="m_pol_ok")
    decision = evaluate_experiment_policy(db_session, experiment.id)
    assert decision.decision == "APPROVE"
    events = _event_types(db_session, experiment.merchant_id, POLICY_APPROVED)
    assert len(events) == 1
    assert events[0].entity_type == "experiment"
    assert events[0].entity_id == experiment.id
    assert events[0].actor == "policy"
    assert events[0].data["violations"] == []


def test_30_policy_reject_emits_policy_rejected(db_session):
    experiment = _seed_proposed_experiment(
        db_session,
        merchant_id="m_pol_bad",
        treatment_config={"discount_pct": 0.20},
    )
    decision = evaluate_experiment_policy(db_session, experiment.id)
    assert decision.decision == "REJECT"
    events = _event_types(db_session, experiment.merchant_id, POLICY_REJECTED)
    assert len(events) == 1
    assert events[0].data["violations"]
    assert len(_event_types(db_session, experiment.merchant_id, POLICY_APPROVED)) == 0


def test_31_second_policy_lookup_does_not_duplicate(db_session):
    experiment = _seed_proposed_experiment(db_session, merchant_id="m_pol_dup")
    evaluate_experiment_policy(db_session, experiment.id)
    evaluate_experiment_policy(db_session, experiment.id)
    assert len(_event_types(db_session, experiment.merchant_id, POLICY_APPROVED)) == 1


def test_32_first_runtime_batch_emits_experiment_started(db_session):
    experiment = make_runtime_experiment(db_session, status="approved")
    run_experiment_batch(db_session, experiment.id, batch_size=5)
    events = _event_types(db_session, experiment.merchant_id, EXPERIMENT_STARTED)
    assert len(events) == 1
    assert events[0].entity_type == "experiment"
    assert events[0].entity_id == experiment.id
    assert events[0].actor == "runtime"
    assert events[0].data["control_target"] == experiment.min_sample_per_variant
    assert events[0].data["treatment_target"] == experiment.min_sample_per_variant


def test_33_second_runtime_batch_no_second_start_event(db_session):
    experiment = make_runtime_experiment(db_session, status="approved")
    run_experiment_batch(db_session, experiment.id, batch_size=5)
    run_experiment_batch(db_session, experiment.id, batch_size=5)
    assert len(_event_types(db_session, experiment.merchant_id, EXPERIMENT_STARTED)) == 1


def test_34_statistics_emits_experiment_completed(db_session):
    experiment = _seed_running_for_stats(db_session)
    result = evaluate_experiment_results(db_session, experiment.id)
    events = _event_types(db_session, experiment.merchant_id, EXPERIMENT_COMPLETED)
    assert len(events) == 1
    assert events[0].entity_id == experiment.id
    assert events[0].actor == "statistics"
    assert events[0].data["decision"] == result.decision
    assert "p_value" in events[0].data
    assert "absolute_lift" in events[0].data
    assert "control_rate" in events[0].data
    assert "treatment_rate" in events[0].data


def test_35_duplicate_statistics_evaluation_no_duplicate(db_session):
    experiment = _seed_running_for_stats(db_session, merchant_id="merchant_stats_dup")
    evaluate_experiment_results(db_session, experiment.id)
    evaluate_experiment_results(db_session, experiment.id)
    assert len(_event_types(db_session, experiment.merchant_id, EXPERIMENT_COMPLETED)) == 1


def test_36_executor_new_resource_emits_razorpay_resource_created(db_session):
    experiment = create_experiment(db_session, experiment_id="exp-audit-deploy")
    resource = deploy_experiment_treatment(
        db_session, experiment.id, razorpay_client=FakeRazorpayClient()
    )
    events = _event_types(db_session, experiment.merchant_id, RAZORPAY_RESOURCE_CREATED)
    assert len(events) == 1
    assert events[0].entity_type == "experiment"
    assert events[0].entity_id == experiment.id
    assert events[0].actor == "razorpay_executor"
    assert events[0].data["resource_type"] == "payment_link"
    assert events[0].data["razorpay_id"] == resource.razorpay_id
    assert events[0].data["variant"] == "treatment"


def test_37_repeated_deploy_no_duplicate_resource_audit(db_session):
    experiment = create_experiment(db_session, experiment_id="exp-audit-deploy-dup")
    fake = FakeRazorpayClient()
    deploy_experiment_treatment(db_session, experiment.id, razorpay_client=fake)
    deploy_experiment_treatment(db_session, experiment.id, razorpay_client=fake)
    assert len(_event_types(db_session, experiment.merchant_id, RAZORPAY_RESOURCE_CREATED)) == 1
    assert len(fake.create_calls) == 1


def test_38_rollback_emits_razorpay_resource_cancelled(db_session):
    experiment = create_experiment(db_session, experiment_id="exp-audit-rb")
    deploy_experiment_treatment(
        db_session, experiment.id, razorpay_client=FakeRazorpayClient()
    )
    db_session.add(
        ExperimentResult(
            experiment_id=experiment.id,
            decision="ROLLBACK",
            control_count=100,
            treatment_count=100,
            control_conversions=40,
            treatment_conversions=30,
        )
    )
    db_session.flush()
    resource = rollback_experiment_treatment(
        db_session, experiment.id, razorpay_client=FakeRazorpayClient()
    )
    events = _event_types(db_session, experiment.merchant_id, RAZORPAY_RESOURCE_CANCELLED)
    assert len(events) == 1
    assert events[0].data["razorpay_id"] == resource.razorpay_id
    assert events[0].data["resource_type"] == "payment_link"


def test_39_rollback_emits_experiment_rolled_back(db_session):
    experiment = create_experiment(db_session, experiment_id="exp-audit-rb2")
    deploy_experiment_treatment(
        db_session, experiment.id, razorpay_client=FakeRazorpayClient()
    )
    db_session.add(
        ExperimentResult(
            experiment_id=experiment.id,
            decision="ROLLBACK",
            control_count=100,
            treatment_count=100,
            control_conversions=40,
            treatment_conversions=30,
        )
    )
    db_session.flush()
    resource = rollback_experiment_treatment(
        db_session, experiment.id, razorpay_client=FakeRazorpayClient()
    )
    events = _event_types(db_session, experiment.merchant_id, EXPERIMENT_ROLLED_BACK)
    assert len(events) == 1
    assert events[0].entity_type == "experiment"
    assert events[0].entity_id == experiment.id
    assert events[0].data["razorpay_id"] == resource.razorpay_id
    assert len(_event_types(db_session, experiment.merchant_id, TREATMENT_PROMOTED)) == 0


def test_40_repeated_rollback_no_duplicate(db_session):
    experiment = create_experiment(db_session, experiment_id="exp-audit-rb-dup")
    deploy_experiment_treatment(
        db_session, experiment.id, razorpay_client=FakeRazorpayClient()
    )
    db_session.add(
        ExperimentResult(
            experiment_id=experiment.id,
            decision="ROLLBACK",
            control_count=100,
            treatment_count=100,
            control_conversions=40,
            treatment_conversions=30,
        )
    )
    db_session.flush()
    rollback_experiment_treatment(
        db_session, experiment.id, razorpay_client=FakeRazorpayClient()
    )
    rollback_experiment_treatment(
        db_session, experiment.id, razorpay_client=FakeRazorpayClient()
    )
    assert len(_event_types(db_session, experiment.merchant_id, RAZORPAY_RESOURCE_CANCELLED)) == 1
    assert len(_event_types(db_session, experiment.merchant_id, EXPERIMENT_ROLLED_BACK)) == 1


# ===========================================================================
# 41-45. isolation
# ===========================================================================


def _all_audit_blobs(db) -> str:
    return json.dumps([event.data for event in db.query(AuditEvent).all()])


def test_41_no_api_keys_stored(db_session):
    merchant = make_audit_merchant(db_session, "m_keys")
    record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=AI_DIAGNOSIS_CREATED,
        data={
            "api_key": "sk-secret-openai",
            "OPENAI_API_KEY": "sk-secret-openai",
            "RAZORPAY_KEY_ID": "rzp_test_abc",
            "RAZORPAY_KEY_SECRET": "super_secret_do_not_leak_42",
            "ai_model": "gpt-4.1-mini",
        },
    )
    blob = _all_audit_blobs(db_session)
    assert "sk-secret-openai" not in blob
    assert "rzp_test_abc" not in blob
    assert "super_secret_do_not_leak_42" not in blob
    stored = db_session.query(AuditEvent).one()
    assert stored.data.get("ai_model") == "gpt-4.1-mini"


def test_42_no_raw_authorization_header_stored(db_session):
    merchant = make_audit_merchant(db_session, "m_auth")
    record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=RAZORPAY_RESOURCE_CREATED,
        data={
            "Authorization": "Bearer super-secret-token",
            "authorization": "Basic dXNlcjpwYXNz",
            "resource_type": "payment_link",
        },
    )
    blob = _all_audit_blobs(db_session)
    assert "Bearer super-secret-token" not in blob
    assert "Basic dXNlcjpwYXNz" not in blob
    assert "Authorization" not in json.dumps(db_session.query(AuditEvent).one().data)


def test_43_no_openai_prompt_stored(db_session):
    merchant = create_merchant(db_session, "m_prompt")
    opportunity = make_opportunity(db_session, merchant.id)
    diagnose_opportunity(
        db_session, opportunity.id, client=FakeOpenAIClient(MOCK_MODEL_RESPONSE)
    )
    record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=AI_DIAGNOSIS_CREATED,
        entity_id="extra",
        data={
            "prompt": "You are diagnosing a merchant payment/revenue anomaly.",
            "system_prompt": "hidden chain-of-thought here",
            "messages": [{"role": "user", "content": "raw prompt"}],
        },
        actor=ACTOR_SYSTEM,
    )
    blob = _all_audit_blobs(db_session)
    assert "You are diagnosing a merchant" not in blob
    assert "hidden chain-of-thought" not in blob
    assert "raw prompt" not in blob
    for event in db_session.query(AuditEvent).all():
        assert "prompt" not in event.data
        assert "messages" not in event.data
        assert "system_prompt" not in event.data


def test_44_no_hidden_causal_fields_stored(db_session):
    merchant = make_audit_merchant(db_session, "m_causal")
    record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=EXPERIMENT_COMPLETED,
        data={
            "decision": "KEEP",
            "expected_lift": 0.42,
            "treatment_effect": 0.11,
            "hidden_problem": "upi_friction",
            "causal_true_uplift": 0.25,
            "p_value": 0.01,
        },
    )
    stored = db_session.query(AuditEvent).one()
    blob = json.dumps(stored.data)
    for forbidden in (
        "expected_lift",
        "treatment_effect",
        "hidden_problem",
        "causal_true_uplift",
    ):
        assert forbidden not in blob
        assert forbidden not in stored.data
    assert stored.data["decision"] == "KEEP"
    assert stored.data["p_value"] == pytest.approx(0.01)


def test_45_no_db_commit_in_audit_service(db_session, monkeypatch):
    source = AUDIT_PATH.read_text(encoding="utf-8")
    assert ".commit(" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "commit"
        ):
            pytest.fail("audit.py calls .commit()")

    merchant = make_audit_merchant(db_session, "m_nocommit")

    def boom():
        raise AssertionError("commit was called")

    monkeypatch.setattr(db_session, "commit", boom)
    record_audit_event(
        db_session,
        merchant_id=merchant.id,
        event_type=OPPORTUNITY_DETECTED,
    )
    get_merchant_audit_history(db_session, merchant.id)
    get_experiment_audit_history(db_session, "exp-none")
    assert verify_merchant_audit_chain(db_session, merchant.id) is True


def test_proposal_schema_still_used_by_persist_helper():
    """Sanity: HypothesisProposal remains the structured diagnosis contract."""
    proposal = HypothesisProposal.model_validate(MOCK_MODEL_RESPONSE)
    assert proposal.intervention_type == "payment_method_config"
