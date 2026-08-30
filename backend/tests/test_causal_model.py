"""Tests for Task 06: sealed causal intervention model."""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import ast
import re

from app.simulation.causal_model import (
    ALLOWED_STATUSES,
    InterventionSpec,
    PaymentContext,
    SimulatedOutcome,
    _clamp_probability,
    causal_model_fingerprint,
    simulate_outcome,
)
from app.simulation.merchant import TECHBAZAAR_PROFILE

COHORT_N = 5000
SEED = 20260827
ALT_SEED = 424242
LIFT_TOLERANCE = 0.03

SEGMENTS = ("android_mid", "android_budget", "web_general", "repeat_buyer", "ios_premium")

_FORBIDDEN_LEAK_FIELDS = {
    "expected_lift",
    "treatment_effect",
    "hidden_problem",
    "best_intervention",
    "causal_label",
}

_SEGMENT_DEFAULTS = {
    "android_mid": {"amount": 200_000, "device_type": "android", "payment_method": "card"},
    "android_budget": {"amount": 100_000, "device_type": "android", "payment_method": "upi"},
    "web_general": {"amount": 300_000, "device_type": "web", "payment_method": "card"},
    "repeat_buyer": {"amount": 600_000, "device_type": "android", "payment_method": "upi"},
    "ios_premium": {"amount": 1_000_000, "device_type": "ios", "payment_method": "card"},
}

PMC_QUALIFYING = InterventionSpec(
    intervention_type="payment_method_config",
    params={"card": False, "upi": True, "netbanking": True},
)
PMC_NON_QUALIFYING = InterventionSpec(
    intervention_type="payment_method_config",
    params={"card": True, "upi": True},
)
OFFER_5 = InterventionSpec(intervention_type="offer_discount", params={"discount_pct": 0.05})
OFFER_10 = InterventionSpec(intervention_type="offer_discount", params={"discount_pct": 0.10})
OFFER_15 = InterventionSpec(intervention_type="offer_discount", params={"discount_pct": 0.15})
PARTIAL_ON = InterventionSpec(intervention_type="partial_payment", params={"accept_partial": True})
EXPIRY_2H = InterventionSpec(intervention_type="expiry_config", params={"expiry_hours": 2})
EXPIRY_4H = InterventionSpec(intervention_type="expiry_config", params={"expiry_hours": 4})
EXPIRY_24H = InterventionSpec(intervention_type="expiry_config", params={"expiry_hours": 24})


def _context(segment: str, index: int, **overrides) -> PaymentContext:
    defaults = {
        "event_ref": f"test_{segment}_{index:06d}",
        "merchant_id": TECHBAZAAR_PROFILE.merchant_id,
        "customer_ref": f"cust_{index:06d}",
        "segment": segment,
        "currency": "INR",
        "source": "organic",
    }
    defaults.update(_SEGMENT_DEFAULTS[segment])
    defaults.update(overrides)
    return PaymentContext(**defaults)


def _cohort(segment: str, n: int = COHORT_N, **overrides) -> list[PaymentContext]:
    return [_context(segment, i, **overrides) for i in range(1, n + 1)]


def _capture_rate(
    contexts: list[PaymentContext],
    intervention: InterventionSpec | None,
    seed: int = SEED,
) -> float:
    captured = 0
    for ctx in contexts:
        outcome = simulate_outcome(context=ctx, intervention=intervention, seed=seed)
        if outcome.status == "captured":
            captured += 1
    return captured / len(contexts)


def _lift(
    contexts: list[PaymentContext],
    intervention: InterventionSpec,
    seed: int = SEED,
) -> float:
    return _capture_rate(contexts, intervention, seed) - _capture_rate(contexts, None, seed)


def _assert_near(actual: float, expected: float, tol: float = LIFT_TOLERANCE, msg: str = "") -> None:
    assert abs(actual - expected) <= tol, (
        f"{msg} observed={actual:.4f} expected={expected:.4f} tol={tol}"
    )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_same_inputs_produce_identical_outcome():
    ctx = _context("android_mid", 1)
    a = simulate_outcome(context=ctx, intervention=PMC_QUALIFYING, seed=SEED)
    b = simulate_outcome(context=ctx, intervention=PMC_QUALIFYING, seed=SEED)
    assert a == b


def test_changing_seed_changes_outcomes_across_cohort():
    contexts = _cohort("android_mid", n=400)
    differed = 0
    for ctx in contexts:
        a = simulate_outcome(context=ctx, intervention=None, seed=SEED)
        b = simulate_outcome(context=ctx, intervention=None, seed=ALT_SEED)
        if a != b:
            differed += 1
    assert differed > 20


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def test_causal_model_fingerprint_is_deterministic_64_hex():
    fp1 = causal_model_fingerprint()
    fp2 = causal_model_fingerprint()
    assert fp1 == fp2
    assert len(fp1) == 64
    assert re.fullmatch(r"[0-9a-f]{64}", fp1)


