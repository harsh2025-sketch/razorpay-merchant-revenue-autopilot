"""Offline tests for Task 16's deterministic evaluation harness."""

from __future__ import annotations

import ast
from dataclasses import asdict
import json
from pathlib import Path
import re

import pytest

from app.evaluation.baselines import (
    ALLOWED_INTERVENTION_TYPES,
    SAFE_INTERVENTION_PARAMS,
    STRATEGIES,
    choose_random_intervention,
    choose_rule_based_intervention,
)
from app.evaluation.harness import (
    CANONICAL_SEGMENTS,
    DEFAULT_SEEDS,
    EVALUATION_CUSTOMERS_PER_SEGMENT,
    build_paired_contexts,
    evaluation_proposal_from_evidence,
    run_benchmark,
)
from app.evaluation.report import (
    BenchmarkReport,
    render_markdown_summary,
    report_to_json,
)
from app.simulation.causal_model import InterventionSpec, simulate_outcome


SMALL_SEEDS = (20260827, 20260828)
SMALL_COHORT = 32
ROOT = Path(__file__).resolve().parents[1]
EVALUATION_DIR = ROOT / "app" / "evaluation"


@pytest.fixture(scope="module")
def small_report() -> BenchmarkReport:
    return run_benchmark(
        seeds=SMALL_SEEDS,
        segments=CANONICAL_SEGMENTS,
        customers_per_segment=SMALL_COHORT,
    )


def test_same_benchmark_configuration_reproduces_identical_report():
    first = run_benchmark(
        seeds=(20260827,), segments=("android_budget",), customers_per_segment=12
    )
    second = run_benchmark(
        seeds=(20260827,), segments=("android_budget",), customers_per_segment=12
    )
    assert first == second
    assert report_to_json(first) == report_to_json(second)


def test_changing_seed_set_changes_runs(small_report):
    changed = run_benchmark(
        seeds=(20260831,), segments=("android_budget",), customers_per_segment=SMALL_COHORT
    )
    assert changed.seeds != small_report.seeds
    assert changed.runs != [run for run in small_report.runs if run.segment == "android_budget"]


def test_exactly_four_strategies_and_five_default_segments(small_report):
    assert STRATEGIES == (
        "NO_OPTIMIZATION",
        "RANDOM_INTERVENTION",
        "RULE_BASED",
        "AUTOPILOT",
    )
    assert set(run.strategy for run in small_report.runs) == set(STRATEGIES)
    assert set(run.segment for run in small_report.runs) == set(CANONICAL_SEGMENTS)
    assert len(CANONICAL_SEGMENTS) == 5
    assert DEFAULT_SEEDS == (20260827, 20260828, 20260829, 20260830, 20260831)
    assert EVALUATION_CUSTOMERS_PER_SEGMENT == 5000


def test_same_paired_contexts_are_used_across_strategies(small_report):
    for seed in SMALL_SEEDS:
        for segment in CANONICAL_SEGMENTS:
            rows = [
                row
                for row in small_report.runs
                if row.seed == seed and row.segment == segment
            ]
            assert len(rows) == 4
            assert len({row.paired_context_fingerprint for row in rows}) == 1
            contexts = build_paired_contexts(seed, segment, SMALL_COHORT)
            assert contexts[0].event_ref == f"eval_{seed}_{segment}_0"


def test_control_outcome_reused_and_control_delta_is_zero_for_control(small_report):
    for seed in SMALL_SEEDS:
        for segment in CANONICAL_SEGMENTS:
            rows = [
                row
                for row in small_report.runs
                if row.seed == seed and row.segment == segment
            ]
            control = next(row for row in rows if row.strategy == "NO_OPTIMIZATION")
            assert all(row.control_conversion_rate == control.conversion_rate for row in rows)
            assert all(row.control_captured_gmv_paise == control.captured_gmv_paise for row in rows)
            assert control.proposed_intervention is None
            assert control.deployed_intervention is None
            assert control.policy_decision == "N/A"
            assert control.absolute_conversion_delta_vs_control == 0.0
            assert control.captured_gmv_delta_vs_control_paise == 0


