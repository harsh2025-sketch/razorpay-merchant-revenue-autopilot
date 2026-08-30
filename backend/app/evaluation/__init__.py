"""Offline deterministic evaluation harness for the Merchant Revenue Autopilot.

The package initializer keeps baseline imports causal-model free.  The sealed
simulation is loaded only when a caller explicitly requests harness symbols;
inside the evaluation package, only ``harness.py`` imports it.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from app.evaluation.baselines import (
    ALLOWED_INTERVENTION_TYPES,
    AUTOPILOT,
    NO_OPTIMIZATION,
    RANDOM_INTERVENTION,
    RULE_BASED,
    SAFE_INTERVENTION_PARAMS,
    STRATEGIES,
    choose_random_intervention,
    choose_rule_based_intervention,
    random_intervention,
    rule_based_intervention,
)

_HARNESS_EXPORTS = {
    "CANONICAL_SEGMENTS",
    "DEFAULT_SEEDS",
    "EVALUATION_CUSTOMERS_PER_SEGMENT",
    "BenchmarkConfig",
    "build_paired_contexts",
    "generate_evaluation_cohort",
    "generate_paired_contexts",
    "evaluation_proposal_from_evidence",
    "run_benchmark",
    "run_evaluation",
    "BenchmarkReport",
    "EvaluationAggregate",
    "EvaluationRunResult",
}


def __getattr__(name: str) -> Any:
    """Lazily expose harness/report APIs without widening the baseline boundary."""
    if name in _HARNESS_EXPORTS:
        module_name = "app.evaluation.report" if name in {
            "BenchmarkReport",
            "EvaluationAggregate",
            "EvaluationRunResult",
        } else "app.evaluation.harness"
        value = getattr(import_module(module_name), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "NO_OPTIMIZATION",
    "RANDOM_INTERVENTION",
    "RULE_BASED",
    "AUTOPILOT",
    "STRATEGIES",
    "ALLOWED_INTERVENTION_TYPES",
    "SAFE_INTERVENTION_PARAMS",
    "random_intervention",
    "choose_random_intervention",
    "rule_based_intervention",
    "choose_rule_based_intervention",
    "CANONICAL_SEGMENTS",
    "DEFAULT_SEEDS",
    "EVALUATION_CUSTOMERS_PER_SEGMENT",
    "BenchmarkConfig",
    "build_paired_contexts",
    "generate_paired_contexts",
    "generate_evaluation_cohort",
    "evaluation_proposal_from_evidence",
    "run_benchmark",
    "run_evaluation",
    "BenchmarkReport",
    "EvaluationAggregate",
    "EvaluationRunResult",
]
