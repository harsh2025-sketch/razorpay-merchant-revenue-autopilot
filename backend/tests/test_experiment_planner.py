"""Tests for Task 09: deterministic experiment planner.

All tests are fully offline. The TechBazaar integration chain uses an
injected fake diagnosis client (Task 08's OpenAI network boundary is never
touched), never calls Razorpay, and never accesses the sealed causal model.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import (
    Experiment,
    Hypothesis,
    Merchant,
    MerchantPolicy,
    Opportunity,
    PaymentAttempt,
)
from app.engines.diagnosis import build_evidence_catalog, diagnose_opportunity
from app.engines.opportunities import run_opportunity_detection
from app.engines.planner import (
    ExperimentPlanningError,
    build_experiment_plan,
    persist_experiment_plan,
    plan_experiment,
)
from app.schemas.experiment import (
    DEFAULT_GUARDRAILS,
    DEFAULT_MAX_DURATION_HOURS,
    DEFAULT_MIN_SAMPLE_PER_VARIANT,
    DEFAULT_PRIMARY_METRIC,
    DEFAULT_TREATMENT_EXPOSURE,
    ExperimentPlan,
)
from app.simulation.generator import generate_baseline_events
from app.simulation.merchant import TECHBAZAAR_PROFILE
from tests.test_diagnosis_engine import (
    ALL_INTERVENTIONS,
    MOCK_MODEL_RESPONSE,
    FakeOpenAIClient,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
PLANNER_PATH = BACKEND_DIR / "app" / "engines" / "planner.py"
EXPERIMENT_SCHEMA_PATH = BACKEND_DIR / "app" / "schemas" / "experiment.py"

FORBIDDEN_PLAN_FIELDS = (
    "policy_decision",
    "approved",
    "razorpay_id",
    "p_value",
    "winner",
    "expected_lift",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session(tmp_path):
    """Temporary SQLite session (no FK pragma so malformed-row scenarios
    referencing missing opportunities can be constructed defensively)."""
    db_file = tmp_path / "test_planner.db"
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detached_opportunity(**overrides) -> Opportunity:
    defaults = dict(
        id="opp_test",
        merchant_id="merchant_test",
        type="segment_conversion_divergence",
        segment="android_budget",
        severity=0.07,
        detected_metric="conversion_rate",
        detected_value=0.472,
        baseline_value=0.586,
        evidence={},
        status="detected",
    )
    defaults.update(overrides)
    return Opportunity(**defaults)


def _detached_hypothesis(**overrides) -> Hypothesis:
    defaults = dict(
        id="hyp_test",
        opportunity_id="opp_test",
        merchant_id="merchant_test",
        ai_model="test-model",
        hypothesis_text="A testable hypothesis.",
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.10},
        confidence="medium",
        reasoning_summary="Rationale.",
        evidence_refs=["segment_conversion_rate"],
        status="proposed",
    )
    defaults.update(overrides)
    return Hypothesis(**defaults)


def make_merchant(
    db, merchant_id: str, *, with_policy: bool = True, **policy_overrides
) -> Merchant:
    merchant = Merchant(id=merchant_id, name=f"Merchant {merchant_id}")
    db.add(merchant)
    if with_policy:
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
        defaults.update(policy_overrides)
        db.add(
            MerchantPolicy(
                id=f"policy_{merchant_id}",
                merchant_id=merchant_id,
                **defaults,
            )
        )
    db.flush()
    return merchant


def make_opportunity_row(
    db, merchant_id: str, *, segment: str | None = "android_budget"
) -> Opportunity:
    opp = Opportunity(
        merchant_id=merchant_id,
        type="segment_conversion_divergence",
        segment=segment,
        severity=0.07,
        detected_metric="conversion_rate",
        detected_value=0.472,
        baseline_value=0.586,
        evidence={"segment_conversion_rate": 0.472},
        status="detected",
    )
    db.add(opp)
    db.flush()
    return opp


def make_hypothesis_row(
    db,
    opportunity: Opportunity,
    *,
    merchant_id: str | None = None,
    intervention_type: str = "offer_discount",
    intervention_params: dict | None = None,
    status: str = "proposed",
) -> Hypothesis:
    hypothesis = Hypothesis(
        opportunity_id=opportunity.id,
        merchant_id=merchant_id if merchant_id is not None else opportunity.merchant_id,
        ai_model="test-model",
        hypothesis_text="A testable hypothesis.",
        intervention_type=intervention_type,
        intervention_params=(
            intervention_params
            if intervention_params is not None
            else {"discount_pct": 0.10}
        ),
        confidence="medium",
        reasoning_summary="Rationale.",
        evidence_refs=["segment_conversion_rate"],
        status=status,
    )
    db.add(hypothesis)
    db.flush()
    return hypothesis


# ===========================================================================
# 1-3. payment_method_config mapping
# ===========================================================================


def test_payment_method_control_config_is_merchant_default():
    hypothesis = _detached_hypothesis(
        intervention_type="payment_method_config",
        intervention_params={"card": False, "upi": True},
    )
    plan = build_experiment_plan(
        hypothesis=hypothesis, opportunity=_detached_opportunity()
    )
    assert plan.control_config == {"payment_methods": "merchant_default"}


def test_payment_method_treatment_preserves_semantic_flags():
    hypothesis = _detached_hypothesis(
        intervention_type="payment_method_config",
        intervention_params={"card": False, "upi": True},
    )
    plan = build_experiment_plan(
        hypothesis=hypothesis, opportunity=_detached_opportunity()
    )
    assert plan.treatment_config == {"payment_methods": {"card": False, "upi": True}}

    # Only keys present in the hypothesis params are preserved.
    sparse = _detached_hypothesis(
        id="hyp_sparse",
        intervention_type="payment_method_config",
        intervention_params={"upi": True},
    )
    plan_sparse = build_experiment_plan(
        hypothesis=sparse, opportunity=_detached_opportunity()
    )
    assert plan_sparse.treatment_config == {"payment_methods": {"upi": True}}

    # No raw Razorpay payload shapes may appear.
    for config in (plan.control_config, plan.treatment_config):
        assert "options" not in config
        assert "checkout" not in config


def test_payment_method_rejects_unsupported_keys_from_malformed_db_hypothesis(
    db_session,
):
    db = db_session
    merchant = make_merchant(db, "m_bad_pm")
    opp = make_opportunity_row(db, merchant.id)

    for bad_params in (
        {"upi": True, "paypal": True},
        {"options": {"upi": True}},
        {"checkout": {"order": {"amount": 100000}}},
        {"card": True, "razorpay_payload": {}},
    ):
        hypothesis = make_hypothesis_row(
            db,
            opp,
            intervention_type="payment_method_config",
            intervention_params=bad_params,
        )
        with pytest.raises(ExperimentPlanningError, match="unsupported"):
            plan_experiment(db, hypothesis.id)
        assert db.query(Experiment).count() == 0


def test_payment_method_rejects_non_boolean_values():
    for bad_value in ("yes", 1, 0, None):
        hypothesis = _detached_hypothesis(
            intervention_type="payment_method_config",
            intervention_params={"upi": bad_value},
        )
        with pytest.raises(ExperimentPlanningError, match="boolean"):
            build_experiment_plan(
                hypothesis=hypothesis, opportunity=_detached_opportunity()
            )


# ===========================================================================
# 4-6. offer_discount mapping
# ===========================================================================


def test_offer_discount_control_is_offer_none():
    hypothesis = _detached_hypothesis(
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.10},
    )
    plan = build_experiment_plan(
        hypothesis=hypothesis, opportunity=_detached_opportunity()
    )
    assert plan.control_config == {"offer": None}


def test_offer_discount_treatment_preserves_discount_pct():
    hypothesis = _detached_hypothesis(
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.10},
    )
    plan = build_experiment_plan(
        hypothesis=hypothesis, opportunity=_detached_opportunity()
    )
    assert plan.treatment_config == {"discount_pct": 0.10}
    # No Razorpay Offer ID is invented in Task 09.
    assert "offer_id" not in plan.treatment_config
    assert "razorpay_offer_id" not in plan.treatment_config


def test_twenty_percent_discount_remains_twenty_percent_pure():
    """Pure proof that merchant policy is NOT applied by the planner."""
    hypothesis = _detached_hypothesis(
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.20},
    )
    plan = build_experiment_plan(
        hypothesis=hypothesis, opportunity=_detached_opportunity()
    )
    assert plan.treatment_config == {"discount_pct": 0.20}


# ===========================================================================
# 7-9. partial_payment mapping
# ===========================================================================


def test_partial_payment_configs_correct():
    hypothesis = _detached_hypothesis(
        intervention_type="partial_payment",
        intervention_params={
            "accept_partial": True,
            "first_min_partial_amount_pct": 0.25,
        },
    )
    plan = build_experiment_plan(
        hypothesis=hypothesis, opportunity=_detached_opportunity()
    )
    assert plan.control_config == {"accept_partial": False}
    assert plan.treatment_config == {
        "accept_partial": True,
        "first_min_partial_amount_pct": 0.25,
    }


def test_partial_payment_without_first_min_pct_works():
    hypothesis = _detached_hypothesis(
        intervention_type="partial_payment",
        intervention_params={"accept_partial": True},
    )
    plan = build_experiment_plan(
        hypothesis=hypothesis, opportunity=_detached_opportunity()
    )
    assert plan.control_config == {"accept_partial": False}
    assert plan.treatment_config == {"accept_partial": True}


def test_invalid_partial_config_rejected_defensively():
    bad_param_cases = [
        {"first_min_partial_amount_pct": 0.25},  # accept_partial missing
        {"accept_partial": False, "first_min_partial_amount_pct": 0.25},
        {"accept_partial": "yes"},
        {"accept_partial": True, "first_min_partial_amount_pct": 1.5},
        {"accept_partial": True, "first_min_partial_amount_pct": 0},
        {"accept_partial": True, "first_min_partial_amount_pct": "25"},
        {"accept_partial": True, "first_min_partial_amount_paise": 50000},
    ]
    for bad_params in bad_param_cases:
        hypothesis = _detached_hypothesis(
            intervention_type="partial_payment",
            intervention_params=bad_params,
        )
        with pytest.raises(ExperimentPlanningError):
            build_experiment_plan(
                hypothesis=hypothesis, opportunity=_detached_opportunity()
            )


# ===========================================================================
# 10-11. expiry_config mapping
# ===========================================================================


def test_expiry_control_is_merchant_default():
    hypothesis = _detached_hypothesis(
        intervention_type="expiry_config",
        intervention_params={"expiry_hours": 4},
    )
    plan = build_experiment_plan(
        hypothesis=hypothesis, opportunity=_detached_opportunity()
    )
    assert plan.control_config == {"expiry_hours": "merchant_default"}


def test_expiry_treatment_hours_correct():
    hypothesis = _detached_hypothesis(
        intervention_type="expiry_config",
        intervention_params={"expiry_hours": 4},
    )
    plan = build_experiment_plan(
        hypothesis=hypothesis, opportunity=_detached_opportunity()
    )
    assert plan.treatment_config == {"expiry_hours": 4}


# ===========================================================================
# 12-16. Code-controlled planning constants
# ===========================================================================


def test_traffic_split_is_exactly_ten_percent():
    plan = build_experiment_plan(
        hypothesis=_detached_hypothesis(), opportunity=_detached_opportunity()
    )
    assert plan.traffic_split_treatment_pct == 0.10
    assert DEFAULT_TREATMENT_EXPOSURE == 0.10


def test_primary_metric_is_exactly_conversion_rate():
    plan = build_experiment_plan(
        hypothesis=_detached_hypothesis(), opportunity=_detached_opportunity()
    )
    assert plan.primary_metric == "conversion_rate"
    assert DEFAULT_PRIMARY_METRIC == "conversion_rate"


def test_guardrails_are_exact_labels():
    plan = build_experiment_plan(
        hypothesis=_detached_hypothesis(), opportunity=_detached_opportunity()
    )
    assert plan.guardrail_metrics == [
        "captured_gmv",
        "failure_rate",
        "abandonment_rate",
    ]
    assert list(DEFAULT_GUARDRAILS) == [
        "captured_gmv",
        "failure_rate",
        "abandonment_rate",
    ]


def test_min_sample_per_variant_is_200():
    plan = build_experiment_plan(
        hypothesis=_detached_hypothesis(), opportunity=_detached_opportunity()
    )
    assert plan.min_sample_per_variant == 200
    assert DEFAULT_MIN_SAMPLE_PER_VARIANT == 200


def test_max_duration_is_72_hours():
    plan = build_experiment_plan(
        hypothesis=_detached_hypothesis(), opportunity=_detached_opportunity()
    )
    assert plan.max_duration_hours == 72
    assert DEFAULT_MAX_DURATION_HOURS == 72


# ===========================================================================
# 17-18. Deterministic name and strict schema
# ===========================================================================


def test_experiment_name_is_deterministic():
    hypothesis = _detached_hypothesis(
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.10},
    )
    opportunity = _detached_opportunity(segment="android_budget")

    plan_a = build_experiment_plan(hypothesis=hypothesis, opportunity=opportunity)
    plan_b = build_experiment_plan(hypothesis=hypothesis, opportunity=opportunity)

    assert plan_a.name == plan_b.name
    assert plan_a.name == "android_budget-offer_discount"

    pm_hypothesis = _detached_hypothesis(
        id="hyp_pm",
        intervention_type="payment_method_config",
        intervention_params={"upi": True},
    )
    plan_c = build_experiment_plan(hypothesis=pm_hypothesis, opportunity=opportunity)
    assert plan_c.name == "android_budget-payment_method_config"


def test_experiment_plan_forbids_extra_fields():
    base = {
        "merchant_id": "merchant_test",
        "hypothesis_id": "hyp_test",
        "opportunity_id": "opp_test",
        "name": "android_budget-offer_discount",
        "segment": "android_budget",
        "intervention_type": "offer_discount",
        "control_config": {"offer": None},
        "treatment_config": {"discount_pct": 0.10},
        "traffic_split_treatment_pct": 0.10,
        "primary_metric": "conversion_rate",
        "guardrail_metrics": ["captured_gmv", "failure_rate", "abandonment_rate"],
        "min_sample_per_variant": 200,
        "max_duration_hours": 72,
    }
    # Sanity: the base payload is a valid plan.
    assert ExperimentPlan.model_validate(base).name == base["name"]

    for field in FORBIDDEN_PLAN_FIELDS:
        with pytest.raises(PydanticValidationError):
            ExperimentPlan.model_validate({**base, field: "anything"})

    # The schema must not even declare these fields.
    for field in FORBIDDEN_PLAN_FIELDS:
        assert field not in ExperimentPlan.model_fields


# ===========================================================================
# 19-26. Defensive validation via plan_experiment / build_experiment_plan
# ===========================================================================


def test_missing_hypothesis_raises(db_session):
    with pytest.raises(ExperimentPlanningError, match="Hypothesis not found"):
        plan_experiment(db_session, "hypothesis_does_not_exist")


def test_missing_opportunity_raises(db_session):
    db = db_session
    merchant = make_merchant(db, "m_no_opp", with_policy=False)
    orphan = Hypothesis(
        opportunity_id="opportunity_missing",
        merchant_id=merchant.id,
        hypothesis_text="Orphan hypothesis.",
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.10},
        status="proposed",
    )
    db.add(orphan)
    db.flush()

    with pytest.raises(ExperimentPlanningError, match="Opportunity not found"):
        plan_experiment(db, orphan.id)
    assert db.query(Experiment).count() == 0


def test_merchant_mismatch_raises(db_session):
    db = db_session
    make_merchant(db, "m_one", with_policy=False)
    merchant_b = make_merchant(db, "m_two", with_policy=False)
    opp = make_opportunity_row(db, merchant_b.id)
    hypothesis = make_hypothesis_row(db, opp, merchant_id="m_one")

    with pytest.raises(ExperimentPlanningError, match="merchant mismatch"):
        plan_experiment(db, hypothesis.id)

    with pytest.raises(ExperimentPlanningError, match="merchant mismatch"):
        build_experiment_plan(
            hypothesis=_detached_hypothesis(merchant_id="m_a"),
            opportunity=_detached_opportunity(merchant_id="m_b"),
        )


def test_hypothesis_not_proposed_raises(db_session):
    db = db_session
    merchant = make_merchant(db, "m_not_proposed", with_policy=False)
    opp = make_opportunity_row(db, merchant.id)
    hypothesis = make_hypothesis_row(db, opp, status="rejected")

    with pytest.raises(ExperimentPlanningError, match="proposed"):
        plan_experiment(db, hypothesis.id)
    assert db.query(Experiment).count() == 0


def test_missing_segment_raises(db_session):
    for bad_segment in (None, "", "   "):
        hypothesis = _detached_hypothesis()
        opportunity = _detached_opportunity(segment=bad_segment)
        with pytest.raises(ExperimentPlanningError, match="segment"):
            build_experiment_plan(hypothesis=hypothesis, opportunity=opportunity)

    # Also through the persisted path.
    db = db_session
    merchant = make_merchant(db, "m_no_segment", with_policy=False)
    opp = make_opportunity_row(db, merchant.id, segment=None)
    hypothesis_row = make_hypothesis_row(db, opp)
    with pytest.raises(ExperimentPlanningError, match="segment"):
        plan_experiment(db, hypothesis_row.id)


def test_unsupported_intervention_raises(db_session):
    db = db_session
    merchant = make_merchant(db, "m_bad_type", with_policy=False)
    opp = make_opportunity_row(db, merchant.id)
    hypothesis = make_hypothesis_row(db, opp, intervention_type="send_email")

    with pytest.raises(ExperimentPlanningError, match="unsupported intervention"):
        plan_experiment(db, hypothesis.id)
    assert db.query(Experiment).count() == 0


def test_malformed_offer_discount_rejected():
    for bad_params in (
        {"discount_pct": "ten"},
        {"discount_pct": True},
        {"discount_pct": 0},
        {"discount_pct": -0.05},
        {"discount_pct": 0.51},
        {"discount_pct": None},
        {"discount_pct": 0.10, "coupon_code": "SAVE10"},
        {},
    ):
        hypothesis = _detached_hypothesis(
            intervention_type="offer_discount",
            intervention_params=bad_params,
        )
        with pytest.raises(ExperimentPlanningError):
            build_experiment_plan(
                hypothesis=hypothesis, opportunity=_detached_opportunity()
            )


def test_malformed_expiry_rejected():
    for bad_params in (
        {"expiry_hours": 0},
        {"expiry_hours": -4},
        {"expiry_hours": 4321},
        {"expiry_hours": "4"},
        {"expiry_hours": True},
        {"expiry_hours": 4, "grace_minutes": 30},
        {},
    ):
        hypothesis = _detached_hypothesis(
            intervention_type="expiry_config",
            intervention_params=bad_params,
        )
        with pytest.raises(ExperimentPlanningError):
            build_experiment_plan(
                hypothesis=hypothesis, opportunity=_detached_opportunity()
            )


def test_hypothesis_not_linked_to_opportunity_raises():
    with pytest.raises(ExperimentPlanningError, match="not linked"):
        build_experiment_plan(
            hypothesis=_detached_hypothesis(opportunity_id="other_opp"),
            opportunity=_detached_opportunity(id="opp_test"),
        )


# ===========================================================================
# 27-28. Persistence: correct row, flush without commit
# ===========================================================================


def test_planner_persists_experiment_correctly(db_session):
    db = db_session
    merchant = make_merchant(db, "m_persist", with_policy=False)
    opp = make_opportunity_row(db, merchant.id, segment="android_budget")
    hypothesis = make_hypothesis_row(
        db,
        opp,
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.10},
    )

    experiment = plan_experiment(db, hypothesis.id)

    assert experiment.id is not None
    assert experiment.merchant_id == merchant.id
    assert experiment.hypothesis_id == hypothesis.id
    assert experiment.opportunity_id == opp.id
    assert experiment.name == "android_budget-offer_discount"
    assert experiment.segment == "android_budget"
    assert experiment.intervention_type == "offer_discount"
    assert experiment.control_config == {"offer": None}
    assert experiment.treatment_config == {"discount_pct": 0.10}
    assert experiment.traffic_split_treatment_pct == 0.10
    assert experiment.primary_metric == "conversion_rate"
    assert experiment.guardrail_metrics == [
        "captured_gmv",
        "failure_rate",
        "abandonment_rate",
    ]
    assert experiment.min_sample_per_variant == 200
    assert experiment.max_duration_hours == 72
    assert experiment.status == "proposed"
    assert experiment.started_at is None
    assert experiment.ended_at is None

    # Flush made the row visible inside this transaction.
    assert (
        db.query(Experiment).filter(Experiment.id == experiment.id).count() == 1
    )


def test_no_db_commit_occurs(db_session):
    db = db_session
    merchant = make_merchant(db, "m_nocommit", with_policy=False)
    opp = make_opportunity_row(db, merchant.id)
    hypothesis = make_hypothesis_row(db, opp)

    plan_experiment(db, hypothesis.id)

    # Runtime proof: a rollback wipes the row, so nothing was committed.
    db.rollback()
    assert db.query(Experiment).count() == 0

    # Static proof: the planner source never calls .commit().
    tree = ast.parse(PLANNER_PATH.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "commit"
        ):
            pytest.fail("planner.py calls .commit()")


# ===========================================================================
# 29-30. Duplicate suppression
# ===========================================================================


def test_duplicate_plan_returns_same_experiment_and_no_second_row(db_session):
    db = db_session
    merchant = make_merchant(db, "m_dup", with_policy=False)
    opp = make_opportunity_row(db, merchant.id)
    hypothesis = make_hypothesis_row(db, opp)

    first = plan_experiment(db, hypothesis.id)
    second = plan_experiment(db, hypothesis.id)

    assert second.id == first.id
    assert second is not None
    assert db.query(Experiment).count() == 1


def test_duplicate_suppression_returns_existing_regardless_of_status(db_session):
    """Task 09 keeps one Experiment per Hypothesis even after terminal states."""
    db = db_session
    merchant = make_merchant(db, "m_dup_status", with_policy=False)
    opp = make_opportunity_row(db, merchant.id)
    hypothesis = make_hypothesis_row(db, opp)

    first = plan_experiment(db, hypothesis.id)

    for terminal_status in ("completed", "rolled_back", "rejected", "cancelled"):
        first.status = terminal_status
        db.flush()
        again = plan_experiment(db, hypothesis.id)
        assert again.id == first.id
        assert again.status == terminal_status
        assert db.query(Experiment).count() == 1


# ===========================================================================
# 31-32. MerchantPolicy does NOT modify planner output
# ===========================================================================


def test_policy_max_exposure_does_not_modify_planner_output(db_session):
    db = db_session
    merchant = make_merchant(db, "m_exposure", max_experiment_exposure_pct=0.05)
    opp = make_opportunity_row(db, merchant.id)
    hypothesis = make_hypothesis_row(db, opp)

    plan = build_experiment_plan(
        hypothesis=_detached_hypothesis(), opportunity=_detached_opportunity()
    )
    assert plan.traffic_split_treatment_pct == 0.10

    experiment = plan_experiment(db, hypothesis.id)
    assert experiment.traffic_split_treatment_pct == 0.10


def test_policy_max_discount_does_not_modify_twenty_percent_discount(db_session):
    db = db_session
    merchant = make_merchant(db, "m_discount", max_discount_pct=0.15)
    opp = make_opportunity_row(db, merchant.id)
    hypothesis = make_hypothesis_row(
        db,
        opp,
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.20},
    )

    experiment = plan_experiment(db, hypothesis.id)
    assert experiment.treatment_config == {"discount_pct": 0.20}
    assert experiment.control_config == {"offer": None}


# ===========================================================================
# 33-37. Source hygiene: OpenAI / Razorpay / causal / policy isolation
# ===========================================================================


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def test_no_openai_import():
    for path in (PLANNER_PATH, EXPERIMENT_SCHEMA_PATH):
        modules = _imported_modules(path)
        for module in modules:
            assert "openai" not in module.lower(), f"{path.name} imports {module}"


def test_no_razorpay_import():
    for path in (PLANNER_PATH, EXPERIMENT_SCHEMA_PATH):
        modules = _imported_modules(path)
        for module in modules:
            assert "razorpay" not in module.lower(), f"{path.name} imports {module}"
        source = path.read_text(encoding="utf-8")
        assert "RazorpayClient" not in source, path


def test_no_causal_model_import():
    for path in (PLANNER_PATH, EXPERIMENT_SCHEMA_PATH):
        modules = _imported_modules(path)
        for module in modules:
            assert "causal_model" not in module, f"{path.name} imports {module}"
            assert "simulation" not in module, f"{path.name} imports {module}"


def test_no_simulate_outcome_reference():
    for path in (PLANNER_PATH, EXPERIMENT_SCHEMA_PATH):
        source = path.read_text(encoding="utf-8")
        assert "simulate_outcome" not in source, path
        assert "causal_model_fingerprint" not in source, path


def test_no_policy_engine_import():
    for path in (PLANNER_PATH, EXPERIMENT_SCHEMA_PATH):
        modules = _imported_modules(path)
        for module in modules:
            parts = module.split(".")
            assert not any(part == "policy" for part in parts), (
                f"{path.name} imports policy module {module}"
            )
            assert "MerchantPolicy" not in module, f"{path.name} imports {module}"


# ===========================================================================
# TechBazaar integration: Task 05 seed -> Task 07 detect -> Task 08 diagnose
# (fake client) -> Task 09 plan
# ===========================================================================


def test_techbazaar_plan_experiment_end_to_end(db_session):
    db = db_session

    # Task 05: deterministic TechBazaar baseline.
    events = generate_baseline_events(
        profile=TECHBAZAAR_PROFILE, seed=20260827, days=30
    )
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
            max_experiment_exposure_pct=0.05,  # stricter than planner default
            max_discount_pct=0.15,
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

    # Task 07: opportunity detection + persistence.
    opportunities = run_opportunity_detection(db, TECHBAZAAR_PROFILE.merchant_id)
    assert opportunities, "TechBazaar baseline should yield at least one opportunity"
    opportunity = opportunities[0]

    # Task 08: diagnosis with a FAKE structured model response (no live API).
    catalog = build_evidence_catalog(opportunity)
    payload = {
        **MOCK_MODEL_RESPONSE,
        "evidence_refs": [
            ref for ref in MOCK_MODEL_RESPONSE["evidence_refs"] if ref in catalog
        ]
        or ["segment_conversion_rate"],
    }
    hypothesis = diagnose_opportunity(db, opportunity.id, client=FakeOpenAIClient(payload))
    assert hypothesis.status == "proposed"

    # Task 09: deterministic planning of the validated hypothesis.
    experiment = plan_experiment(db, hypothesis.id)

    # Full chain: PaymentAttempt -> Opportunity -> Hypothesis -> Experiment.
    assert opportunity.merchant_id == TECHBAZAAR_PROFILE.merchant_id
    assert hypothesis.opportunity_id == opportunity.id
    assert experiment.merchant_id == TECHBAZAAR_PROFILE.merchant_id
    assert experiment.hypothesis_id == hypothesis.id
    assert experiment.opportunity_id == opportunity.id

    # Deterministic plan contents.
    assert experiment.name == f"{opportunity.segment}-{hypothesis.intervention_type}"
    assert experiment.segment == opportunity.segment
    assert experiment.intervention_type == hypothesis.intervention_type
    assert experiment.control_config == {"payment_methods": "merchant_default"}
    assert experiment.treatment_config == {
        "payment_methods": {"card": False, "upi": True}
    }
    assert experiment.traffic_split_treatment_pct == 0.10  # policy says 0.05: ignored
    assert experiment.primary_metric == "conversion_rate"
    assert experiment.guardrail_metrics == [
        "captured_gmv",
        "failure_rate",
        "abandonment_rate",
    ]
    assert experiment.min_sample_per_variant == 200
    assert experiment.max_duration_hours == 72
    assert experiment.status == "proposed"
    assert experiment.started_at is None
    assert experiment.ended_at is None

    # Duplicate suppression across the whole chain.
    again = plan_experiment(db, hypothesis.id)
    assert again.id == experiment.id
    assert db.query(Experiment).count() == 1

    # No commit: everything is rolled back by the fixture teardown anyway,
    # but a manual rollback here must wipe the experiment row.
    db.rollback()
    assert db.query(Experiment).count() == 0
