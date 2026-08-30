"""Sealed causal intervention model for evaluation/simulation only.

This module encodes hidden treatment-effect rules used to simulate how
customers respond to CONTROL vs TREATMENT. It must never be imported by
Autopilot decision, metric, policy, or AI code.

Public output is a customer payment outcome only - never hidden causal
parameters, expected lift, or scenario labels.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping
import hashlib
import json
import random

from app.simulation.merchant import TECHBAZAAR_PROFILE, SegmentProfile

# ---------------------------------------------------------------------------
# Public, observable types
# ---------------------------------------------------------------------------

ALLOWED_STATUSES = frozenset({"captured", "failed", "abandoned"})
ALLOWED_INTERVENTION_TYPES = frozenset(
    {
        "payment_method_config",
        "offer_discount",
        "partial_payment",
        "expiry_config",
    }
)

_FORBIDDEN_PUBLIC_FIELDS = frozenset(
    {
        "expected_lift",
        "treatment_effect",
        "hidden_problem",
        "best_intervention",
        "causal_label",
    }
)


@dataclass(frozen=True)
class PaymentContext:
    """Observable customer/event characteristics for a simulated attempt."""

    event_ref: str
    merchant_id: str
    customer_ref: str
    segment: str
    amount: int
    currency: str
    payment_method: str
    device_type: str
    source: str


@dataclass(frozen=True)
class InterventionSpec:
    """Treatment applied to a simulated attempt. Control is intervention=None."""

    intervention_type: str
    params: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", MappingProxyType(dict(self.params)))


@dataclass(frozen=True)
class SimulatedOutcome:
    """Customer payment outcome. Contains no hidden causal metadata."""

    status: str
    failure_reason: str | None
    completion_seconds: int | None


# ---------------------------------------------------------------------------
# Sealed model version / configuration (private)
# ---------------------------------------------------------------------------

_CAUSAL_MODEL_VERSION = "techbazaar-v1"

_CLAMP_MIN = 0.02
_CLAMP_MAX = 0.98
_FAILED_SHARE_OF_NON_CAPTURED = 0.60

# Hidden scenario magnitudes. Not part of the public API.
_EFFECT_ANDROID_MID_PMC = 0.13
_EFFECT_ANDROID_BUDGET_TIER1 = 0.08
_EFFECT_ANDROID_BUDGET_TIER2 = 0.11
_EFFECT_ANDROID_BUDGET_TIER3 = 0.12
_ANDROID_BUDGET_TIER1_MAX = 0.05
_ANDROID_BUDGET_TIER2_MAX = 0.10
_EFFECT_REPEAT_BUYER_PARTIAL = 0.08
_REPEAT_BUYER_AMOUNT_MIN_PAISE = 500_000
_EFFECT_IOS_PREMIUM_EXPIRY_SHORT = -0.07
_EFFECT_IOS_PREMIUM_EXPIRY_MED = -0.03
_IOS_PREMIUM_EXPIRY_SHORT_HOURS = 2.0
_IOS_PREMIUM_EXPIRY_MED_HOURS = 6.0

_FALLBACK_FAILURE_REASON = "unknown"
_FALLBACK_BASELINE = 0.50


def _sealed_config() -> dict[str, object]:
    """Canonical private representation of the frozen hidden model."""
    return {
        "version": _CAUSAL_MODEL_VERSION,
        "clamp_min": _CLAMP_MIN,
        "clamp_max": _CLAMP_MAX,
        "failed_share_of_non_captured": _FAILED_SHARE_OF_NON_CAPTURED,
        "allowed_intervention_types": sorted(ALLOWED_INTERVENTION_TYPES),
        "allowed_statuses": sorted(ALLOWED_STATUSES),
        "scenarios": {
            "android_mid": {
                "intervention_type": "payment_method_config",
                "requires": {"card": False, "upi": True},
                "effect": _EFFECT_ANDROID_MID_PMC,
            },
            "android_budget": {
                "intervention_type": "offer_discount",
                "param": "discount_pct",
                "tiers": [
                    {"gt": 0.0, "lte": _ANDROID_BUDGET_TIER1_MAX, "effect": _EFFECT_ANDROID_BUDGET_TIER1},
                    {
                        "gt": _ANDROID_BUDGET_TIER1_MAX,
                        "lte": _ANDROID_BUDGET_TIER2_MAX,
                        "effect": _EFFECT_ANDROID_BUDGET_TIER2,
                    },
                    {"gt": _ANDROID_BUDGET_TIER2_MAX, "effect": _EFFECT_ANDROID_BUDGET_TIER3},
                ],
            },
            "web_general": {
                "null_scenario": True,
                "effect": 0.0,
            },
            "repeat_buyer": {
                "intervention_type": "partial_payment",
                "requires": {"accept_partial": True},
                "min_amount_paise": _REPEAT_BUYER_AMOUNT_MIN_PAISE,
                "effect": _EFFECT_REPEAT_BUYER_PARTIAL,
            },
            "ios_premium": {
                "intervention_type": "expiry_config",
                "param": "expiry_hours",
                "tiers": [
                    {"lte": _IOS_PREMIUM_EXPIRY_SHORT_HOURS, "effect": _EFFECT_IOS_PREMIUM_EXPIRY_SHORT},
                    {
                        "gt": _IOS_PREMIUM_EXPIRY_SHORT_HOURS,
                        "lte": _IOS_PREMIUM_EXPIRY_MED_HOURS,
                        "effect": _EFFECT_IOS_PREMIUM_EXPIRY_MED,
                    },
                    {"gt": _IOS_PREMIUM_EXPIRY_MED_HOURS, "effect": 0.0},
                ],
            },
        },
    }


def causal_model_fingerprint() -> str:
    """SHA-256 of the sealed model version/config. Does not reveal the config."""
    canonical = json.dumps(_sealed_config(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _jsonable(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _canonicalize_intervention(intervention: InterventionSpec | None) -> str:
    if intervention is None:
        return "null"
    payload = {
        "intervention_type": intervention.intervention_type,
        "params": _jsonable(dict(intervention.params)),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _local_rng(
    seed: int,
    event_ref: str,
    intervention: InterventionSpec | None,
) -> random.Random:
    material = f"{seed}|{event_ref}|{_canonicalize_intervention(intervention)}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return random.Random(int(digest, 16))


def _segment_profile(segment: str) -> SegmentProfile | None:
    for seg in TECHBAZAAR_PROFILE.segments:
        if seg.name == segment:
            return seg
    return None


def _baseline_capture_rate(segment: str) -> float:
    profile = _segment_profile(segment)
    if profile is None:
        return _FALLBACK_BASELINE
    return profile.target_conversion_rate


def _clamp_probability(value: float) -> float:
    if value < _CLAMP_MIN:
        return _CLAMP_MIN
    if value > _CLAMP_MAX:
        return _CLAMP_MAX
    return value


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _hidden_capture_effect(
    context: PaymentContext,
    intervention: InterventionSpec | None,
) -> float:
    """Return the sealed absolute capture-probability delta. Never exposed."""
    if intervention is None:
        return 0.0

    segment = context.segment
    itype = intervention.intervention_type
    params = intervention.params

    # Scenario 3 - web_general is a null / false-correlation world.
    if segment == "web_general":
        return 0.0

    # Scenario 1 - android_mid: disable card, keep UPI.
    if segment == "android_mid":
        if itype == "payment_method_config":
            if params.get("card") is False and params.get("upi") is True:
                return _EFFECT_ANDROID_MID_PMC
        return 0.0

    # Scenario 2 - android_budget: modest offer discount.
    if segment == "android_budget":
        if itype == "offer_discount" and "discount_pct" in params:
            pct = _as_float(params.get("discount_pct"))
            if pct is None:
                return 0.0
            if 0.0 < pct <= _ANDROID_BUDGET_TIER1_MAX:
                return _EFFECT_ANDROID_BUDGET_TIER1
            if _ANDROID_BUDGET_TIER1_MAX < pct <= _ANDROID_BUDGET_TIER2_MAX:
                return _EFFECT_ANDROID_BUDGET_TIER2
            if pct > _ANDROID_BUDGET_TIER2_MAX:
                return _EFFECT_ANDROID_BUDGET_TIER3
        return 0.0

    # Scenario 4 - repeat_buyer: partial payment on larger tickets.
    if segment == "repeat_buyer":
        if itype == "partial_payment" and params.get("accept_partial") is True:
            if context.amount >= _REPEAT_BUYER_AMOUNT_MIN_PAISE:
                return _EFFECT_REPEAT_BUYER_PARTIAL
            return 0.0
        return 0.0

    # Scenario 5 - ios_premium: very short expiry harms conversion.
    if segment == "ios_premium":
        if itype == "expiry_config" and "expiry_hours" in params:
            hours = _as_float(params.get("expiry_hours"))
            if hours is None:
                return 0.0
            if hours <= _IOS_PREMIUM_EXPIRY_SHORT_HOURS:
                return _EFFECT_IOS_PREMIUM_EXPIRY_SHORT
            if hours <= _IOS_PREMIUM_EXPIRY_MED_HOURS:
                return _EFFECT_IOS_PREMIUM_EXPIRY_MED
            return 0.0
        return 0.0

    return 0.0


def _sample_failure_reason(segment: str, rng: random.Random) -> str:
    profile = _segment_profile(segment)
    if profile is None or not profile.failure_reason_weights:
        return _FALLBACK_FAILURE_REASON
    names = list(profile.failure_reason_weights.keys())
    weights = list(profile.failure_reason_weights.values())
    return rng.choices(names, weights=weights, k=1)[0]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def simulate_outcome(
    *,
    context: PaymentContext,
    intervention: InterventionSpec | None,
    seed: int = 20260827,
) -> SimulatedOutcome:
    """Simulate a customer payment outcome for control or a treatment.

    Deterministic for a given (context, intervention, seed) triple.
    Does not mutate context, write to a database, or call any network API.
    """
    rng = _local_rng(seed, context.event_ref, intervention)
    p_capture = _clamp_probability(
        _baseline_capture_rate(context.segment) + _hidden_capture_effect(context, intervention)
    )

    if rng.random() < p_capture:
        return SimulatedOutcome(
            status="captured",
            failure_reason=None,
            completion_seconds=rng.randint(5, 120),
        )

    if rng.random() < _FAILED_SHARE_OF_NON_CAPTURED:
        return SimulatedOutcome(
            status="failed",
            failure_reason=_sample_failure_reason(context.segment, rng),
            completion_seconds=rng.randint(2, 45),
        )

    return SimulatedOutcome(
        status="abandoned",
        failure_reason=None,
        completion_seconds=None,
    )