def test_random_selection_is_deterministic_and_in_allowed_vocabulary():
    first = choose_random_intervention(20260827, "android_mid")
    second = choose_random_intervention(20260827, "android_mid")
    assert first == second
    assert first["intervention_type"] in ALLOWED_INTERVENTION_TYPES
    assert first["params"] == SAFE_INTERVENTION_PARAMS[first["intervention_type"]]


def test_rule_baseline_uses_observable_metrics_only():
    evidence = {
        "payment_method_metrics": {
            "card": {"success_rate": 0.30},
            "upi": {"success_rate": 0.42},
        },
        "segment_conversion_rate": 0.70,
    }
    choice = choose_rule_based_intervention(evidence)
    assert choice["intervention_type"] == "payment_method_config"
    assert choice["params"] == SAFE_INTERVENTION_PARAMS["payment_method_config"]

    poor = {"segment_conversion_rate": 0.49}
    assert choose_rule_based_intervention(poor)["intervention_type"] == "offer_discount"


def test_rule_baseline_has_no_segment_dispatch():
    source = (EVALUATION_DIR / "baselines.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.Compare)
        and any(isinstance(op, (ast.Eq, ast.Is)) for op in node.ops)
        and any(isinstance(comparator, ast.Constant) and isinstance(comparator.value, str)
                and comparator.value in CANONICAL_SEGMENTS for comparator in node.comparators)
        for node in ast.walk(tree)
    )


def test_evaluation_adapter_is_evidence_only_and_validated():
    catalog = {
        "segment_conversion_rate": 0.44,
        "comparison_conversion_rate": 0.58,
        "absolute_gap": 0.14,
        "payment_method.card.success_rate": 0.50,
        "payment_method.upi.success_rate": 0.51,
    }
    proposal = evaluation_proposal_from_evidence(catalog, ALLOWED_INTERVENTION_TYPES)
    assert proposal.intervention_type == "offer_discount"
    assert set(proposal.evidence_refs) <= set(catalog)
    assert not hasattr(proposal, "causal_label")


def test_evaluation_files_keep_causal_import_in_harness_only():
    importing_files = []
    for path in EVALUATION_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "causal_model" in node.module:
                importing_files.append(path.name)
            if isinstance(node, ast.Import):
                if any(alias.name.endswith("causal_model") for alias in node.names):
                    importing_files.append(path.name)
    assert importing_files == ["harness.py"]
    assert "causal_model" not in (EVALUATION_DIR / "baselines.py").read_text(encoding="utf-8")


def test_policy_rejection_prevents_deployment_and_proposed_is_recorded(small_report):
    rejected = [row for row in small_report.runs if row.policy_decision == "REJECT"]
    assert rejected
    assert all(row.proposed_intervention is not None for row in rejected)
    assert all(row.deployed_intervention is None for row in rejected)
    assert all(row.absolute_conversion_delta_vs_control == 0.0 for row in rejected)


def test_autopilot_plan_was_policy_evaluated(small_report):
    autopilot = [row for row in small_report.runs if row.strategy == "AUTOPILOT"]
    assert len(autopilot) == len(SMALL_SEEDS) * len(CANONICAL_SEGMENTS)
    assert all(row.policy_decision in {"APPROVE", "REJECT"} for row in autopilot)
    assert all(row.proposed_intervention is not None for row in autopilot)


def test_report_metrics_are_mathematically_correct(small_report):
    for row in small_report.runs:
        assert row.attempts == SMALL_COHORT
        assert 0 <= row.captured <= row.attempts
        assert row.conversion_rate == row.captured / row.attempts
        assert row.control_conversion_rate == row.control_conversion_rate
        if row.deployed_intervention is None:
            assert row.captured == next(
                control.captured
                for control in small_report.runs
                if control.seed == row.seed
                and control.segment == row.segment
                and control.strategy == "NO_OPTIMIZATION"
            )
        else:
            assert row.captured_gmv_paise == sum(
                context.amount
                for context in build_paired_contexts(row.seed, row.segment, SMALL_COHORT)
                if simulate_outcome(
                    context=context,
                    intervention=InterventionSpec(
                        intervention_type=row.deployed_intervention["intervention_type"],
                        params=row.deployed_intervention["params"],
                    ),
                    seed=row.seed,
                ).status
                == "captured"
            )
        assert row.captured_gmv_paise >= 0
        assert row.captured_gmv_delta_vs_control_paise == (
            row.captured_gmv_paise - row.control_captured_gmv_paise
            if row.deployed_intervention is not None
            else 0
        )
        if row.deployed_intervention and row.deployed_intervention["intervention_type"] == "offer_discount":
            assert row.discount_exposure_paise == round(
                row.captured_gmv_paise * row.deployed_intervention["params"]["discount_pct"]
            )
        else:
            assert row.discount_exposure_paise == 0


