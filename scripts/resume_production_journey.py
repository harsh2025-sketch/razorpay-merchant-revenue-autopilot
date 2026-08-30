#!/usr/bin/env python3
"""Resume an already-started production verification cycle without rollover.

This companion to ``verify_production_journey.py`` exists for one case only:
a guarded production verification already created a new cycle, then a later
transition failed. It resumes the persisted active cycle in place instead of
creating another opportunity.

It MUTATES demo lifecycle state and therefore requires ``--confirm-writes``.
"""

from __future__ import annotations

import argparse
import json
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
    _assert_health,
    _assert_rollover_blocked,
    _cycle,
    _opportunities,
    _overview,
    request_json,
)


def _latest_resolved_history_row(
    rows: list[dict[str, Any]], active_opportunity_id: str
) -> dict[str, Any] | None:
    resolved = [
        row
        for row in rows
        if row.get("id") != active_opportunity_id and row.get("status") == "resolved"
    ]
    if not resolved:
        return None
    return max(
        resolved,
        key=lambda row: (str(row.get("created_at") or ""), str(row.get("id") or "")),
    )


def run_resume_verification(
    base_url: str,
    *,
    expected_opportunity_id: str | None = None,
) -> dict[str, Any]:
    _assert_health(base_url)
    before = _overview(base_url)
    before_status = _as_dict(before.get("autopilot_status"), "before.autopilot_status")

    state = before_status.get("state")
    if state in TERMINAL_STATES:
        raise VerificationFailure(
            "demo cycle is already terminal; use the normal production verifier to start a fresh cycle"
        )

    active_opportunity_id = before_status.get("latest_opportunity_id")
    if not isinstance(active_opportunity_id, str) or not active_opportunity_id:
        raise VerificationFailure("active release state has no latest_opportunity_id")
    if expected_opportunity_id and active_opportunity_id != expected_opportunity_id:
        raise VerificationFailure(
            "active opportunity does not match the explicitly expected opportunity id"
        )

    before_opportunity_count = before_status.get("opportunity_count")
    before_experiment_count = before_status.get("experiment_count")
    active_cycle_before = _cycle(base_url, active_opportunity_id)
    active_experiment_before = active_cycle_before.get("experiment")

    rows = _opportunities(base_url)
    historical_row = _latest_resolved_history_row(rows, active_opportunity_id)
    historical_id = historical_row.get("id") if historical_row else None
    historical_cycle = (
        _cycle(base_url, historical_id)
        if isinstance(historical_id, str) and historical_id
        else None
    )
    historical_result = historical_cycle.get("result") if historical_cycle else None
    historical_policy = historical_cycle.get("policy_decision") if historical_cycle else None

    print(
        "Resuming production verification from "
        f"state={state} opportunity={active_opportunity_id[:8]}"
    )

    # The defining safety property of resume mode: an active cycle cannot be
    # skipped just because a previous verifier process stopped.
    _assert_rollover_blocked(base_url)
    skip_guard_verified = True

    step_history: list[dict[str, Any]] = []
    for index in range(1, MAX_STEPS + 1):
        _, payload = request_json(
            base_url,
            "POST",
            f"/api/v1/merchants/{MERCHANT_ID}/autopilot/step",
        )
        step = _as_dict(payload, f"step {index}")
        step_history.append(step)
        print(
            f"  step {index:02d}: {step.get('step')} -> "
            f"state={step.get('status')} next={step.get('next_action')}"
        )

        if step.get("step") == "RESOURCE_DEPLOYED":
            _assert_rollover_blocked(base_url)

        next_state = step.get("status")
        next_action = step.get("next_action")
        if next_state in {"POLICY_REJECTED", "DEPLOYMENT_BLOCKED"}:
            break
        if next_state == "COMPLETED" and next_action in {"DONE", None}:
            break

        time.sleep(0.5)
    else:
        raise VerificationFailure(
            f"Autopilot did not reach a terminal state in {MAX_STEPS} resume steps"
        )

    after = _overview(base_url)
    after_status = _as_dict(after.get("autopilot_status"), "after.autopilot_status")

    if after.get("audit_chain_valid") is not True or after_status.get("audit_chain_valid") is not True:
        raise VerificationFailure("audit chain is not valid after resumed production cycle")

    after_opportunity_id = after_status.get("latest_opportunity_id")
    if after_opportunity_id != active_opportunity_id:
        raise VerificationFailure("resume unexpectedly changed the focused opportunity")

    if isinstance(before_opportunity_count, int):
        if after_status.get("opportunity_count") != before_opportunity_count:
            raise VerificationFailure("resume created or removed an opportunity instead of preserving history")

    if historical_cycle is not None and isinstance(historical_id, str):
        preserved = _cycle(base_url, historical_id)
        if historical_result is not None and preserved.get("result") != historical_result:
            raise VerificationFailure("historical statistical result changed during resume")
        if historical_policy is not None and preserved.get("policy_decision") != historical_policy:
            raise VerificationFailure("historical policy decision changed during resume")

    active_cycle_after = _cycle(base_url, active_opportunity_id)
    if active_cycle_after.get("audit_chain_valid") is not True:
        raise VerificationFailure("resumed cycle read model reports an invalid audit chain")

    final_state = after_status.get("state")
    if final_state not in TERMINAL_STATES:
        raise VerificationFailure(f"final resumed state is not terminal: {final_state!r}")

    experiment = active_cycle_after.get("experiment")
    result = active_cycle_after.get("result")
    policy = active_cycle_after.get("policy_decision")
    resource = active_cycle_after.get("razorpay_resource")

    if isinstance(resource, dict):
        external_id = resource.get("razorpay_id")
        if not isinstance(external_id, str) or not external_id.startswith("demo_"):
            raise VerificationFailure(
                "hosted release unexpectedly created a non-demo Razorpay resource"
            )

    if (
        active_experiment_before is None
        and experiment is not None
        and isinstance(before_experiment_count, int)
    ):
        after_experiment_count = after_status.get("experiment_count")
        if not isinstance(after_experiment_count, int) or after_experiment_count < before_experiment_count + 1:
            raise VerificationFailure("experiment history did not grow for the resumed cycle")

    if isinstance(result, dict) and isinstance(result.get("decision"), str):
        terminal_outcome = result["decision"]
    elif isinstance(policy, dict) and policy.get("decision") == "REJECT":
        terminal_outcome = "POLICY_REJECTED"
    elif final_state == "DEPLOYMENT_BLOCKED":
        terminal_outcome = "DEPLOYMENT_BLOCKED"
    else:
        terminal_outcome = str(final_state)

    return {
        "resumed_opportunity_id": active_opportunity_id,
        "preserved_historical_opportunity_id": historical_id,
        "steps": [step.get("step") for step in step_history],
        "skip_guard_verified": skip_guard_verified,
        "final_state": final_state,
        "terminal_outcome": terminal_outcome,
        "audit_chain_valid": True,
        "simulated_resource": isinstance(resource, dict),
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resume controlled production lifecycle verification (writes demo state)"
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BASE_URL"),
        help="Backend HTTPS origin, or set BASE_URL",
    )
    parser.add_argument(
        "--expected-opportunity-id",
        default=os.environ.get("EXPECTED_OPPORTUNITY_ID"),
        help="Optional guard: refuse unless this is the active persisted opportunity",
    )
    parser.add_argument(
        "--confirm-writes",
        action="store_true",
        help="Required acknowledgement that this advances the active demo cycle",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if not args.confirm_writes:
        print(
            "PRODUCTION JOURNEY RESUME: REFUSED - pass --confirm-writes to mutate demo lifecycle state",
            file=sys.stderr,
        )
        return 2
    if not isinstance(args.base_url, str) or not args.base_url.strip():
        print("PRODUCTION JOURNEY RESUME: FAIL - BASE_URL is required", file=sys.stderr)
        return 2

    try:
        summary = run_resume_verification(
            args.base_url.strip(),
            expected_opportunity_id=(
                args.expected_opportunity_id.strip()
                if isinstance(args.expected_opportunity_id, str)
                and args.expected_opportunity_id.strip()
                else None
            ),
        )
    except VerificationFailure as exc:
        print(f"PRODUCTION JOURNEY RESUME: FAIL - {exc}", file=sys.stderr)
        return 1

    print("PRODUCTION JOURNEY RESUME: PASS")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
