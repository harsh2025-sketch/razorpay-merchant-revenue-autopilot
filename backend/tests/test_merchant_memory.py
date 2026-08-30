"""Task 19A tests for deterministic merchant experiment memory."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
    Opportunity,
    PolicyDecision,
    RazorpayResource,
)
from app.services.memory import (
    MerchantMemoryNotFoundError,
    find_equivalent_trials,
    get_merchant_experiment_memory,
    treatment_config_fingerprint,
)


@pytest.fixture
def db(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'memory.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([Merchant(id="merchant-a", name="A"), Merchant(id="merchant-b", name="B")])
    session.flush()
    yield session
    session.close()
    engine.dispose()


def _make_experiment(
    db: Session,
    *,
    experiment_id: str,
    merchant_id: str = "merchant-a",
    status: str = "completed",
    segment: str = "android_budget",
    intervention_type: str = "partial_payment",
    treatment_config: dict | None = None,
    created_at: datetime | None = None,
) -> Experiment:
    when = created_at or datetime(2026, 1, 1, tzinfo=timezone.utc)
    opportunity = Opportunity(
        id=f"opp-{experiment_id}",
        merchant_id=merchant_id,
        type="segment_conversion_divergence",
        segment=segment,
        severity=0.1,
        detected_metric="conversion_rate",
        detected_value=0.45,
        baseline_value=0.55,
        evidence={},
        status="resolved" if status in {"completed", "rolled_back", "cancelled"} else "detected",
        created_at=when,
    )
    db.add(opportunity)
    db.flush()
    hypothesis = Hypothesis(
        id=f"hyp-{experiment_id}",
        opportunity_id=opportunity.id,
        merchant_id=merchant_id,
        hypothesis_text="Try a bounded treatment",
        intervention_type=intervention_type,
        intervention_params=treatment_config or {},
        evidence_refs=[],
        created_at=when,
    )
    db.add(hypothesis)
    db.flush()
    experiment = Experiment(
        id=experiment_id,
        merchant_id=merchant_id,
        hypothesis_id=hypothesis.id,
        opportunity_id=opportunity.id,
        name=f"{segment}-{intervention_type}",
        segment=segment,
        intervention_type=intervention_type,
        control_config={},
        treatment_config=treatment_config or {},
        traffic_split_treatment_pct=0.1,
        primary_metric="conversion_rate",
        guardrail_metrics=[],
        min_sample_per_variant=200,
        max_duration_hours=72,
        status=status,
        started_at=when if status != "proposed" else None,
        ended_at=when + timedelta(hours=1) if status in {"completed", "rolled_back", "cancelled"} else None,
        created_at=when,
    )
    db.add(experiment)
    db.flush()
    return experiment


def _add_policy(
    db: Session,
    experiment: Experiment,
    decision: str,
    *,
    violations: list[str] | None = None,
    evaluated_at: datetime | None = None,
    row_id: str | None = None,
) -> PolicyDecision:
    policy = PolicyDecision(
        id=row_id,
        experiment_id=experiment.id,
        merchant_id=experiment.merchant_id,
        decision=decision,
        violations=violations or [],
        original_params={},
        final_params={} if decision == "APPROVE" else None,
        evaluated_at=evaluated_at or experiment.created_at,
    )
    db.add(policy)
    db.flush()
    return policy


def _add_result(
    db: Session,
    experiment: Experiment,
    decision: str,
    *,
    absolute_lift: float,
    p_value: float,
) -> ExperimentResult:
    result = ExperimentResult(
        experiment_id=experiment.id,
        control_count=1000,
        treatment_count=200,
        control_conversions=500,
        treatment_conversions=100,
        control_rate=0.5,
        treatment_rate=0.5 + absolute_lift,
        absolute_lift=absolute_lift,
        relative_lift=absolute_lift / 0.5,
        p_value=p_value,
        confidence_interval_lower=absolute_lift - 0.05,
        confidence_interval_upper=absolute_lift + 0.05,
        is_significant=p_value < 0.05,
        decision=decision,
        decided_at=(experiment.ended_at or experiment.created_at),
    )
    db.add(result)
    db.flush()
    return result


def test_empty_memory_for_valid_merchant_and_missing_merchant_error(db: Session):
    memory = get_merchant_experiment_memory(db, "merchant-a")
    assert memory.merchant_id == "merchant-a"
    assert memory.records == ()
    assert memory.knowledge == ()
    assert memory.trial_count == 0

    with pytest.raises(MerchantMemoryNotFoundError):
        get_merchant_experiment_memory(db, "missing")


def test_completed_approved_experiment_becomes_structured_memory(db: Session):
    experiment = _make_experiment(
        db,
        experiment_id="exp-1",
        treatment_config={"accept_partial": True, "first_min_partial_amount_pct": 0.25},
    )
    _add_policy(db, experiment, "APPROVE")
    _add_result(db, experiment, "INCONCLUSIVE", absolute_lift=-0.009, p_value=0.8071)
    db.add(
        RazorpayResource(
            experiment_id=experiment.id,
            variant="treatment",
            resource_type="payment_link",
            razorpay_id="demo_plink_memory",
            config={},
            status="active",
        )
    )
    db.flush()

    memory = get_merchant_experiment_memory(db, "merchant-a")

    assert memory.trial_count == 1
    assert memory.completed_result_count == 1
    assert memory.inconclusive_count == 1
    record = memory.records[0]
    assert record.experiment_id == "exp-1"
    assert record.segment == "android_budget"
    assert record.intervention_type == "partial_payment"
    assert record.policy_decision == "APPROVE"
    assert record.policy_violations == ()
    assert record.statistical_decision == "INCONCLUSIVE"
    assert record.absolute_lift == pytest.approx(-0.009)
    assert record.p_value == pytest.approx(0.8071)
    assert record.treatment_resource_status == "active"
    assert record.terminal_reason == "statistical_inconclusive"
    assert record.treatment_config_fingerprint == treatment_config_fingerprint(
        {"first_min_partial_amount_pct": 0.25, "accept_partial": True}
    )


def test_policy_rejection_is_memory_even_without_statistical_result(db: Session):
    experiment = _make_experiment(db, experiment_id="exp-rejected", status="proposed")
    _add_policy(db, experiment, "REJECT", violations=["MAX_DISCOUNT_EXCEEDED"])

    memory = get_merchant_experiment_memory(db, "merchant-a")

    assert memory.trial_count == 1
    assert memory.completed_result_count == 0
    assert memory.policy_rejection_count == 1
    record = memory.records[0]
    assert record.experiment_status == "proposed"
    assert record.policy_decision == "REJECT"
    assert record.policy_violations == ("MAX_DISCOUNT_EXCEEDED",)
    assert record.statistical_decision is None
    assert record.terminal_reason == "policy_rejected"


def test_active_approved_and_running_experiments_are_not_learned(db: Session):
    approved = _make_experiment(db, experiment_id="exp-approved", status="approved")
    _add_policy(db, approved, "APPROVE")
    _make_experiment(db, experiment_id="exp-running", status="running")

    memory = get_merchant_experiment_memory(db, "merchant-a")

    assert memory.records == ()
    assert memory.trial_count == 0


def test_cancelled_terminal_experiment_is_remembered_without_result(db: Session):
    experiment = _make_experiment(db, experiment_id="exp-cancelled", status="cancelled")
    _add_policy(db, experiment, "APPROVE")

    memory = get_merchant_experiment_memory(db, "merchant-a")

    assert memory.trial_count == 1
    assert memory.records[0].terminal_reason == "cancelled"
    assert memory.records[0].statistical_decision is None


def test_knowledge_aggregates_trials_and_uses_latest_trial(db: Session):
    first = _make_experiment(
        db,
        experiment_id="exp-first",
        treatment_config={"accept_partial": True, "first_min_partial_amount_pct": 0.25},
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    _add_policy(db, first, "APPROVE")
    _add_result(db, first, "INCONCLUSIVE", absolute_lift=-0.01, p_value=0.8)

    second = _make_experiment(
        db,
        experiment_id="exp-second",
        treatment_config={"accept_partial": True, "first_min_partial_amount_pct": 0.5},
        created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    _add_policy(db, second, "APPROVE")
    _add_result(db, second, "KEEP", absolute_lift=0.04, p_value=0.01)

    rejected = _make_experiment(
        db,
        experiment_id="exp-rejected",
        treatment_config={"accept_partial": True, "first_min_partial_amount_pct": 0.75},
        status="proposed",
        created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )
    _add_policy(db, rejected, "REJECT", violations=["FINANCIAL_EXPOSURE_EXCEEDED"])

    memory = get_merchant_experiment_memory(db, "merchant-a")

    assert memory.trial_count == 3
    assert memory.keep_count == 1
    assert memory.inconclusive_count == 1
    assert memory.policy_rejection_count == 1
    assert len(memory.knowledge) == 1
    knowledge = memory.knowledge[0]
    assert knowledge.segment == "android_budget"
    assert knowledge.intervention_type == "partial_payment"
    assert knowledge.trial_count == 3
    assert knowledge.approved_count == 2
    assert knowledge.rejected_count == 1
    assert knowledge.completed_result_count == 2
    assert knowledge.keep_count == 1
    assert knowledge.inconclusive_count == 1
    assert knowledge.latest_outcome == "policy_rejected"
    assert knowledge.latest_experiment_id == "exp-rejected"
    assert knowledge.latest_treatment_config["first_min_partial_amount_pct"] == 0.75
    assert knowledge.latest_absolute_lift is None


def test_latest_policy_decision_wins_deterministically(db: Session):
    experiment = _make_experiment(db, experiment_id="exp-policy")
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _add_policy(
        db,
        experiment,
        "REJECT",
        violations=["OLD"],
        evaluated_at=base,
        row_id="policy-a",
    )
    _add_policy(
        db,
        experiment,
        "APPROVE",
        evaluated_at=base + timedelta(seconds=1),
        row_id="policy-b",
    )
    _add_result(db, experiment, "KEEP", absolute_lift=0.03, p_value=0.01)

    memory = get_merchant_experiment_memory(db, "merchant-a")

    assert memory.records[0].policy_decision == "APPROVE"
    assert memory.records[0].policy_violations == ()


def test_fingerprint_and_equivalent_trial_matching_are_canonical(db: Session):
    left = {"accept_partial": True, "first_min_partial_amount_pct": 0.25}
    right = {"first_min_partial_amount_pct": 0.25, "accept_partial": True}
    assert treatment_config_fingerprint(left) == treatment_config_fingerprint(right)

    experiment = _make_experiment(db, experiment_id="exp-equivalent", treatment_config=left)
    _add_policy(db, experiment, "APPROVE")
    _add_result(db, experiment, "INCONCLUSIVE", absolute_lift=0.0, p_value=1.0)
    memory = get_merchant_experiment_memory(db, "merchant-a")

    matches = find_equivalent_trials(
        memory,
        segment="android_budget",
        intervention_type="partial_payment",
        treatment_config=right,
    )
    assert [row.experiment_id for row in matches] == ["exp-equivalent"]
    assert (
        find_equivalent_trials(
            memory,
            segment="android_mid",
            intervention_type="partial_payment",
            treatment_config=right,
        )
        == ()
    )


def test_memory_is_merchant_isolated_and_read_only(db: Session, monkeypatch: pytest.MonkeyPatch):
    own = _make_experiment(db, experiment_id="exp-own", merchant_id="merchant-a")
    _add_policy(db, own, "APPROVE")
    _add_result(db, own, "INCONCLUSIVE", absolute_lift=0.0, p_value=1.0)

    foreign = _make_experiment(db, experiment_id="exp-foreign", merchant_id="merchant-b")
    _add_policy(db, foreign, "APPROVE")
    _add_result(db, foreign, "KEEP", absolute_lift=0.04, p_value=0.01)
    db.flush()

    original_status = own.status
    commit_calls: list[bool] = []
    monkeypatch.setattr(db, "commit", lambda: commit_calls.append(True))

    memory = get_merchant_experiment_memory(db, "merchant-a")

    assert [row.experiment_id for row in memory.records] == ["exp-own"]
    assert own.status == original_status
    assert commit_calls == []


def test_memory_service_has_no_forbidden_runtime_dependencies():
    text = Path(__file__).parents[1].joinpath("app/services/memory.py").read_text()
    for forbidden in (
        "causal_model",
        "OpenAI",
        "RazorpayClient",
        "simulate_outcome",
        "record_audit_event",
        ".commit(",
    ):
        assert forbidden not in text