# ---------------------------------------------------------------------------
# Control / status / timing
# ---------------------------------------------------------------------------

def test_control_simulation_works_for_every_segment():
    for segment in SEGMENTS:
        ctx = _context(segment, 1)
        outcome = simulate_outcome(context=ctx, intervention=None, seed=SEED)
        assert isinstance(outcome, SimulatedOutcome)
        assert outcome.status in ALLOWED_STATUSES


def test_only_valid_statuses_produced():
    interventions = [None, PMC_QUALIFYING, OFFER_5, PARTIAL_ON, EXPIRY_2H]
    seen = set()
    for segment in SEGMENTS:
        for intervention in interventions:
            for i in range(1, 51):
                outcome = simulate_outcome(
                    context=_context(segment, i),
                    intervention=intervention,
                    seed=SEED,
                )
                seen.add(outcome.status)
                assert outcome.status in ALLOWED_STATUSES
    assert seen <= ALLOWED_STATUSES
    assert seen  # at least one status observed


def test_failure_reason_populated_only_for_failed():
    allowed_reasons = {
        "authentication_failed",
        "bank_declined",
        "insufficient_funds",
        "network_error",
        "payment_timeout",
        "unknown",
    }
    for segment in SEGMENTS:
        for i in range(1, 201):
            outcome = simulate_outcome(
                context=_context(segment, i),
                intervention=None,
                seed=SEED,
            )
            if outcome.status == "failed":
                assert outcome.failure_reason in allowed_reasons
            else:
                assert outcome.failure_reason is None


def test_completion_seconds_by_status():
    for segment in SEGMENTS:
        for i in range(1, 201):
            outcome = simulate_outcome(
                context=_context(segment, i),
                intervention=None,
                seed=SEED,
            )
            if outcome.status == "captured":
                assert outcome.completion_seconds is not None
                assert 5 <= outcome.completion_seconds <= 120
            elif outcome.status == "failed":
                assert outcome.completion_seconds is not None
                assert 2 <= outcome.completion_seconds <= 45
            else:
                assert outcome.status == "abandoned"
                assert outcome.completion_seconds is None


# ---------------------------------------------------------------------------
# Hidden effects - statistical cohort tests
# ---------------------------------------------------------------------------

def test_android_mid_qualifying_pmc_lift():
    lift = _lift(_cohort("android_mid"), PMC_QUALIFYING)
    _assert_near(lift, 0.13, msg="android_mid qualifying payment_method_config")


def test_android_mid_non_qualifying_pmc_no_lift():
    lift = _lift(_cohort("android_mid"), PMC_NON_QUALIFYING)
    _assert_near(lift, 0.0, msg="android_mid non-qualifying payment_method_config")


def test_payment_method_config_does_not_improve_other_segments():
    for segment in SEGMENTS:
        if segment == "android_mid":
            continue
        lift = _lift(_cohort(segment), PMC_QUALIFYING)
        _assert_near(lift, 0.0, msg=f"{segment} qualifying-shaped payment_method_config")


def test_android_budget_5pct_discount_lift():
    lift = _lift(_cohort("android_budget"), OFFER_5)
    _assert_near(lift, 0.08, msg="android_budget 5% discount")


def test_android_budget_10pct_discount_lift():
    lift = _lift(_cohort("android_budget"), OFFER_10)
    _assert_near(lift, 0.11, msg="android_budget 10% discount")


def test_android_budget_gt10pct_discount_lift():
    lift = _lift(_cohort("android_budget"), OFFER_15)
    _assert_near(lift, 0.12, msg="android_budget >10% discount")


def test_offer_discount_does_not_improve_web_general():
    lift = _lift(_cohort("web_general"), OFFER_10)
    _assert_near(lift, 0.0, msg="web_general offer_discount")


def test_web_general_null_for_all_intervention_types():
    contexts = _cohort("web_general")
    treatments = [
        ("payment_method_config", PMC_QUALIFYING),
        ("offer_discount", OFFER_15),
        ("partial_payment", PARTIAL_ON),
        ("expiry_config", EXPIRY_2H),
    ]
    for name, intervention in treatments:
        lift = _lift(contexts, intervention)
        _assert_near(lift, 0.0, msg=f"web_general {name}")


def test_repeat_buyer_partial_payment_large_amount_lift():
    contexts = _cohort("repeat_buyer", amount=600_000)
    lift = _lift(contexts, PARTIAL_ON)
    _assert_near(lift, 0.08, msg="repeat_buyer partial >= ₹5000")


