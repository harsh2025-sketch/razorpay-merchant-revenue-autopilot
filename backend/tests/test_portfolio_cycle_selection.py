"""Integration tests for Task 19B portfolio selection at cycle rollover."""

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
from app.services import autopilot
from app.services.cycles import start_new_cycle


@pytest.fixture
def db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'portfolio-cycle.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(Merchant(id="merchant-a", name="A"))
    session.add(
        MerchantPolicy(
            merchant_id="merchant-a",
            allowed_interventions=["payment_method_config", "partial_payment"],
            max_experiment_exposure_pct=0.1,
            max_discount_pct=0.15,
            min_margin_pct=0.05,
            max_concurrent_experiments=3,
            max_experiment_duration_hours=168,
            min_sample_size=30,
            max_financial_exposure=50000,
        )
    )
    session.flush()
    yield session
    session.close()
    engine.dispose()


def _segment_attempts(db: Session, segment: str, *, amount: int) -> None:
    for index in range(4):
        db.add(
            PaymentAttempt(
                id=f"{segment}-{index}",
                merchant_id="merchant-a",
                amount=amount,
                status="captured" if index < 2 else "failed",
                payment_method="upi",
                segment=segment,
                is_simulated=True,
            )
        )
    db.flush()


def _candidate(
    db: Session,
    *,
    row_id: str,
    segment: str,
    severity: float,
    gap: float,
    attempts: int = 1000,
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
        status="detected",
    )
    db.add(row)
    db.flush()
    return row


def _completed_focus(db: Session) -> Opportunity:
    opportunity = Opportunity(
        id="opp-current",
        merchant_id="merchant-a",
        type="segment_conversion_divergence",
        segment="current-segment",
        severity=0.9,
        detected_metric="conversion_rate",
        detected_value=0.3,
        baseline_value=0.5,
        evidence={"absolute_gap": 0.2, "segment_attempts": 1000},
        status="detected",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(opportunity)
    db.flush()
    hypothesis = Hypothesis(
        id="hyp-current",
        opportunity_id=opportunity.id,
        merchant_id="merchant-a",
        hypothesis_text="current completed cycle",
        intervention_type="partial_payment",
        intervention_params={"accept_partial": True},
        evidence_refs=[],
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(hypothesis)
    db.flush()
    experiment = Experiment(
        id="exp-current",
        merchant_id="merchant-a",
        hypothesis_id=hypothesis.id,
        opportunity_id=opportunity.id,
        name="current",
        segment="current-segment",
        intervention_type="partial_payment",
        control_config={"accept_partial": False},
        treatment_config={"accept_partial": True},
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
            id="policy-current",
            experiment_id=experiment.id,
            merchant_id="merchant-a",
            decision="APPROVE",
            violations=[],
            original_params={},
            final_params={},
            evaluated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
    )
    db.add(
        ExperimentResult(
            id="result-current",
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
            decision="INCONCLUSIVE",
            decided_at=datetime(2026, 1, 1, 1, tzinfo=timezone.utc),
        )
    )
    db.flush()
    return opportunity


def test_start_new_cycle_uses_portfolio_instead_of_raw_severity(db: Session):
    current = _completed_focus(db)
    _segment_attempts(db, "alpha", amount=10_000)
    _segment_attempts(db, "beta", amount=30_000)
    alpha = _candidate(
        db,
        row_id="opp-alpha",
        segment="alpha",
        severity=0.30,
        gap=0.10,
    )
    beta = _candidate(
        db,
        row_id="opp-beta",
        segment="beta",
        severity=0.10,
        gap=0.08,
    )

    # Legacy focus would choose alpha by detector severity once the current
    # terminal cycle is resolved. Task 19B should choose beta because its
    # observable recoverable-GMV proxy is larger.
    selected = start_new_cycle(db, "merchant-a")

    assert current.status == "resolved"
    assert autopilot.focus_opportunity(db, "merchant-a") is beta
    assert selected is beta
    assert selected.id == "opp-beta"


def test_started_cycle_resume_beats_portfolio_ranking(db: Session):
    _segment_attempts(db, "alpha", amount=10_000)
    _segment_attempts(db, "beta", amount=50_000)
    started = _candidate(
        db,
        row_id="opp-started",
        segment="alpha",
        severity=0.05,
        gap=0.08,
    )
    untouched = _candidate(
        db,
        row_id="opp-untouched",
        segment="beta",
        severity=0.50,
        gap=0.20,
    )
    db.add(
        Hypothesis(
            id="hyp-started",
            opportunity_id=started.id,
            merchant_id="merchant-a",
            hypothesis_text="resume me",
            intervention_type="partial_payment",
            intervention_params={"accept_partial": True},
            evidence_refs=[],
        )
    )
    db.flush()

    selected = autopilot.focus_opportunity(db, "merchant-a")

    assert selected is started
    assert selected is not untouched


def test_missing_policy_falls_back_to_existing_focus(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'no-policy.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        db.add(Merchant(id="merchant-a", name="A"))
        db.flush()
        first = _candidate(
            db,
            row_id="opp-first",
            segment="alpha",
            severity=0.30,
            gap=0.10,
        )
        _candidate(
            db,
            row_id="opp-second",
            segment="beta",
            severity=0.20,
            gap=0.20,
        )

        selected = autopilot.focus_opportunity(db, "merchant-a")

        assert selected is first
    finally:
        db.close()
        engine.dispose()
