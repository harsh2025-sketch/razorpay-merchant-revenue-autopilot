#!/usr/bin/env python3
"""Task 20: controlled production verification for the adaptive Autopilot layer.

This script intentionally MUTATES the canonical hosted demo by starting exactly
one new optimization cycle. It verifies Tasks 19A-19E against the deployed API,
then advances the new cycle through the existing one-step orchestrator.

It does not create a fourth cycle merely to prove learning persistence. The
post-cycle Intelligence read model must already contain the new terminal trial,
which proves that the next cycle would receive the updated memory/champion
state without adding unnecessary production mutation.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from typing import Any

from verify_production_journey import (
    MAX_STEPS,
    MERCHANT_ID,
    TERMINAL_STATES,
    VerificationFailure,
    _as_dict,
    _as_list,
    _assert_health,
    _assert_rollover_blocked,
    _cycle,
    _opportunities,
    _overview,
    request_json,
)

MATERIAL_RATE_DELTA = 0.02
MATERIAL_ATTEMPT_MIN_DELTA = 100
MATERIAL_ATTEMPT_RELATIVE_DELTA = 0.20
RATE_KEYS = (
    "absolute_gap",
    "segment_conversion_rate",
    "comparison_conversion_rate",
)
BLOCKING_OUTCOMES = {"ROLLBACK", "INCONCLUSIVE", "POLICY_REJECTED"}


def _intelligence(base_url: str) -> dict[str, Any]:
    _, payload = request_json(
        base_url,
        "GET",
        f"/api/v1/merchants/{MERCHANT_ID}/intelligence",
    )
    return _as_dict(payload, "intelligence")


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _material_evidence_changed(current: dict[str, Any], prior: dict[str, Any]) -> bool:
    current_evidence = _as_dict(current.get("evidence") or {}, "current opportunity evidence")
    prior_evidence = _as_dict(prior.get("evidence") or {}, "prior opportunity evidence")

    for key in RATE_KEYS:
        current_value = _finite_number(current_evidence.get(key))
        prior_value = _finite_number(prior_evidence.get(key))
        if current_value is None or prior_value is None:
            continue
        delta = abs(current_value - prior_value)
        if delta > MATERIAL_RATE_DELTA or math.isclose(
            delta, MATERIAL_RATE_DELTA, rel_tol=0.0, abs_tol=1e-12
        ):
            return True

    current_attempts = _finite_number(current_evidence.get("segment_attempts"))
    prior_attempts = _finite_number(prior_evidence.get("segment_attempts"))
    if current_attempts is not None and prior_attempts is not None:
        absolute_growth = current_attempts - prior_attempts
        if prior_attempts > 0:
            relative_growth = absolute_growth / prior_attempts
            if (
                absolute_growth >= MATERIAL_ATTEMPT_MIN_DELTA
                and relative_growth >= MATERIAL_ATTEMPT_RELATIVE_DELTA
            ):
                return True
        elif absolute_growth >= MATERIAL_ATTEMPT_MIN_DELTA:
            return True

    return False


def _cycle_outcome(cycle: dict[str, Any]) -> str | None:
    result = cycle.get("result")
    if isinstance(result, dict) and isinstance(result.get("decision"), str):
        return result["decision"]
    policy = cycle.get("policy_decision")
    if isinstance(policy, dict) and policy.get("decision") == "REJECT":
        return "POLICY_REJECTED"
    return None


def _assert_memory_shape(intelligence: dict[str, Any], *, min_trials: int) -> dict[str, Any]:
    memory = _as_dict(intelligence.get("memory"), "intelligence.memory")
    records = _as_list(memory.get("records"), "intelligence.memory.records")
    knowledge = _as_list(memory.get("knowledge"), "intelligence.memory.knowledge")
    trial_count = memory.get("trial_count")
    if not isinstance(trial_count, int) or trial_count != len(records):
        raise VerificationFailure("memory trial_count does not match persisted record count")
    if trial_count < min_trials:
        raise VerificationFailure(
            f"expected at least {min_trials} learned trials before Task 20, got {trial_count}"
        )

    experiment_ids = [row.get("experiment_id") for row in records if isinstance(row, dict)]
    if len(experiment_ids) != len(set(experiment_ids)):
        raise VerificationFailure("memory contains duplicate experiment records")

    completed = memory.get("completed_result_count")
    keep = memory.get("keep_count")
    rollback = memory.get("rollback_count")
    inconclusive = memory.get("inconclusive_count")
    counters = (completed, keep, rollback, inconclusive)
    if not all(isinstance(value, int) and value >= 0 for value in counters):
        raise VerificationFailure("memory result counters are malformed")
    if completed != keep + rollback + inconclusive:
        raise VerificationFailure("memory statistical counters are internally inconsistent")

    if not all(isinstance(row, dict) for row in knowledge):
        raise VerificationFailure("memory knowledge contains a non-object row")
    return memory


def _assert_champion_shape(
    intelligence: dict[str, Any], memory: dict[str, Any]
) -> dict[str, Any]:
    champion = _as_dict(intelligence.get("champion"), "intelligence.champion")
    version = champion.get("version")
    promotion_count = champion.get("promotion_count")
    configs = _as_list(champion.get("configs"), "intelligence.champion.configs")
    if not isinstance(promotion_count, int) or promotion_count < 0:
        raise VerificationFailure("champion promotion_count is malformed")
    if version != promotion_count + 1:
        raise VerificationFailure("champion version is not baseline v1 plus KEEP promotions")

    memory_by_experiment = {
        row.get("experiment_id"): row
        for row in _as_list(memory.get("records"), "memory.records")
        if isinstance(row, dict)
    }
    for config in configs:
        row = _as_dict(config, "champion config")
        source_id = row.get("source_experiment_id")
        memory_row = memory_by_experiment.get(source_id)
        if not isinstance(memory_row, dict):
            raise VerificationFailure("champion source experiment is missing from memory")
        if memory_row.get("statistical_decision") != "KEEP":
            raise VerificationFailure("champion was sourced from a non-KEEP experiment")
        if memory_row.get("treatment_config") != row.get("config"):
            raise VerificationFailure("champion config differs from its promoted treatment")
    return champion


def _assert_portfolio_shape(intelligence: dict[str, Any]) -> dict[str, Any]:
    portfolio = _as_dict(intelligence.get("portfolio"), "intelligence.portfolio")
    rows = _as_list(portfolio.get("opportunities"), "intelligence.portfolio.opportunities")
    for expected_rank, item in enumerate(rows, start=1):
        row = _as_dict(item, f"portfolio[{expected_rank}]")
        if row.get("rank") != expected_rank:
            raise VerificationFailure("portfolio ranks are not deterministic/sequential")
        priority = row.get("priority_index")
        if not isinstance(priority, (int, float)) or isinstance(priority, bool):
            raise VerificationFailure("portfolio priority_index is malformed")
        if not 0.0 <= float(priority) <= 1.0:
            raise VerificationFailure("portfolio priority_index escaped [0, 1]")
        if row.get("estimated_recoverable_gmv_paise") is not None and row.get(
            "history_adjusted_gmv_proxy_paise"
        ) is None:
            raise VerificationFailure("portfolio GMV proxy lost its history-adjusted projection")

    next_best = portfolio.get("next_best_opportunity_id")
    if next_best is not None:
        feasible = [
            row
            for row in rows
            if isinstance(row, dict) and row.get("policy_feasible") is True
        ]
        if not feasible or feasible[0].get("opportunity_id") != next_best:
            raise VerificationFailure("portfolio next_best does not match the first feasible rank")
    return portfolio


def _assert_no_blocked_stale_repeat(
    base_url: str,
    *,
    current_cycle: dict[str, Any],
    prior_memory_records: list[Any],
) -> None:
    current_hypothesis = _as_dict(current_cycle.get("hypothesis"), "current hypothesis")
    current_opportunity = _as_dict(current_cycle.get("opportunity"), "current opportunity")
    current_type = current_hypothesis.get("intervention_type")
    current_params = current_hypothesis.get("intervention_params")
    if not isinstance(current_type, str) or not isinstance(current_params, dict):
        raise VerificationFailure("current hypothesis semantic signature is malformed")

    for raw_record in prior_memory_records:
        record = _as_dict(raw_record, "prior memory record")
        if record.get("segment") != current_opportunity.get("segment"):
            continue
        outcome = record.get("statistical_decision")
        if outcome is None and record.get("policy_decision") == "REJECT":
            outcome = "POLICY_REJECTED"
        if outcome not in BLOCKING_OUTCOMES:
            continue

        prior_cycle = _cycle(base_url, str(record.get("opportunity_id")))
        prior_hypothesis = prior_cycle.get("hypothesis")
        if not isinstance(prior_hypothesis, dict):
            continue
        if prior_hypothesis.get("intervention_type") != current_type:
            continue
        if prior_hypothesis.get("intervention_params") != current_params:
            continue

        if outcome == "POLICY_REJECTED":
            raise VerificationFailure(
                "memory-aware diagnosis repeated an exact policy-rejected proposal"
            )
        prior_opportunity = _as_dict(prior_cycle.get("opportunity"), "prior opportunity")
        if not _material_evidence_changed(current_opportunity, prior_opportunity):
            raise VerificationFailure(
                f"memory-aware diagnosis repeated an unchanged exact {outcome} proposal"
            )


def _champion_config_for(champion: dict[str, Any], intervention_type: str) -> dict[str, Any] | None:
    for raw in _as_list(champion.get("configs"), "champion.configs"):
        row = _as_dict(raw, "champion config")
        if row.get("intervention_type") == intervention_type:
            config = row.get("config")
            return dict(config) if isinstance(config, dict) else None
    return None


def run_verification(base_url: str, *, min_existing_trials: int = 2) -> dict[str, Any]:
    _assert_health(base_url)
    before_overview = _overview(base_url)
    before_status = _as_dict(
        before_overview.get("autopilot_status"), "before.autopilot_status"
    )
    if before_status.get("state") not in TERMINAL_STATES:
        raise VerificationFailure(
            "existing demo cycle is not terminal; refusing adaptive production mutation"
        )
    if before_overview.get("audit_chain_valid") is not True:
        raise VerificationFailure("audit chain is invalid before Task 20")

    before_intelligence = _intelligence(base_url)
    before_memory = _assert_memory_shape(
        before_intelligence, min_trials=min_existing_trials
    )
    before_champion = _assert_champion_shape(before_intelligence, before_memory)
    before_portfolio = _assert_portfolio_shape(before_intelligence)
    prior_memory_records = list(_as_list(before_memory.get("records"), "before memory records"))
    prior_experiment_ids = {
        row.get("experiment_id") for row in prior_memory_records if isinstance(row, dict)
    }
    prior_opportunity_ids = {
        row.get("opportunity_id") for row in prior_memory_records if isinstance(row, dict)
    }

    previous_opportunity_id = before_status.get("latest_opportunity_id")
    if not isinstance(previous_opportunity_id, str) or not previous_opportunity_id:
        raise VerificationFailure("terminal state has no latest opportunity id")
    previous_cycle = _cycle(base_url, previous_opportunity_id)
    previous_result = previous_cycle.get("result")
    previous_policy = previous_cycle.get("policy_decision")

    pre_ranked_id = before_portfolio.get("next_best_opportunity_id")
    print(
        "Task 20 preflight: "
        f"trials={before_memory.get('trial_count')} "
        f"champion=v{before_champion.get('version')} "
        f"next={str(pre_ranked_id)[:8] if pre_ranked_id else 'detect'}"
    )

    _, payload = request_json(
        base_url,
        "POST",
        f"/api/v1/merchants/{MERCHANT_ID}/autopilot/new-cycle",
    )
    new_opportunity = _as_dict(payload, "new-cycle opportunity")
    new_opportunity_id = new_opportunity.get("id")
    if not isinstance(new_opportunity_id, str) or not new_opportunity_id:
        raise VerificationFailure("new-cycle did not return a valid opportunity id")
    if new_opportunity_id == previous_opportunity_id:
        raise VerificationFailure("Task 20 rollover reused the prior terminal opportunity")
    if pre_ranked_id is not None and new_opportunity_id != pre_ranked_id:
        raise VerificationFailure(
            "Autopilot ignored the portfolio's pre-existing next-best opportunity"
        )

    after_rollover_intelligence = _intelligence(base_url)
    rollover_memory = _assert_memory_shape(
        after_rollover_intelligence, min_trials=min_existing_trials
    )
    if rollover_memory.get("trial_count") != before_memory.get("trial_count"):
        raise VerificationFailure("starting a new cycle incorrectly changed learned memory")
    rollover_portfolio = _assert_portfolio_shape(after_rollover_intelligence)
    if rollover_portfolio.get("next_best_opportunity_id") != new_opportunity_id:
        raise VerificationFailure("new untouched opportunity is not the portfolio next-best")

    rows = _opportunities(base_url)
    by_id = {row.get("id"): row for row in rows}
    old_row = by_id.get(previous_opportunity_id)
    if not isinstance(old_row, dict) or old_row.get("status") != "resolved":
        raise VerificationFailure("previous opportunity was not preserved as resolved")

    step_history: list[str] = []
    hypothesis_verified = False
    planner_verified = False
    skip_guard_verified = False
    new_intervention_type: str | None = None

    for index in range(1, MAX_STEPS + 1):
        _, step_payload = request_json(
            base_url,
            "POST",
            f"/api/v1/merchants/{MERCHANT_ID}/autopilot/step",
        )
        step = _as_dict(step_payload, f"step {index}")
        step_name = step.get("step")
        if isinstance(step_name, str):
            step_history.append(step_name)
        print(
            f"  step {index:02d}: {step_name} -> "
            f"state={step.get('status')} next={step.get('next_action')}"
        )

        if step_name == "HYPOTHESIS_PROPOSED":
            current = _cycle(base_url, new_opportunity_id)
            _assert_no_blocked_stale_repeat(
                base_url,
                current_cycle=current,
                prior_memory_records=prior_memory_records,
            )
            hypothesis = _as_dict(current.get("hypothesis"), "new hypothesis")
            new_intervention_type = str(hypothesis.get("intervention_type"))
            hypothesis_verified = True

        if step_name == "EXPERIMENT_PLANNED":
            current = _cycle(base_url, new_opportunity_id)
            experiment = _as_dict(current.get("experiment"), "new experiment")
            intervention_type = experiment.get("intervention_type")
            control = _as_dict(experiment.get("control_config"), "experiment control")
            treatment = _as_dict(experiment.get("treatment_config"), "experiment treatment")
            if control == treatment:
                raise VerificationFailure("planner produced a challenger identical to its control")
            if isinstance(intervention_type, str):
                promoted = _champion_config_for(before_champion, intervention_type)
                if promoted is not None and control != promoted:
                    raise VerificationFailure(
                        "planner did not inherit the current promoted champion as control"
                    )
            planner_verified = True

        if step_name == "RESOURCE_DEPLOYED":
            current = _cycle(base_url, new_opportunity_id)
            resource = _as_dict(current.get("razorpay_resource"), "deployed resource")
            external_id = resource.get("razorpay_id")
            if not isinstance(external_id, str) or not external_id.startswith("demo_"):
                raise VerificationFailure("hosted Task 20 run created a non-demo resource")
            _assert_rollover_blocked(base_url)
            skip_guard_verified = True

        state = step.get("status")
        next_action = step.get("next_action")
        if state in {"POLICY_REJECTED", "DEPLOYMENT_BLOCKED"}:
            break
        if state == "COMPLETED" and next_action in {"DONE", None}:
            break
        time.sleep(0.5)
    else:
        raise VerificationFailure(f"Task 20 did not reach terminal state in {MAX_STEPS} steps")

    if not hypothesis_verified:
        raise VerificationFailure("Task 20 never verified a memory-aware hypothesis")
    if not planner_verified:
        raise VerificationFailure("Task 20 never verified champion/challenger planning")

    after_overview = _overview(base_url)
    after_status = _as_dict(
        after_overview.get("autopilot_status"), "after.autopilot_status"
    )
    if after_status.get("state") not in TERMINAL_STATES:
        raise VerificationFailure("Task 20 final state is not terminal")
    if after_overview.get("audit_chain_valid") is not True or after_status.get(
        "audit_chain_valid"
    ) is not True:
        raise VerificationFailure("audit chain is invalid after Task 20")

    new_cycle = _cycle(base_url, new_opportunity_id)
    if new_cycle.get("audit_chain_valid") is not True:
        raise VerificationFailure("new cycle reports invalid audit integrity")

    preserved_cycle = _cycle(base_url, previous_opportunity_id)
    if previous_result is not None and preserved_cycle.get("result") != previous_result:
        raise VerificationFailure("previous statistical result changed during Task 20")
    if previous_policy is not None and preserved_cycle.get("policy_decision") != previous_policy:
        raise VerificationFailure("previous policy decision changed during Task 20")

    after_intelligence = _intelligence(base_url)
    after_memory = _assert_memory_shape(
        after_intelligence, min_trials=min_existing_trials
    )
    after_champion = _assert_champion_shape(after_intelligence, after_memory)
    _assert_portfolio_shape(after_intelligence)

    experiment = new_cycle.get("experiment")
    result = new_cycle.get("result")
    policy = new_cycle.get("policy_decision")
    experiment_id = experiment.get("id") if isinstance(experiment, dict) else None

    memory_eligible = False
    if isinstance(experiment, dict):
        status = experiment.get("status")
        memory_eligible = (
            isinstance(result, dict)
            or (isinstance(policy, dict) and policy.get("decision") == "REJECT")
            or status in {"completed", "rolled_back", "cancelled"}
        )

    expected_trials = int(before_memory.get("trial_count")) + (1 if memory_eligible else 0)
    if after_memory.get("trial_count") != expected_trials:
        raise VerificationFailure("post-cycle memory did not learn exactly the terminal new trial")

    after_records = _as_list(after_memory.get("records"), "after memory records")
    after_experiment_ids = {
        row.get("experiment_id") for row in after_records if isinstance(row, dict)
    }
    if not prior_experiment_ids.issubset(after_experiment_ids):
        raise VerificationFailure("historical experiment memory was lost during Task 20")
    after_opportunity_ids = {
        row.get("opportunity_id") for row in after_records if isinstance(row, dict)
    }
    if not prior_opportunity_ids.issubset(after_opportunity_ids):
        raise VerificationFailure("historical opportunity memory was lost during Task 20")
    if memory_eligible and experiment_id not in after_experiment_ids:
        raise VerificationFailure("new terminal experiment did not appear in learned memory")

    statistical_decision = result.get("decision") if isinstance(result, dict) else None
    if statistical_decision == "KEEP":
        if after_champion.get("promotion_count") != before_champion.get("promotion_count") + 1:
            raise VerificationFailure("KEEP did not advance champion promotion count")
        if after_champion.get("version") != before_champion.get("version") + 1:
            raise VerificationFailure("KEEP did not advance champion version")
        if not isinstance(experiment, dict) or not isinstance(new_intervention_type, str):
            raise VerificationFailure("KEEP cannot be reconciled to the new experiment")
        promoted = _champion_config_for(after_champion, new_intervention_type)
        if promoted != experiment.get("treatment_config"):
            raise VerificationFailure("KEEP did not promote the tested treatment config")
        audit_events = _as_list(new_cycle.get("audit_events"), "new cycle audit")
        if not any(
            isinstance(event, dict) and event.get("event_type") == "TREATMENT_PROMOTED"
            for event in audit_events
        ):
            raise VerificationFailure("KEEP is missing TREATMENT_PROMOTED audit evidence")
    else:
        if after_champion != before_champion:
            raise VerificationFailure("non-KEEP outcome changed derived champion state")

    terminal_outcome = (
        statistical_decision
        if isinstance(statistical_decision, str)
        else (
            "POLICY_REJECTED"
            if isinstance(policy, dict) and policy.get("decision") == "REJECT"
            else str(after_status.get("state"))
        )
    )

    return {
        "previous_trial_count": before_memory.get("trial_count"),
        "new_trial_count": after_memory.get("trial_count"),
        "previous_champion_version": before_champion.get("version"),
        "new_champion_version": after_champion.get("version"),
        "portfolio_preselected_opportunity_id": pre_ranked_id,
        "new_opportunity_id": new_opportunity_id,
        "steps": step_history,
        "memory_aware_hypothesis_verified": hypothesis_verified,
        "champion_control_verified": planner_verified,
        "skip_guard_verified": skip_guard_verified,
        "terminal_outcome": terminal_outcome,
        "audit_chain_valid": True,
        "learning_persisted": after_memory.get("trial_count") == expected_trials,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Task 20 adaptive production verification (writes one demo cycle)"
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BASE_URL"),
        help="Backend HTTPS origin, or set BASE_URL",
    )
    parser.add_argument(
        "--confirm-writes",
        action="store_true",
        help="Required acknowledgement that exactly one new demo cycle is started",
    )
    parser.add_argument(
        "--min-existing-trials",
        type=int,
        default=2,
        help="Minimum previously learned terminal trials required before Task 20",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if not args.confirm_writes:
        print(
            "TASK 20: REFUSED - pass --confirm-writes to mutate demo lifecycle state",
            file=sys.stderr,
        )
        return 2
    if not isinstance(args.base_url, str) or not args.base_url.strip():
        print("TASK 20: FAIL - BASE_URL is required", file=sys.stderr)
        return 2
    if args.min_existing_trials < 0:
        print("TASK 20: FAIL - --min-existing-trials must be >= 0", file=sys.stderr)
        return 2

    try:
        summary = run_verification(
            args.base_url.strip(), min_existing_trials=args.min_existing_trials
        )
    except VerificationFailure as exc:
        print(f"TASK 20: FAIL - {exc}", file=sys.stderr)
        return 1

    print("TASK 20: PASS")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
