from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.router import read_merchant_intelligence, router
from app.api.schemas import MerchantIntelligenceResponse
from app.db.base import Base
from app.db.models import (
    Experiment,
    ExperimentResult,
    Hypothesis,
    Merchant,
    MerchantPolicy,
    Opportunity,
    PaymentAttempt,
    PolicyDecision,
)
from app.services import autopilot


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'intelligence.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def seed_intelligence(db):
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    merchant = Merchant(id="merchant-a", name="Merchant A", category="electronics")
    policy = MerchantPolicy(
        id="policy-a",
        merchant_id="merchant-a",
        allowed_interventions=["offer_discount", "partial_payment"],
    )
    history_opp = Opportunity(
        id="opp-history", merchant_id="merchant-a", type="segment_conversion_divergence",
        segment="android_budget", severity=0.12, detected_metric="conversion_rate",
        detected_value=0.40, baseline_value=0.55, evidence={"segment_attempts": 100, "absolute_gap": 0.15},
        status="resolved", created_at=now,
    )
    hypothesis = Hypothesis(
        id="hyp-history", opportunity_id=history_opp.id, merchant_id="merchant-a",
        hypothesis_text="test a five percent offer", intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.05}, evidence_refs=["absolute_gap"],
        status="tested", created_at=now,
    )
    experiment = Experiment(
        id="exp-keep", merchant_id="merchant-a", hypothesis_id=hypothesis.id,
        opportunity_id=history_opp.id, name="android-budget-offer", segment="android_budget",
        intervention_type="offer_discount", control_config={"offer": None},
        treatment_config={"discount_pct": 0.05}, traffic_split_treatment_pct=0.10,
        primary_metric="conversion_rate", guardrail_metrics=[], min_sample_per_variant=200,
        max_duration_hours=72, status="completed", started_at=now, ended_at=now, created_at=now,
    )
    decision = PolicyDecision(
        id="pd-keep", experiment_id=experiment.id, merchant_id="merchant-a", decision="APPROVE",
        violations=[], original_params={"discount_pct": 0.05}, final_params={"discount_pct": 0.05},
        evaluated_at=now,
    )
    result = ExperimentResult(
        id="result-keep", experiment_id=experiment.id, control_count=200, treatment_count=200,
        control_conversions=80, treatment_conversions=100, control_rate=0.40, treatment_rate=0.50,
        absolute_lift=0.10, relative_lift=0.25, p_value=0.01,
        confidence_interval_lower=0.03, confidence_interval_upper=0.17,
        is_significant=True, decision="KEEP", decided_at=now,
    )
    active = Opportunity(
        id="opp-live", merchant_id="merchant-a", type="segment_conversion_divergence",
        segment="android_budget", severity=0.20, detected_metric="conversion_rate",
        detected_value=0.42, baseline_value=0.58,
        evidence={"segment_attempts": 120, "absolute_gap": 0.16}, status="detected", created_at=now,
    )
    attempts = [
        PaymentAttempt(id="pa-1", merchant_id="merchant-a", amount=10000, payment_method="upi", status="captured", segment="android_budget", is_simulated=True, created_at=now),
        PaymentAttempt(id="pa-2", merchant_id="merchant-a", amount=12000, payment_method="card", status="failed", segment="android_budget", is_simulated=True, created_at=now),
    ]
    db.add_all([merchant, policy, history_opp, hypothesis, experiment, decision, result, active, *attempts])
    db.flush()


def test_intelligence_combines_portfolio_champion_and_terminal_memory(db):
    seed_intelligence(db)
    payload = autopilot.merchant_intelligence(db, "merchant-a")
    response = MerchantIntelligenceResponse.model_validate(payload)

    assert response.merchant.merchant_id == "merchant-a"
    assert response.champion.version == 2
    assert response.champion.promotion_count == 1
    assert response.champion.configs[0].source_experiment_id == "exp-keep"
    assert response.champion.configs[0].config == {"discount_pct": 0.05}

    assert response.memory.trial_count == 1
    assert response.memory.keep_count == 1
    assert response.memory.records[0].experiment_id == "exp-keep"
    assert response.memory.knowledge[0].keep_count == 1

    assert response.portfolio.next_best_opportunity_id == "opp-live"
    ranked = response.portfolio.opportunities[0]
    assert ranked.opportunity_id == "opp-live"
    assert ranked.prior_terminal_trials == 1
    assert ranked.previously_tried_interventions == ["offer_discount"]
    assert ranked.untried_allowed_interventions == ["partial_payment"]
    assert ranked.priority_index == 1.0


def test_intelligence_route_is_read_only_projection(db):
    seed_intelligence(db)
    response = read_merchant_intelligence("merchant-a", db)
    assert response.champion.version == 2
    assert response.memory.keep_count == 1
    assert any(route.path == "/api/v1/merchants/{merchant_id}/intelligence" for route in router.routes)

    # GET projection does not own a commit. The seeded transaction is still
    # rollback-able after the handler returns.
    db.rollback()
    assert db.get(Merchant, "merchant-a") is None


def test_intelligence_missing_merchant_uses_existing_not_found_contract(db):
    with pytest.raises(autopilot.MerchantNotFoundError):
        autopilot.merchant_intelligence(db, "missing")
