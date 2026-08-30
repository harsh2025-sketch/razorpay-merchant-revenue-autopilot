"""Task 19B tests for deterministic opportunity portfolio ranking."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

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
from app.services.portfolio import (
    OpportunityPortfolioMerchantNotFoundError,
    build_opportunity_portfolio,
)


@pytest.fixture
def db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'portfolio.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Merchant(id="merchant-a", name="A"))
    session.flush()
    yield session
    session.close()
    engine.dispose()


def _add_policy(db: Session, allowed: list[str]) -> None:
    db.add(
        MerchantPolicy(
            merchant_id="merchant-a",
            allowed_interventions=allowed,
            max_experiment_exposure_pct=0.1,
            max_discount_pct=0.15,
            min_margin_pct=0.05,
            max_concurrent_experiments=3,
            max_experiment_duration_hours=168,
            min_sample_size=30,
            max_financial_exposure=50000,
        )
    )
    db.flush()


def _add_segment_aov(db: Session, segment: str, *, amount_paise: int) -> None:
    for index in range(4):
        db.add(
            PaymentAttempt(
                id=f"{segment}-{index}",
                merchant_id="merchant-a",
                amount=amount_paise,
                payment_method="upi",
                status="captured" if index < 2 else "failed",
                segment=segment,
                is_simulated=True,
            )
        )
    db.flush()


def _opportunity(
    db: Session,
    *,
    row_id: str,
    segment: str,
    gap: float,
    attempts: int,
    severity: float = 0.1,
    status: str = "detected",
) -> Opportunity:
    row = Opportunity(
        id=row_id,
        merchant_id="merchant-a",
        type="segment_conversion_divergence",
        segment=segment,
        severity=severity,
        detected_metric="conversion_rate",
        detected_value=0.5 - gap,
        baseline_value=0.5,
        evidence={"absolute_gap": gap, "segment_attempts": attempts},
        status=status,
    )
    db.add(row)
    db.flush()
    return row


def _completed_trial(
    db: Session,
    *,
    experiment_id: str,
    segment: str,
    intervention_type: str,
    treatment_config: dict,
    decision: str = "INCONCLUSIVE",
) -> None:
    opportunity = Opportunity(
        id=f"history-opp-{experiment_id}",
        merchant_id="merchant-a",
        type="segment_conversion_divergence",
        segment=segment,
        severity=0.1,
        detected_metric="conversion_rate",
        detected_value=0.4,
        baseline_value=0.5,
        evidence={},
        status="resolved",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(opportunity)
    db.flush()
    hypothesis = Hypothesis(
        id=f"history-hyp-{experiment_id}",
        opportunity_id=opportunity.id,
        merchant_id="merchant-a",
        hypothesis_text="historical treatment",
        intervention_type=intervention_type,
        intervention_params=treatment_config,
        evidence_refs=[],
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(hypothesis)
    db.flush()
    experiment = Experiment(
        id=experiment_id,
        merchant_id="merchant-a",
        hypothesis_id=hypothesis.id,
        opportunity_id=opportunity.id,
        name=f"{segment}-{intervention_type}",
        segment=segment,
        intervention_type=intervention_type,
        control_config={},
        treatment_config=treatment_config,
        traffic_split_treatment_pct=0.1,
        primary_metric="conversion_rate",
        guardrail_metrics=[],
        min_sample_per_variant=200,
        max_duration_hours=72,
        status="completed",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
    )
    db.add(experiment)
    db.flush()
    db.add(
        PolicyDecision(
            experiment_id=experiment.id,
            merchant_id="merchant-a",
            decision="APPROVE",
            violations=[],
            original_params={},
            final_params={},
        )
    )
    db.add(
        ExperimentResult(
            experiment_id=experiment.id,
            control_count=1000,
            treatment_count=200,
            control_conversions=500,
            treatment_conversions=100,
            control_rate=0.5,
            treatment_rate=0.5,
            absolute_lift=0.0,
            relative_lift=0.0,
            p_value=1.0,
            confidence_interval_lower=-0.05,
            confidence_interval_upper=0.05,
            is_significant=False,
            decision=decision,
            decided_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        )
    )
    db.flush()


def test_portfolio_sizes_recoverable_gmv_proxy_and_ranks_without_magic_weights(db: Session):
    _add_policy(db, ["payment_method_config", "partial_payment"])
    _add_segment_aov(db, "alpha", amount_paise=10_000)
    _add_segment_aov(db, "beta", amount_paise=20_000)
    alpha = _opportunity(db, row_id="opp-alpha", segment="alpha", gap=0.10, attempts=1000)
    beta = _opportunity(db, row_id="opp-beta", segment="beta", gap=0.08, attempts=1000)

    portfolio = build_opportunity_portfolio(db, "merchant-a", opportunities=[alpha, beta])

    assert [row.opportunity_id for row in portfolio.opportunities] == ["opp-beta", "opp-alpha"]
    assert portfolio.next_best_opportunity_id == "opp-beta"
    top = portfolio.opportunities[0]
    assert top.estimated_incremental_captures == pytest.approx(80.0)
    assert top.average_captured_order_value_paise == pytest.approx(20_000)
    assert top.estimated_recoverable_gmv_paise == 1_600_000
    assert top.history_adjusted_gmv_proxy_paise == 1_600_000
    assert top.priority_index == pytest.approx(1.0)
    assert portfolio.opportunities[1].priority_index == pytest.approx(1_000_000 / 1_600_000)


def test_previous_segment_trials_apply_transparent_exploration_penalty(db: Session):
    _add_policy(db, ["payment_method_config", "partial_payment"])
    _add_segment_aov(db, "alpha", amount_paise=10_000)
    _add_segment_aov(db, "beta", amount_paise=20_000)
    _completed_trial(
        db,
        experiment_id="history-beta",
        segment="beta",
        intervention_type="partial_payment",
        treatment_config={"accept_partial": True},
    )
    alpha = _opportunity(db, row_id="opp-alpha", segment="alpha", gap=0.10, attempts=1000)
    beta = _opportunity(db, row_id="opp-beta", segment="beta", gap=0.08, attempts=1000)

    portfolio = build_opportunity_portfolio(db, "merchant-a", opportunities=[alpha, beta])

    assert [row.opportunity_id for row in portfolio.opportunities] == ["opp-alpha", "opp-beta"]
    beta_ranked = portfolio.opportunities[1]
    assert beta_ranked.prior_terminal_trials == 1
    assert beta_ranked.history_factor == pytest.approx(0.5)
    assert beta_ranked.history_adjusted_gmv_proxy_paise == 800_000
    assert beta_ranked.previously_tried_interventions == ("partial_payment",)
    assert beta_ranked.untried_allowed_interventions == ("payment_method_config",)


def test_missing_or_empty_policy_marks_candidates_infeasible(db: Session):
    _add_segment_aov(db, "alpha", amount_paise=10_000)
    alpha = _opportunity(db, row_id="opp-alpha", segment="alpha", gap=0.10, attempts=1000)

    portfolio = build_opportunity_portfolio(db, "merchant-a", opportunities=[alpha])
    assert portfolio.next_best_opportunity_id is None
    assert portfolio.opportunities[0].policy_feasible is False
    assert portfolio.opportunities[0].priority_index == 0.0


def test_touched_or_inactive_opportunities_are_not_portfolio_candidates(db: Session):
    _add_policy(db, ["partial_payment"])
    _add_segment_aov(db, "alpha", amount_paise=10_000)
    touched = _opportunity(db, row_id="opp-touched", segment="alpha", gap=0.10, attempts=1000)
    resolved = _opportunity(
        db,
        row_id="opp-resolved",
        segment="alpha",
        gap=0.20,
        attempts=1000,
        status="resolved",
    )
    db.add(
        Hypothesis(
            id="hyp-touched",
            opportunity_id=touched.id,
            merchant_id="merchant-a",
            hypothesis_text="already diagnosing",
            intervention_type="partial_payment",
            intervention_params={},
            evidence_refs=[],
        )
    )
    db.flush()

    portfolio = build_opportunity_portfolio(
        db,
        "merchant-a",
        opportunities=[touched, resolved],
    )
    assert portfolio.opportunities == ()
    assert portfolio.next_best_opportunity_id is None


def test_rank_is_deterministic_when_value_and_severity_tie(db: Session):
    _add_policy(db, ["partial_payment"])
    _add_segment_aov(db, "alpha", amount_paise=10_000)
    _add_segment_aov(db, "beta", amount_paise=10_000)
    beta = _opportunity(db, row_id="opp-z", segment="beta", gap=0.10, attempts=1000, severity=0.1)
    alpha = _opportunity(db, row_id="opp-a", segment="alpha", gap=0.10, attempts=1000, severity=0.1)

    first = build_opportunity_portfolio(db, "merchant-a", opportunities=[beta, alpha])
    second = build_opportunity_portfolio(db, "merchant-a", opportunities=[alpha, beta])

    assert [row.opportunity_id for row in first.opportunities] == ["opp-a", "opp-z"]
    assert first == second


def test_portfolio_queries_persisted_untouched_active_opportunities(db: Session):
    _add_policy(db, ["partial_payment"])
    _add_segment_aov(db, "alpha", amount_paise=10_000)
    _opportunity(db, row_id="opp-alpha", segment="alpha", gap=0.10, attempts=1000)

    portfolio = build_opportunity_portfolio(db, "merchant-a")

    assert portfolio.next_best_opportunity_id == "opp-alpha"
    assert len(portfolio.opportunities) == 1


def test_missing_merchant_errors_and_service_is_read_only(db: Session, monkeypatch: pytest.MonkeyPatch):
    with pytest.raises(OpportunityPortfolioMerchantNotFoundError):
        build_opportunity_portfolio(db, "missing")

    _add_policy(db, ["partial_payment"])
    _add_segment_aov(db, "alpha", amount_paise=10_000)
    opportunity = _opportunity(db, row_id="opp-alpha", segment="alpha", gap=0.10, attempts=1000)
    commit_calls: list[bool] = []
    monkeypatch.setattr(db, "commit", lambda: commit_calls.append(True))

    portfolio = build_opportunity_portfolio(db, "merchant-a", opportunities=[opportunity])

    assert portfolio.next_best_opportunity_id == "opp-alpha"
    assert opportunity.status == "detected"
    assert commit_calls == []


def test_portfolio_has_no_forbidden_runtime_dependencies():
    text = Path(__file__).parents[1].joinpath("app/services/portfolio.py").read_text()
    for forbidden in (
        "causal_model",
        "OpenAI",
        "RazorpayClient",
        "simulate_outcome",
        "record_audit_event",
        ".commit(",
    ):
        assert forbidden not in text