def test_repeat_buyer_partial_payment_small_amount_no_lift():
    contexts = _cohort("repeat_buyer", amount=250_000)
    lift = _lift(contexts, PARTIAL_ON)
    _assert_near(lift, 0.0, msg="repeat_buyer partial < ₹5000")


def test_ios_premium_expiry_le_2h_negative_lift():
    lift = _lift(_cohort("ios_premium"), EXPIRY_2H)
    _assert_near(lift, -0.07, msg="ios_premium expiry <= 2h")


def test_ios_premium_expiry_3_to_6h_negative_lift():
    lift = _lift(_cohort("ios_premium"), EXPIRY_4H)
    _assert_near(lift, -0.03, msg="ios_premium expiry 3–6h")


def test_ios_premium_expiry_gt_6h_no_lift():
    lift = _lift(_cohort("ios_premium"), EXPIRY_24H)
    _assert_near(lift, 0.0, msg="ios_premium expiry > 6h")


def test_wrong_intervention_types_have_no_positive_hidden_effect():
    cases = [
        ("android_mid", OFFER_10),
        ("android_mid", PARTIAL_ON),
        ("android_mid", EXPIRY_2H),
        ("android_budget", PMC_QUALIFYING),
        ("android_budget", PARTIAL_ON),
        ("android_budget", EXPIRY_2H),
        ("repeat_buyer", PMC_QUALIFYING),
        ("repeat_buyer", OFFER_10),
        ("repeat_buyer", EXPIRY_2H),
        ("ios_premium", PMC_QUALIFYING),
        ("ios_premium", OFFER_10),
        ("ios_premium", PARTIAL_ON),
        ("web_general", PMC_QUALIFYING),
        ("web_general", OFFER_10),
        ("web_general", PARTIAL_ON),
        ("web_general", EXPIRY_2H),
    ]
    for segment, intervention in cases:
        extras = {}
        if segment == "repeat_buyer":
            extras["amount"] = 800_000
        lift = _lift(_cohort(segment, **extras), intervention)
        _assert_near(
            lift,
            0.0,
            msg=f"{segment} {intervention.intervention_type} should not receive hidden effect",
        )


# ---------------------------------------------------------------------------
# Clamp, hashing, leakage, side effects
# ---------------------------------------------------------------------------

def test_capture_probability_clamps_without_public_hidden_api():
    assert _clamp_probability(-1.0) == 0.02
    assert _clamp_probability(0.0) == 0.02
    assert _clamp_probability(0.02) == 0.02
    assert _clamp_probability(0.5) == 0.5
    assert _clamp_probability(0.98) == 0.98
    assert _clamp_probability(1.0) == 0.98
    assert _clamp_probability(2.0) == 0.98


def test_intervention_params_are_canonically_hashed():
    ctx = _context("android_mid", 42)
    a = InterventionSpec(
        intervention_type="payment_method_config",
        params={"card": False, "upi": True},
    )
    b = InterventionSpec(
        intervention_type="payment_method_config",
        params={"upi": True, "card": False},
    )
    out_a = simulate_outcome(context=ctx, intervention=a, seed=SEED)
    out_b = simulate_outcome(context=ctx, intervention=b, seed=SEED)
    assert out_a == out_b


def test_no_information_leakage_in_public_types():
    for cls in (PaymentContext, InterventionSpec, SimulatedOutcome):
        names = {f.name for f in fields(cls)}
        leaked = names & _FORBIDDEN_LEAK_FIELDS
        assert not leaked, f"{cls.__name__} leaked fields: {leaked}"
        for name in names:
            for term in _FORBIDDEN_LEAK_FIELDS:
                assert term not in name


def test_simulate_outcome_return_has_no_hidden_attributes():
    outcome = simulate_outcome(
        context=_context("android_mid", 1),
        intervention=PMC_QUALIFYING,
        seed=SEED,
    )
    for term in _FORBIDDEN_LEAK_FIELDS:
        assert not hasattr(outcome, term)
        assert term not in outcome.__dict__


def test_causal_model_has_no_db_or_network_side_effects():
    path = Path(__file__).resolve().parents[1] / "app" / "simulation" / "causal_model.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_roots = {
        "sqlalchemy",
        "httpx",
        "requests",
        "urllib",
        "socket",
        "psycopg",
        "aiohttp",
        "http",
        "ftplib",
        "smtplib",
        "subprocess",
        "pathlib",
    }
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
            if node.module.startswith("app.db") or node.module.startswith("app.services"):
                raise AssertionError(f"causal_model imports {node.module}")
    assert imported.isdisjoint(forbidden_roots), imported & forbidden_roots
    assert "app.db" not in source
    assert "razorpay" not in source.lower()
