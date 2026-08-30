"""Observable, deterministic baseline strategies for Task 16.

This module deliberately contains no simulator imports.  A strategy returns a
small semantic intervention payload, or ``None`` for the control.  The harness
is responsible for policy evaluation and for turning that payload into the
sealed simulator's evaluation type.

The rule baseline is intentionally modest and is documented by the thresholds
below.  It is allowed to inspect only baseline PaymentAttempt metrics supplied
by its caller; it never receives treatment outcomes or hidden scenario data.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import math
import random
from typing import Any


NO_OPTIMIZATION = "NO_OPTIMIZATION"
RANDOM_INTERVENTION = "RANDOM_INTERVENTION"
RULE_BASED = "RULE_BASED"
AUTOPILOT = "AUTOPILOT"

STRATEGIES: tuple[str, ...] = (
    NO_OPTIMIZATION,
    RANDOM_INTERVENTION,
    RULE_BASED,
    AUTOPILOT,
)

ALLOWED_INTERVENTION_TYPES: tuple[str, ...] = (
    "payment_method_config",
    "offer_discount",
    "partial_payment",
    "expiry_config",
)

# These are the only treatment parameters used by the random baseline.  They
# are intentionally conservative and are shared with the frozen benchmark
# specification.  Values are plain JSON-compatible data, not simulator types.
SAFE_INTERVENTION_PARAMS: dict[str, dict[str, object]] = {
    "payment_method_config": {"card": False, "upi": True},
    "offer_discount": {"discount_pct": 0.05},
    "partial_payment": {
        "accept_partial": True,
        "first_min_partial_amount_pct": 0.25,
    },
    "expiry_config": {"expiry_hours": 4},
}

# Observable-only rule thresholds.  A difference must clear the threshold,
# rather than merely be positive, so small sampling noise is ignored.
PAYMENT_METHOD_GAP_THRESHOLD = 0.05
HIGH_VALUE_ABANDONMENT_GAP_THRESHOLD = 0.08
HIGH_VALUE_CONVERSION_GAP_THRESHOLD = 0.08
POOR_CONVERSION_THRESHOLD = 0.50


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value_f = float(value)
    return value_f if math.isfinite(value_f) else None


def _metric(evidence: Mapping[str, Any], *keys: str) -> float | None:
    """Read a scalar from either a nested metric map or flattened catalog.

    Supporting both shapes makes this baseline useful with the raw observable
    metrics returned by Task 07 and with Task 08's flattened evidence catalog.
    """
    for key in keys:
        if key in evidence:
            value = _finite_float(evidence[key])
            if value is not None:
                return value
    return None


def _payment_method_rate(evidence: Mapping[str, Any], method: str) -> float | None:
    nested = evidence.get("payment_method_metrics")
    if isinstance(nested, Mapping):
        method_stats = nested.get(method)
        if isinstance(method_stats, Mapping):
            value = _finite_float(method_stats.get("success_rate"))
            if value is not None:
                return value
    return _metric(evidence, f"payment_method.{method}.success_rate")


def _choice(intervention_type: str) -> dict[str, object]:
    return {
        "intervention_type": intervention_type,
        "params": deepcopy(SAFE_INTERVENTION_PARAMS[intervention_type]),
    }


def stable_segment_derivation(segment: str) -> int:
    """Return a process-independent integer derived from a segment label."""
    digest = hashlib.sha256(segment.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def random_intervention(seed: int, segment: str) -> dict[str, object]:
    """Choose one safe intervention using an isolated, stable RNG.

    The global random generator is never touched.  Python's process-randomized
    ``hash`` is not used, so the same ``seed`` and ``segment`` always produce
    the same choice across processes and machines.
    """
    rng = random.Random(seed + stable_segment_derivation(segment))
    intervention_type = rng.choice(ALLOWED_INTERVENTION_TYPES)
    return _choice(intervention_type)


# Alias with a name that reads naturally at call sites and in reports.
choose_random_intervention = random_intervention


def rule_based_intervention(evidence: Mapping[str, Any]) -> dict[str, object]:
    """Choose an intervention from observable baseline metrics only.

    Exact rules, evaluated in order:

    1. If card success is at least 5 percentage points below UPI success,
       choose ``payment_method_config``.
    2. If high-value abandonment is at least 8 points above low-value
       abandonment, or high-value conversion is at least 8 points below
       low-value conversion, choose ``partial_payment``.  These optional
       bucket metrics are still ordinary baseline PaymentAttempt metrics.
    3. If segment conversion is below 50%, choose ``offer_discount``.
    4. Otherwise choose ``expiry_config``.

    Missing optional bucket metrics do not cause an inference: the rule simply
    proceeds to the next observable rule.  No segment identity is dispatched
    on in this function.
    """
    card_rate = _payment_method_rate(evidence, "card")
    upi_rate = _payment_method_rate(evidence, "upi")
    if (
        card_rate is not None
        and upi_rate is not None
        and card_rate + PAYMENT_METHOD_GAP_THRESHOLD < upi_rate
    ):
        return _choice("payment_method_config")

    high_abandonment = _metric(
        evidence,
        "high_value_abandonment_rate",
        "amount_bucket.high_value.abandonment_rate",
    )
    low_abandonment = _metric(
        evidence,
        "low_value_abandonment_rate",
        "amount_bucket.low_value.abandonment_rate",
    )
    if (
        high_abandonment is not None
        and low_abandonment is not None
        and high_abandonment - low_abandonment >= HIGH_VALUE_ABANDONMENT_GAP_THRESHOLD
    ):
        return _choice("partial_payment")

    high_conversion = _metric(
        evidence,
        "high_value_conversion_rate",
        "amount_bucket.high_value.conversion_rate",
    )
    low_conversion = _metric(
        evidence,
        "low_value_conversion_rate",
        "amount_bucket.low_value.conversion_rate",
    )
    if (
        high_conversion is not None
        and low_conversion is not None
        and low_conversion - high_conversion >= HIGH_VALUE_CONVERSION_GAP_THRESHOLD
    ):
        return _choice("partial_payment")

    overall_rate = _metric(
        evidence,
        "segment_conversion_rate",
        "overall_segment_conversion_rate",
        "conversion_rate",
    )
    if overall_rate is not None and overall_rate < POOR_CONVERSION_THRESHOLD:
        return _choice("offer_discount")

    return _choice("expiry_config")


choose_rule_based_intervention = rule_based_intervention


def intervention_is_safe_payload(value: object) -> bool:
    """Return whether a value belongs to the baseline intervention vocabulary."""
    if not isinstance(value, Mapping):
        return False
    intervention_type = value.get("intervention_type")
    params = value.get("params")
    return (
        intervention_type in SAFE_INTERVENTION_PARAMS
        and isinstance(params, Mapping)
        and dict(params) == SAFE_INTERVENTION_PARAMS[intervention_type]
    )


__all__ = [
    "NO_OPTIMIZATION",
    "RANDOM_INTERVENTION",
    "RULE_BASED",
    "AUTOPILOT",
    "STRATEGIES",
    "ALLOWED_INTERVENTION_TYPES",
    "SAFE_INTERVENTION_PARAMS",
    "PAYMENT_METHOD_GAP_THRESHOLD",
    "HIGH_VALUE_ABANDONMENT_GAP_THRESHOLD",
    "HIGH_VALUE_CONVERSION_GAP_THRESHOLD",
    "POOR_CONVERSION_THRESHOLD",
    "stable_segment_derivation",
    "random_intervention",
    "choose_random_intervention",
    "rule_based_intervention",
    "choose_rule_based_intervention",
    "intervention_is_safe_payload",
]
