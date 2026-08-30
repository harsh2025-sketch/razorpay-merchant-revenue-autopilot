"""Tests for Task 11: deterministic experiment runtime."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import (
    Experiment,
    ExperimentAssignment,
    Hypothesis,
    Merchant,
    MerchantPolicy,
    Opportunity,
    PaymentAttempt,
)
from app.engines.diagnosis import build_evidence_catalog, diagnose_opportunity
from app.engines.opportunities import run_opportunity_detection
from app.engines.planner import plan_experiment
from app.engines.policy import evaluate_experiment_policy
from app.simulation.causal_model import InterventionSpec, PaymentContext, simulate_outcome
from app.simulation.generator import generate_baseline_events
from app.simulation.merchant import TECHBAZAAR_PROFILE
from app.simulation.runner import (
    ExperimentRunSummary,
    ExperimentRuntimeError,
    assign_variant,
    run_experiment_batch,
)
from tests.test_diagnosis_engine import ALL_INTERVENTIONS, MOCK_MODEL_RESPONSE, FakeOpenAIClient

BACKEND_DIR = Path(__file__).resolve().parents[1]
RUNNER_PATH = BACKEND_DIR / "app" / "simulation" / "runner.py"
SERVICE_PATH = BACKEND_DIR / "app" / "services" / "experiments.py"

HIDDEN_SUMMARY_FIELDS = (
    "p_value",
    "significance",
    "winner",
    "lift",
    "expected_lift",
    "treatment_effect",
    "hidden_problem",
)


@pytest.fixture
def db_session(tmp_path):
    db_file = tmp_path / "test_runtime.db"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def make_merchant(db, merchant_id: str = TECHBAZAAR_PROFILE.merchant_id) -> Merchant:
    merchant = Merchant(
        id=merchant_id,
        name=TECHBAZAAR_PROFILE.name if merchant_id == TECHBAZAAR_PROFILE.merchant_id else merchant_id,
        category=TECHBAZAAR_PROFILE.category,
        monthly_gmv=TECHBAZAAR_PROFILE.monthly_gmv_paise,
    )
    db.add(merchant)
    db.add(
        MerchantPolicy(
            id=f"policy_{merchant_id}",
            merchant_id=merchant_id,
            max_experiment_exposure_pct=0.50,
            max_discount_pct=0.15,
            allowed_interventions=list(ALL_INTERVENTIONS),
        )
    )
    db.flush()
    return merchant


def make_opportunity(db, merchant_id: str, segment: str) -> Opportunity:
    opp = Opportunity(
        merchant_id=merchant_id,
        type="segment_conversion_divergence",
        segment=segment,
        severity=0.07,
        detected_metric="conversion_rate",
        detected_value=0.50,
        baseline_value=0.60,
        evidence={},
        status="detected",
    )
    db.add(opp)
    db.flush()
    return opp


def make_hypothesis(db, opportunity: Opportunity, intervention_type: str, params: dict) -> Hypothesis:
    hyp = Hypothesis(
        opportunity_id=opportunity.id,
        merchant_id=opportunity.merchant_id,
        hypothesis_text="test",
        intervention_type=intervention_type,
        intervention_params=params,
        status="proposed",
        evidence_refs=["segment_conversion_rate"],
    )
    db.add(hyp)
    db.flush()
    return hyp


def make_experiment(
    db,
    *,
    status: str = "approved",
    segment: str = "android_mid",
    intervention_type: str = "payment_method_config",
    treatment_config: dict | None = None,
    control_config: dict | None = None,
    traffic_split: float = 0.10,
    min_sample: int = 50,
    merchant_id: str = TECHBAZAAR_PROFILE.merchant_id,
) -> Experiment:
    make_merchant(db, merchant_id)
    opp = make_opportunity(db, merchant_id, segment)
    hyp = make_hypothesis(
        db,
        opp,
        intervention_type,
        treatment_config or {"card": False, "upi": True},
    )
    if treatment_config is None:
        if intervention_type == "payment_method_config":
            treatment_config = {"payment_methods": {"card": False, "upi": True}}
            control_config = control_config or {"payment_methods": "merchant_default"}
        elif intervention_type == "offer_discount":
            treatment_config = {"discount_pct": 0.05}
            control_config = control_config or {"offer": None}
        elif intervention_type == "partial_payment":
            treatment_config = {"accept_partial": True, "first_min_partial_amount_pct": 0.25}
            control_config = control_config or {"accept_partial": False}
        elif intervention_type == "expiry_config":
            treatment_config = {"expiry_hours": 2}
            control_config = control_config or {"expiry_hours": "merchant_default"}
    experiment = Experiment(
        merchant_id=merchant_id,
        hypothesis_id=hyp.id,
        opportunity_id=opp.id,
        name=f"{segment}-{intervention_type}",
        segment=segment,
        intervention_type=intervention_type,
        control_config=control_config or {},
        treatment_config=treatment_config,
        traffic_split_treatment_pct=traffic_split,
        primary_metric="conversion_rate",
        guardrail_metrics=["captured_gmv"],
        min_sample_per_variant=min_sample,
        max_duration_hours=72,
        status=status,
    )
    db.add(experiment)
    db.flush()
    return experiment


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------


def test_assign_variant_deterministic():
    a = assign_variant(experiment_id="e1", customer_ref="c1", treatment_pct=0.10)
    b = assign_variant(experiment_id="e1", customer_ref="c1", treatment_pct=0.10)
    assert a == b
    assert a in ("control", "treatment")


def test_same_customer_same_variant():
    for i in range(50):
        cust = f"cust_{i}"
        assert assign_variant(experiment_id="expA", customer_ref=cust, treatment_pct=0.25) == assign_variant(
            experiment_id="expA", customer_ref=cust, treatment_pct=0.25
        )


def test_different_experiment_id_changes_assignment_distribution():
    n = 2000
    a = [
        assign_variant(experiment_id="exp_one", customer_ref=f"c{i}", treatment_pct=0.10)
        for i in range(n)
    ]
    b = [
        assign_variant(experiment_id="exp_two", customer_ref=f"c{i}", treatment_pct=0.10)
        for i in range(n)
    ]
    assert a != b


def test_treatment_assignment_rate_approximately_configured_pct():
    n = 5000
    treatments = sum(
        1
        for i in range(n)
        if assign_variant(experiment_id="rate_exp", customer_ref=f"c{i}", treatment_pct=0.10)
        == "treatment"
    )
    rate = treatments / n
    assert 0.08 <= rate <= 0.12


def test_assignment_only_control_treatment():
    for i in range(200):
        v = assign_variant(experiment_id="only", customer_ref=f"c{i}", treatment_pct=0.5)
        assert v in ("control", "treatment")


# ---------------------------------------------------------------------------
# Status / batch size
# ---------------------------------------------------------------------------


def test_approved_experiment_becomes_running(db_session):
    exp = make_experiment(db_session, status="approved")
    summary = run_experiment_batch(db_session, exp.id, batch_size=10)
    assert exp.status == "running"
    assert summary.status == "running"


def test_running_experiment_can_continue_batch(db_session):
    exp = make_experiment(db_session, status="approved")
    run_experiment_batch(db_session, exp.id, batch_size=10)
    summary = run_experiment_batch(db_session, exp.id, batch_size=10)
    assert summary.generated_attempts == 10
    assert exp.status == "running"


@pytest.mark.parametrize("status", ["proposed", "rejected", "completed"])
def test_invalid_status_rejected(db_session, status):
    exp = make_experiment(db_session, status=status)
    with pytest.raises(ExperimentRuntimeError, match="status"):
        run_experiment_batch(db_session, exp.id, batch_size=5)


def test_invalid_batch_size_zero_rejected(db_session):
    exp = make_experiment(db_session)
    with pytest.raises(ExperimentRuntimeError, match="batch_size"):
        run_experiment_batch(db_session, exp.id, batch_size=0)


def test_bool_batch_size_rejected(db_session):
    exp = make_experiment(db_session)
    with pytest.raises(ExperimentRuntimeError, match="batch_size"):
        run_experiment_batch(db_session, exp.id, batch_size=True)  # type: ignore[arg-type]


def test_batch_size_over_5000_rejected(db_session):
    exp = make_experiment(db_session)
    with pytest.raises(ExperimentRuntimeError, match="5000"):
        run_experiment_batch(db_session, exp.id, batch_size=5001)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_runtime_creates_experiment_assignment_rows(db_session):
    exp = make_experiment(db_session)
    run_experiment_batch(db_session, exp.id, batch_size=20)
    assert db_session.query(ExperimentAssignment).filter_by(experiment_id=exp.id).count() > 0


def test_unique_customer_assignment_preserved(db_session):
    exp = make_experiment(db_session, segment="repeat_buyer", intervention_type="partial_payment")
    run_experiment_batch(db_session, exp.id, batch_size=80)
    rows = db_session.query(ExperimentAssignment).filter_by(experiment_id=exp.id).all()
    keys = [(r.experiment_id, r.customer_ref) for r in rows]
    assert len(keys) == len(set(keys))
    by_cust = {r.customer_ref: r.variant for r in rows}
    attempts = db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id).all()
    for att in attempts:
        assert att.variant == by_cust[att.customer_ref]


def test_runtime_creates_payment_attempt_rows(db_session):
    exp = make_experiment(db_session)
    summary = run_experiment_batch(db_session, exp.id, batch_size=15)
    assert summary.generated_attempts == 15
    assert db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id).count() == 15


def test_payment_attempt_experiment_id_correct(db_session):
    exp = make_experiment(db_session)
    run_experiment_batch(db_session, exp.id, batch_size=8)
    for att in db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id):
        assert att.experiment_id == exp.id


def test_payment_attempt_variant_correct(db_session):
    exp = make_experiment(db_session)
    run_experiment_batch(db_session, exp.id, batch_size=20)
    for att in db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id):
        assert att.variant in ("control", "treatment")


def test_is_simulated_true(db_session):
    exp = make_experiment(db_session)
    run_experiment_batch(db_session, exp.id, batch_size=8)
    for att in db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id):
        assert att.is_simulated is True


def test_no_razorpay_ids_generated(db_session):
    exp = make_experiment(db_session)
    run_experiment_batch(db_session, exp.id, batch_size=8)
    for att in db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id):
        assert att.razorpay_order_id is None
        assert att.razorpay_payment_id is None
        assert att.razorpay_payment_link_id is None


def test_deterministic_payment_attempt_ids(db_session):
    exp = make_experiment(db_session)
    run_experiment_batch(db_session, exp.id, batch_size=5, seed=20260827)
    ids_first = sorted(
        a.id for a in db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id)
    )
    expected = [f"exp_{exp.id}_event_{i:06d}" for i in range(1, 6)]
    assert ids_first == expected


def test_no_duplicate_ids_across_second_batch(db_session):
    exp = make_experiment(db_session)
    run_experiment_batch(db_session, exp.id, batch_size=5)
    run_experiment_batch(db_session, exp.id, batch_size=5)
    ids = [a.id for a in db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id)]
    assert len(ids) == len(set(ids)) == 10


def test_event_sequence_continues_across_batches(db_session):
    exp = make_experiment(db_session)
    run_experiment_batch(db_session, exp.id, batch_size=3)
    run_experiment_batch(db_session, exp.id, batch_size=3)
    ids = sorted(a.id for a in db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id))
    assert ids[-1] == f"exp_{exp.id}_event_000006"
    assert f"exp_{exp.id}_event_000001" in ids


def test_timestamps_timezone_aware(db_session):
    exp = make_experiment(db_session)
    run_experiment_batch(db_session, exp.id, batch_size=5)
    for att in db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id):
        assert att.created_at.tzinfo is not None
        assert att.created_at.tzinfo.utcoffset(att.created_at) is not None


def test_completion_timestamp_matches_completion_seconds(db_session, monkeypatch):
    from app.simulation import runner as runner_mod

    original = runner_mod.simulate_outcome

    def wrapped(*, context, intervention, seed=20260827):
        return original(context=context, intervention=intervention, seed=seed)

    monkeypatch.setattr(runner_mod, "simulate_outcome", wrapped)
    exp = make_experiment(db_session)
    run_experiment_batch(db_session, exp.id, batch_size=20)
    for att in db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id):
        if att.status == "abandoned":
            assert att.completed_at is None
        else:
            assert att.completed_at is not None
            delta = (att.completed_at - att.created_at).total_seconds()
            assert delta >= 2


# ---------------------------------------------------------------------------
# Intervention mapping
# ---------------------------------------------------------------------------


def test_control_uses_intervention_none(db_session, monkeypatch):
    from app.simulation import runner as runner_mod

    seen: list[InterventionSpec | None] = []
    original = runner_mod.simulate_outcome

    def spy(*, context, intervention, seed=20260827):
        seen.append(intervention)
        return original(context=context, intervention=intervention, seed=seed)

    monkeypatch.setattr(runner_mod, "simulate_outcome", spy)
    exp = make_experiment(db_session, traffic_split=0.10)
    run_experiment_batch(db_session, exp.id, batch_size=40)
    assert any(i is None for i in seen)
    attempts = db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id, variant="control").all()
    assert attempts
    # All control simulations used None.
    control_count = sum(1 for a in attempts)
    none_count = sum(1 for i in seen if i is None)
    assert none_count == control_count


def test_payment_method_config_treatment_mapped(db_session, monkeypatch):
    from app.simulation import runner as runner_mod

    seen: list = []
    original = runner_mod.simulate_outcome

    def spy(*, context, intervention, seed=20260827):
        seen.append(intervention)
        return original(context=context, intervention=intervention, seed=seed)

    monkeypatch.setattr(runner_mod, "simulate_outcome", spy)
    exp = make_experiment(
        db_session,
        intervention_type="payment_method_config",
        treatment_config={"payment_methods": {"card": False, "upi": True}},
        traffic_split=0.9,
    )
    run_experiment_batch(db_session, exp.id, batch_size=30)
    treatments = [i for i in seen if i is not None]
    assert treatments
    for spec in treatments:
        assert spec.intervention_type == "payment_method_config"
        assert spec.params["card"] is False
        assert spec.params["upi"] is True


def test_offer_discount_treatment_mapped(db_session, monkeypatch):
    from app.simulation import runner as runner_mod

    seen: list = []
    original = runner_mod.simulate_outcome

    def spy(*, context, intervention, seed=20260827):
        seen.append(intervention)
        return original(context=context, intervention=intervention, seed=seed)

    monkeypatch.setattr(runner_mod, "simulate_outcome", spy)
    exp = make_experiment(
        db_session,
        segment="android_budget",
        intervention_type="offer_discount",
        treatment_config={"discount_pct": 0.05},
        control_config={"offer": None},
        traffic_split=0.9,
    )
    run_experiment_batch(db_session, exp.id, batch_size=20)
    treatments = [i for i in seen if i is not None]
    assert treatments
    for spec in treatments:
        assert spec.intervention_type == "offer_discount"
        assert spec.params["discount_pct"] == 0.05


def test_partial_payment_treatment_mapped(db_session, monkeypatch):
    from app.simulation import runner as runner_mod

    seen: list = []
    original = runner_mod.simulate_outcome

    def spy(*, context, intervention, seed=20260827):
        seen.append(intervention)
        return original(context=context, intervention=intervention, seed=seed)

    monkeypatch.setattr(runner_mod, "simulate_outcome", spy)
    exp = make_experiment(
        db_session,
        segment="repeat_buyer",
        intervention_type="partial_payment",
        treatment_config={"accept_partial": True, "first_min_partial_amount_pct": 0.25},
        control_config={"accept_partial": False},
        traffic_split=0.9,
    )
    run_experiment_batch(db_session, exp.id, batch_size=20)
    treatments = [i for i in seen if i is not None]
    assert treatments
    for spec in treatments:
        assert spec.intervention_type == "partial_payment"
        assert spec.params["accept_partial"] is True


def test_expiry_config_treatment_mapped(db_session, monkeypatch):
    from app.simulation import runner as runner_mod

    seen: list = []
    original = runner_mod.simulate_outcome

    def spy(*, context, intervention, seed=20260827):
        seen.append(intervention)
        return original(context=context, intervention=intervention, seed=seed)

    monkeypatch.setattr(runner_mod, "simulate_outcome", spy)
    exp = make_experiment(
        db_session,
        segment="ios_premium",
        intervention_type="expiry_config",
        treatment_config={"expiry_hours": 4},
        control_config={"expiry_hours": "merchant_default"},
        traffic_split=0.9,
    )
    run_experiment_batch(db_session, exp.id, batch_size=20)
    treatments = [i for i in seen if i is not None]
    assert treatments
    for spec in treatments:
        assert spec.intervention_type == "expiry_config"
        assert spec.params["expiry_hours"] == 4


# ---------------------------------------------------------------------------
# Directional effects (large samples, no exact individual wins)
# ---------------------------------------------------------------------------


def _rates(db, exp_id: str) -> tuple[float, float]:
    control = db.query(PaymentAttempt).filter_by(experiment_id=exp_id, variant="control").all()
    treat = db.query(PaymentAttempt).filter_by(experiment_id=exp_id, variant="treatment").all()
    cr = sum(1 for a in control if a.status == "captured") / max(len(control), 1)
    tr = sum(1 for a in treat if a.status == "captured") / max(len(treat), 1)
    return cr, tr


def test_android_mid_treatment_directional_improvement(db_session):
    exp = make_experiment(
        db_session,
        segment="android_mid",
        intervention_type="payment_method_config",
        treatment_config={"payment_methods": {"card": False, "upi": True}},
        traffic_split=0.5,
        min_sample=400,
    )
    run_experiment_batch(db_session, exp.id, batch_size=1200)
    cr, tr = _rates(db_session, exp.id)
    assert tr > cr


def test_web_general_treatment_approximately_null(db_session):
    exp = make_experiment(
        db_session,
        segment="web_general",
        intervention_type="offer_discount",
        treatment_config={"discount_pct": 0.05},
        control_config={"offer": None},
        traffic_split=0.5,
        min_sample=400,
    )
    run_experiment_batch(db_session, exp.id, batch_size=1200)
    cr, tr = _rates(db_session, exp.id)
    assert abs(tr - cr) < 0.06


def test_ios_premium_short_expiry_directional_harm(db_session):
    exp = make_experiment(
        db_session,
        segment="ios_premium",
        intervention_type="expiry_config",
        treatment_config={"expiry_hours": 2},
        control_config={"expiry_hours": "merchant_default"},
        traffic_split=0.5,
        min_sample=400,
    )
    run_experiment_batch(db_session, exp.id, batch_size=1200)
    cr, tr = _rates(db_session, exp.id)
    assert tr < cr


# ---------------------------------------------------------------------------
# Stop condition / summary
# ---------------------------------------------------------------------------


def test_stop_generation_when_both_variant_targets_met(db_session):
    exp = make_experiment(db_session, traffic_split=0.5, min_sample=20)
    summary = run_experiment_batch(db_session, exp.id, batch_size=500)
    assert summary.control_attempts >= 20
    assert summary.treatment_attempts >= 20
    # Should not keep generating the entire batch once both met.
    assert summary.generated_attempts < 500


def test_runtime_never_sets_completed(db_session):
    exp = make_experiment(db_session, traffic_split=0.5, min_sample=10)
    run_experiment_batch(db_session, exp.id, batch_size=200)
    assert exp.status == "running"
    assert exp.status != "completed"


def test_summary_counts_correct(db_session):
    exp = make_experiment(db_session, traffic_split=0.5, min_sample=100)
    summary = run_experiment_batch(db_session, exp.id, batch_size=40)
    control = db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id, variant="control").count()
    treat = db_session.query(PaymentAttempt).filter_by(experiment_id=exp.id, variant="treatment").count()
    assert summary.control_attempts == control
    assert summary.treatment_attempts == treat
    assert summary.generated_attempts == control + treat
    assert summary.sample_target_per_variant == 100


def test_summary_remaining_never_negative(db_session):
    exp = make_experiment(db_session, traffic_split=0.5, min_sample=10)
    summary = run_experiment_batch(db_session, exp.id, batch_size=200)
    assert summary.control_remaining >= 0
    assert summary.treatment_remaining >= 0


def test_repeated_run_after_target_met_generates_zero(db_session):
    exp = make_experiment(db_session, traffic_split=0.5, min_sample=15)
    run_experiment_batch(db_session, exp.id, batch_size=200)
    again = run_experiment_batch(db_session, exp.id, batch_size=50)
    assert again.generated_attempts == 0
    assert again.status == "running"


def test_no_db_commit(db_session):
    exp = make_experiment(db_session)
    run_experiment_batch(db_session, exp.id, batch_size=5)
    db_session.rollback()
    assert db_session.query(PaymentAttempt).count() == 0

    tree = ast.parse(RUNNER_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "commit"
        ):
            pytest.fail("runner.py calls .commit()")


def test_no_openai_import():
    for path in (RUNNER_PATH, SERVICE_PATH):
        for module in _imported_modules(path):
            assert "openai" not in module.lower()


def test_no_razorpay_import():
    for path in (RUNNER_PATH, SERVICE_PATH):
        for module in _imported_modules(path):
            assert "razorpay" not in module.lower()


def test_runner_is_only_module_allowed_to_import_causal_model():
    service_modules = _imported_modules(SERVICE_PATH)
    for module in service_modules:
        assert "causal_model" not in module
    runner_modules = _imported_modules(RUNNER_PATH)
    assert any("causal_model" in m for m in runner_modules)


def test_no_hidden_causal_values_in_summary(db_session):
    exp = make_experiment(db_session)
    summary = run_experiment_batch(db_session, exp.id, batch_size=8)
    fields = set(summary.__dataclass_fields__)
    for hidden in HIDDEN_SUMMARY_FIELDS:
        assert hidden not in fields
    assert isinstance(summary, ExperimentRunSummary)


def test_customer_contexts_contain_no_hidden_fields(db_session, monkeypatch):
    from app.simulation import runner as runner_mod

    contexts: list[PaymentContext] = []
    original = runner_mod.simulate_outcome

    def spy(*, context, intervention, seed=20260827):
        contexts.append(context)
        return original(context=context, intervention=intervention, seed=seed)

    monkeypatch.setattr(runner_mod, "simulate_outcome", spy)
    exp = make_experiment(db_session)
    run_experiment_batch(db_session, exp.id, batch_size=5)
    allowed = {
        "event_ref",
        "merchant_id",
        "customer_ref",
        "segment",
        "amount",
        "currency",
        "payment_method",
        "device_type",
        "source",
    }
    for ctx in contexts:
        assert set(ctx.__dataclass_fields__) == allowed
        for hidden in ("expected_lift", "treatment_effect", "hidden_problem", "best_intervention"):
            assert not hasattr(ctx, hidden)


def test_techbazaar_full_chain_through_runtime(db_session):
    db = db_session
    events = generate_baseline_events(profile=TECHBAZAAR_PROFILE, seed=20260827, days=30)
    db.add(
        Merchant(
            id=TECHBAZAAR_PROFILE.merchant_id,
            name=TECHBAZAAR_PROFILE.name,
            category=TECHBAZAAR_PROFILE.category,
            monthly_gmv=TECHBAZAAR_PROFILE.monthly_gmv_paise,
        )
    )
    db.add(
        MerchantPolicy(
            id="policy_techbazaar",
            merchant_id=TECHBAZAAR_PROFILE.merchant_id,
            max_experiment_exposure_pct=0.10,
            max_discount_pct=0.15,
            max_financial_exposure=10**12,
            allowed_interventions=list(ALL_INTERVENTIONS),
        )
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

    opportunities = run_opportunity_detection(db, TECHBAZAAR_PROFILE.merchant_id)
    assert opportunities
    opportunity = opportunities[0]
    catalog = build_evidence_catalog(opportunity)
    payload = {
        **MOCK_MODEL_RESPONSE,
        "evidence_refs": [
            ref for ref in MOCK_MODEL_RESPONSE["evidence_refs"] if ref in catalog
        ]
        or ["segment_conversion_rate"],
    }
    hypothesis = diagnose_opportunity(db, opportunity.id, client=FakeOpenAIClient(payload))
    experiment = plan_experiment(db, hypothesis.id)
    decision = evaluate_experiment_policy(db, experiment.id)
    assert decision.decision == "APPROVE"
    assert experiment.status == "approved"

    summary = run_experiment_batch(db, experiment.id, batch_size=50)
    assert experiment.status == "running"
    assert summary.generated_attempts == 50
    runtime_attempts = (
        db.query(PaymentAttempt)
        .filter(PaymentAttempt.experiment_id == experiment.id)
        .all()
    )
    assert len(runtime_attempts) == 50
    assert all(a.is_simulated for a in runtime_attempts)
