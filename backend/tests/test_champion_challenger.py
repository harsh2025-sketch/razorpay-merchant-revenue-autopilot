"""Task 19C tests: derived champion state and champion-vs-challenger planning."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import (
    AuditEvent,
    Experiment,
    ExperimentResult,
    Hypothesis,
    Merchant,
    Opportunity,
    PaymentAttempt,
)
from app.engines.planner import ExperimentPlanningError, plan_experiment
from app.engines.statistics import evaluate_experiment_results
from app.services.audit import EXPERIMENT_COMPLETED, TREATMENT_PROMOTED
from app.services.champion import (
    ChampionMerchantNotFoundError,
    champion_control_config,
    get_merchant_champion_state,
    is_identical_to_current_champion,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
CHAMPION_PATH = BACKEND_DIR / "app" / "services" / "champion.py"


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'champion.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([Merchant(id="merchant-a", name="A"), Merchant(id="merchant-b", name="B")])
    session.flush()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _make_opportunity(db, *, merchant_id="merchant-a", suffix="1", segment="android_budget"):
    opportunity = Opportunity(
        id=f"opp-{suffix}",
        merchant_id=merchant_id,
        type="segment_conversion_divergence",
        segment=segment,
        severity=0.1,
        detected_metric="conversion_rate",
        detected_value=0.45,
        baseline_value=0.55,
        evidence={"segment_attempts": 500, "absolute_gap": 0.10},
        status="detected",
    )
    db.add(opportunity)
    db.flush()
    return opportunity


def _make_hypothesis(
    db,
    opportunity,
    *,
    suffix="1",
    intervention_type="offer_discount",
    params=None,
):
    if params is None:
        params = {"discount_pct": 0.10}
    hypothesis = Hypothesis(
        id=f"hyp-{suffix}",
        opportunity_id=opportunity.id,
        merchant_id=opportunity.merchant_id,
        hypothesis_text="test challenger",
        intervention_type=intervention_type,
        intervention_params=params,
        evidence_refs=["segment_conversion_rate"],
        status="proposed",
    )
    db.add(hypothesis)
    db.flush()
    return hypothesis


def _make_experiment(
    db,
    *,
    experiment_id,
    merchant_id="merchant-a",
    segment="android_budget",
    intervention_type="offer_discount",
    control_config=None,
    treatment_config=None,
    status="completed",
    created_at=None,
):
    opportunity = _make_opportunity(
        db,
        merchant_id=merchant_id,
        suffix=f"{experiment_id}-opp",
        segment=segment,
    )
    hypothesis = _make_hypothesis(
        db,
        opportunity,
        suffix=f"{experiment_id}-hyp",
        intervention_type=intervention_type,
        params=treatment_config or {"discount_pct": 0.10},
    )
    experiment = Experiment(
        id=experiment_id,
        merchant_id=merchant_id,
        hypothesis_id=hypothesis.id,
        opportunity_id=opportunity.id,
        name=f"{segment}-{intervention_type}",
        segment=segment,
        intervention_type=intervention_type,
        control_config=control_config or {"offer": None},
        treatment_config=treatment_config or {"discount_pct": 0.10},
        traffic_split_treatment_pct=0.10,
        primary_metric="conversion_rate",
        guardrail_metrics=[],
        min_sample_per_variant=200,
        max_duration_hours=72,
        status=status,
        created_at=created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(experiment)
    db.flush()
    return experiment


def _add_result(
    db,
    experiment,
    *,
    decision,
    decided_at,
    absolute_lift=0.10,
    p_value=0.001,
):
    result = ExperimentResult(
        experiment_id=experiment.id,
        control_count=200,
        treatment_count=200,
        control_conversions=80,
        treatment_conversions=100,
        control_rate=0.40,
        treatment_rate=0.50,
        absolute_lift=absolute_lift,
        relative_lift=0.25,
        p_value=p_value,
        confidence_interval_lower=0.03,
        confidence_interval_upper=0.17,
        is_significant=decision in {"KEEP", "ROLLBACK"},
        decision=decision,
        decided_at=decided_at,
    )
    db.add(result)
    db.flush()
    return result


def test_initial_state_is_baseline_v1_and_missing_merchant_fails(db):
    state = get_merchant_champion_state(db, "merchant-a")
    assert state.version == 1
    assert state.promotion_count == 0
    assert state.configs == ()
    assert state.latest_promotion_experiment_id is None

    control, version, source = champion_control_config(
        db,
        merchant_id="merchant-a",
        intervention_type="offer_discount",
        fallback_control={"offer": None},
    )
    assert control == {"offer": None}
    assert version == 1
    assert source is None

    with pytest.raises(ChampionMerchantNotFoundError):
        get_merchant_champion_state(db, "missing")


def test_keep_advances_version_and_latest_keep_wins_per_intervention(db):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = _make_experiment(
        db,
        experiment_id="exp-keep-1",
        treatment_config={"discount_pct": 0.05},
        created_at=t0,
    )
    _add_result(db, first, decision="KEEP", decided_at=t0 + timedelta(hours=1), absolute_lift=0.04)

    ignored = _make_experiment(
        db,
        experiment_id="exp-inconclusive",
        treatment_config={"discount_pct": 0.08},
        created_at=t0 + timedelta(hours=2),
    )
    _add_result(
        db,
        ignored,
        decision="INCONCLUSIVE",
        decided_at=t0 + timedelta(hours=3),
        absolute_lift=0.01,
        p_value=0.40,
    )

    second = _make_experiment(
        db,
        experiment_id="exp-keep-2",
        treatment_config={"discount_pct": 0.10},
        created_at=t0 + timedelta(hours=4),
    )
    _add_result(db, second, decision="KEEP", decided_at=t0 + timedelta(hours=5), absolute_lift=0.06)

    partial = _make_experiment(
        db,
        experiment_id="exp-partial",
        intervention_type="partial_payment",
        control_config={"accept_partial": False},
        treatment_config={"accept_partial": True},
        created_at=t0 + timedelta(hours=6),
    )
    _add_result(db, partial, decision="KEEP", decided_at=t0 + timedelta(hours=7), absolute_lift=0.05)

    state = get_merchant_champion_state(db, "merchant-a")
    assert state.version == 4
    assert state.promotion_count == 3
    assert state.latest_promotion_experiment_id == partial.id
    offer = state.config_for("offer_discount")
    assert offer is not None
    assert offer.config == {"discount_pct": 0.10}
    assert offer.source_experiment_id == second.id
    assert state.config_for("partial_payment").config == {"accept_partial": True}

    control, version, source = champion_control_config(
        db,
        merchant_id="merchant-a",
        intervention_type="offer_discount",
        fallback_control={"offer": None},
    )
    assert control == {"discount_pct": 0.10}
    assert version == 4
    assert source == second.id


def test_planner_uses_current_champion_as_control(db):
    t0 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    champion = _make_experiment(
        db,
        experiment_id="exp-champion",
        treatment_config={"discount_pct": 0.05},
        created_at=t0,
    )
    _add_result(db, champion, decision="KEEP", decided_at=t0 + timedelta(minutes=1))

    opportunity = _make_opportunity(db, suffix="challenger")
    hypothesis = _make_hypothesis(
        db,
        opportunity,
        suffix="challenger",
        params={"discount_pct": 0.10},
    )
    challenger = plan_experiment(db, hypothesis.id)

    assert challenger.control_config == {"discount_pct": 0.05}
    assert challenger.treatment_config == {"discount_pct": 0.10}
    assert challenger.control_config != challenger.treatment_config


def test_planner_rejects_challenger_identical_to_current_champion(db):
    t0 = datetime(2026, 2, 2, tzinfo=timezone.utc)
    champion = _make_experiment(
        db,
        experiment_id="exp-same",
        treatment_config={"discount_pct": 0.10},
        created_at=t0,
    )
    _add_result(db, champion, decision="KEEP", decided_at=t0 + timedelta(minutes=1))

    opportunity = _make_opportunity(db, suffix="same-challenger")
    hypothesis = _make_hypothesis(
        db,
        opportunity,
        suffix="same-challenger",
        params={"discount_pct": 0.10},
    )

    assert is_identical_to_current_champion(
        db,
        merchant_id="merchant-a",
        intervention_type="offer_discount",
        challenger_config={"discount_pct": 0.10},
    )
    with pytest.raises(ExperimentPlanningError, match="identical to current champion"):
        plan_experiment(db, hypothesis.id)
    assert db.get(Experiment, "exp-same") is not None
    assert db.query(Experiment).filter(Experiment.hypothesis_id == hypothesis.id).count() == 0


def _seed_running_experiment(db, *, merchant_id, experiment_id, control_captured, treatment_captured):
    experiment = _make_experiment(
        db,
        experiment_id=experiment_id,
        merchant_id=merchant_id,
        status="running",
        treatment_config={"discount_pct": 0.05},
    )
    for variant, captured in (("control", control_captured), ("treatment", treatment_captured)):
        for index in range(200):
            db.add(
                PaymentAttempt(
                    id=f"{experiment_id}-{variant}-{index}",
                    merchant_id=merchant_id,
                    experiment_id=experiment_id,
                    variant=variant,
                    status="captured" if index < captured else "failed",
                    amount=10000,
                    segment=experiment.segment,
                    is_simulated=True,
                )
            )
    db.flush()
    return experiment


def test_keep_emits_treatment_promoted_after_completion(db):
    experiment = _seed_running_experiment(
        db,
        merchant_id="merchant-a",
        experiment_id="exp-stat-keep",
        control_captured=80,
        treatment_captured=120,
    )

    result = evaluate_experiment_results(db, experiment.id)
    assert result.decision == "KEEP"

    events = (
        db.query(AuditEvent)
        .filter(AuditEvent.entity_id == experiment.id)
        .order_by(AuditEvent.created_at.asc())
        .all()
    )
    assert [event.event_type for event in events] == [
        EXPERIMENT_COMPLETED,
        TREATMENT_PROMOTED,
    ]
    promoted = events[-1]
    assert promoted.actor == "statistics"
    assert promoted.data["champion_version"] == 2
    assert promoted.data["intervention_type"] == "offer_discount"
    assert promoted.data["source_experiment_id"] == experiment.id

    state = get_merchant_champion_state(db, "merchant-a")
    assert state.version == 2
    assert state.config_for("offer_discount").config == {"discount_pct": 0.05}


def test_non_keep_never_emits_treatment_promoted(db):
    experiment = _seed_running_experiment(
        db,
        merchant_id="merchant-b",
        experiment_id="exp-stat-rollback",
        control_captured=120,
        treatment_captured=80,
    )

    result = evaluate_experiment_results(db, experiment.id)
    assert result.decision == "ROLLBACK"
    assert (
        db.query(AuditEvent)
        .filter(
            AuditEvent.entity_id == experiment.id,
            AuditEvent.event_type == TREATMENT_PROMOTED,
        )
        .count()
        == 0
    )
    assert get_merchant_champion_state(db, "merchant-b").version == 1


def test_champion_service_has_no_write_or_forbidden_execution_dependencies():
    source = CHAMPION_PATH.read_text(encoding="utf-8")
    assert ".commit(" not in source
    for forbidden in (
        "OpenAI",
        "RazorpayClient",
        "simulate_outcome",
        "causal_model",
        "engines.policy",
        "record_audit_event",
    ):
        assert forbidden not in source