def test_aggregates_and_positive_negative_counts_are_correct(small_report):
    for aggregate in small_report.aggregates:
        rows = [
            row
            for row in small_report.runs
            if row.strategy == aggregate.strategy and row.segment == aggregate.segment
        ]
        assert aggregate.runs == len(SMALL_SEEDS)
        assert aggregate.mean_conversion_rate == sum(row.conversion_rate for row in rows) / len(rows)
        assert aggregate.mean_absolute_delta == sum(
            row.absolute_conversion_delta_vs_control for row in rows
        ) / len(rows)
        deltas = [row.captured_gmv_delta_vs_control_paise for row in rows]
        conversion_deltas = [
            row.absolute_conversion_delta_vs_control for row in rows
        ]
        assert aggregate.total_gmv_delta_paise == sum(deltas)
        assert aggregate.positive_seed_count == sum(delta > 0 for delta in conversion_deltas)
        assert aggregate.negative_seed_count == sum(delta < 0 for delta in conversion_deltas)


def test_null_and_harmful_world_behaviour_is_not_fabricated():
    null_contexts = build_paired_contexts(20260827, "web_general", 500)
    neutral = InterventionSpec("offer_discount", {"discount_pct": 0.05})
    control = sum(
        simulate_outcome(context=context, intervention=None, seed=20260827).status == "captured"
        for context in null_contexts
    )
    treatment = sum(
        simulate_outcome(context=context, intervention=neutral, seed=20260827).status == "captured"
        for context in null_contexts
    )
    assert abs(treatment / len(null_contexts) - control / len(null_contexts)) < 0.15

    harmful_contexts = build_paired_contexts(20260827, "ios_premium", 5000)
    harmful = InterventionSpec("expiry_config", {"expiry_hours": 4})
    control_rate = sum(
        simulate_outcome(context=context, intervention=None, seed=20260827).status == "captured"
        for context in harmful_contexts
    ) / len(harmful_contexts)
    harmful_rate = sum(
        simulate_outcome(context=context, intervention=harmful, seed=20260827).status == "captured"
        for context in harmful_contexts
    ) / len(harmful_contexts)
    assert harmful_rate < control_rate


def test_no_future_outcomes_are_available_during_selection():
    source = (EVALUATION_DIR / "harness.py").read_text(encoding="utf-8")
    assert source.index("selections = _selection_table(resolved)") < source.index(
        "scored_runs: list[EvaluationRunResult]"
    )
    selection_source = source[source.index("def _selection_table"):source.index("# ---------------------------------------------------------------------------\n# Paired causal scoring")]
    assert "simulate_outcome" not in selection_source


def test_no_network_boundaries_in_evaluation_package():
    source = "\n".join(path.read_text(encoding="utf-8") for path in EVALUATION_DIR.glob("*.py"))
    assert "requests." not in source
    assert "razorpay" not in source.lower()
    # The adapter is local; it is not allowed to instantiate a client.
    assert "OpenAI(" not in source


def test_report_fingerprint_json_and_markdown(small_report, tmp_path):
    assert re.fullmatch(r"[0-9a-f]{64}", small_report.causal_model_fingerprint)
    payload = json.loads(report_to_json(small_report))
    assert payload["causal_model_fingerprint"] == small_report.causal_model_fingerprint
    markdown = render_markdown_summary(small_report)
    assert small_report.causal_model_fingerprint in markdown
    assert "Limitations" in markdown
    assert "synthetic deterministic evaluation" in markdown.lower()


def test_repeat_benchmark_and_report_are_exactly_deterministic():
    first = run_benchmark(
        seeds=(20260827,), segments=("repeat_buyer",), customers_per_segment=16
    )
    second = run_benchmark(
        seeds=(20260827,), segments=("repeat_buyer",), customers_per_segment=16
    )
    assert asdict(first) == asdict(second)
    assert report_to_json(first) == report_to_json(second)
