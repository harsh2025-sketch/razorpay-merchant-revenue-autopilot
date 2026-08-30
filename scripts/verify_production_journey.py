#!/usr/bin/env python3
"""Controlled write verification for the deployed demo lifecycle.

This is intentionally separate from ``smoke_deployment.py``. It MUTATES the
canonical demo merchant by starting exactly one new optimization cycle and then
advancing it one legal Autopilot transition at a time until a terminal state.
It is intended for release verification, not routine health checks.

Safety properties verified when the reached path permits them:
- the previous cycle remains persisted after rollover,
- a deployed/running experiment cannot be skipped with ``new-cycle``,
- the lifecycle advances only through the public one-step orchestrator,
- the final audit chain remains valid,
- the new cycle ends in a documented terminal outcome instead of hanging.

Usage:

    BASE_URL=https://merchant-revenue-autopilot-api.onrender.com \
      python scripts/verify_production_journey.py --confirm-writes
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

MERCHANT_ID = "merchant_techbazaar"
DEFAULT_TIMEOUT_SECONDS = 120
MAX_STEPS = 24
TERMINAL_STATES = {"COMPLETED", "POLICY_REJECTED", "DEPLOYMENT_BLOCKED"}


class VerificationFailure(Exception):
    """Production lifecycle verification failed."""


def _join_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/") + "/"
    return urllib.parse.urljoin(base, path.lstrip("/"))


def _decode_json(body: bytes, label: str) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationFailure(
            f"{label}: invalid JSON ({exc.__class__.__name__})"
        ) from None


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    expected: set[int] | None = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[int, Any]:
    """Send one JSON request and return ``(status, payload)``.

    No request body is required by the Autopilot step/new-cycle endpoints.
    HTTP errors are decoded as JSON too so expected fail-closed responses can
    be asserted without hiding their stable error code.
    """
    expected = expected or {200}
    url = _join_url(base_url, path)
    request = urllib.request.Request(
        url,
        data=b"" if method == "POST" else None,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        body = exc.read()
    except urllib.error.URLError as exc:
        reason = str(exc.reason).splitlines()[0] if getattr(exc, "reason", None) else "failed"
        raise VerificationFailure(f"{method} {path}: {reason}") from None

    payload = _decode_json(body, f"{method} {path}") if body else None
    if status not in expected:
        safe = json.dumps(payload, sort_keys=True)[:500]
        raise VerificationFailure(
            f"{method} {path}: HTTP {status}, expected {sorted(expected)}; {safe}"
        )
    return status, payload


def _as_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VerificationFailure(f"{label}: expected object")
    return value


def _as_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise VerificationFailure(f"{label}: expected list")
    return value


def _overview(base_url: str) -> dict[str, Any]:
    _, payload = request_json(
        base_url,
        "GET",
        f"/api/v1/merchants/{MERCHANT_ID}/overview",
    )
    return _as_dict(payload, "overview")


def _opportunities(base_url: str) -> list[dict[str, Any]]:
    _, payload = request_json(
        base_url,
        "GET",
        f"/api/v1/merchants/{MERCHANT_ID}/opportunities",
    )
    return [
        _as_dict(item, f"opportunities[{index}]")
        for index, item in enumerate(_as_list(payload, "opportunities"))
    ]


def _cycle(base_url: str, opportunity_id: str) -> dict[str, Any]:
    _, payload = request_json(
        base_url,
        "GET",
        f"/api/v1/opportunities/{opportunity_id}/cycle",
    )
    return _as_dict(payload, "cycle")


def _assert_health(base_url: str) -> None:
    _, payload = request_json(base_url, "GET", "/health")
    body = _as_dict(payload, "health")
    if body.get("status") != "ok":
        raise VerificationFailure("health: status is not ok")


def _assert_rollover_blocked(base_url: str) -> None:
    status, payload = request_json(
        base_url,
        "POST",
        f"/api/v1/merchants/{MERCHANT_ID}/autopilot/new-cycle",
        expected={409},
    )
    body = _as_dict(payload, "blocked rollover")
    detail = _as_dict(body.get("detail"), "blocked rollover.detail")
    if detail.get("code") != "INVALID_TRANSITION":
        raise VerificationFailure(
            f"blocked rollover: expected INVALID_TRANSITION, got {detail.get('code')!r}"
        )
    print(f"  skip protection: PASS (HTTP {status} INVALID_TRANSITION)")


def run_verification(base_url: str) -> dict[str, Any]:
    _assert_health(base_url)
    before = _overview(base_url)
    before_status = _as_dict(before.get("autopilot_status"), "before.autopilot_status")
    previous_opportunity_id = before_status.get("latest_opportunity_id")
    before_opportunity_count = before_status.get("opportunity_count")
    before_experiment_count = before_status.get("experiment_count")

    if before_status.get("state") not in TERMINAL_STATES:
        raise VerificationFailure(
            "existing demo cycle is not terminal; refusing to mutate an in-progress release state"
        )
    if not isinstance(previous_opportunity_id, str) or not previous_opportunity_id:
        raise VerificationFailure("existing terminal cycle has no latest_opportunity_id")

    previous_cycle = _cycle(base_url, previous_opportunity_id)
    previous_result = previous_cycle.get("result")
    previous_policy = previous_cycle.get("policy_decision")

    print(
        "Starting production verification from "
        f"state={before_status.get('state')} opportunity={previous_opportunity_id[:8]}"
    )

    _, next_payload = request_json(
        base_url,
        "POST",
        f"/api/v1/merchants/{MERCHANT_ID}/autopilot/new-cycle",
    )
    next_opportunity = _as_dict(next_payload, "new cycle opportunity")
    new_opportunity_id = next_opportunity.get("id")
    if not isinstance(new_opportunity_id, str) or not new_opportunity_id:
        raise VerificationFailure("new-cycle did not return an opportunity id")
    if new_opportunity_id == previous_opportunity_id:
        raise VerificationFailure("new-cycle reused the terminal opportunity id")

    rows = _opportunities(base_url)
    by_id = {row.get("id"): row for row in rows}
    old_row = by_id.get(previous_opportunity_id)
    if not isinstance(old_row, dict) or old_row.get("status") != "resolved":
        raise VerificationFailure("previous opportunity was not preserved as resolved")
    if new_opportunity_id not in by_id:
        raise VerificationFailure("new opportunity is not present in persisted opportunity history")

    print(
        f"  rollover: PASS ({previous_opportunity_id[:8]} -> {new_opportunity_id[:8]})"
    )

    skip_guard_verified = False
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

        # As soon as a treatment is actually deployed, prove that explicit
        # rollover cannot skip the live experiment before any more batches run.
        if step.get("step") == "RESOURCE_DEPLOYED" and not skip_guard_verified:
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
        raise VerificationFailure(f"Autopilot did not reach a terminal state in {MAX_STEPS} steps")

    after = _overview(base_url)
    after_status = _as_dict(after.get("autopilot_status"), "after.autopilot_status")
    if after.get("audit_chain_valid") is not True or after_status.get("audit_chain_valid") is not True:
        raise VerificationFailure("audit chain is not valid after production cycle")

    if isinstance(before_opportunity_count, int):
        after_count = after_status.get("opportunity_count")
        if not isinstance(after_count, int) or after_count < before_opportunity_count + 1:
            raise VerificationFailure("opportunity history did not grow after rollover")

    if previous_result is not None or previous_policy is not None:
        preserved = _cycle(base_url, previous_opportunity_id)
        if previous_result is not None and preserved.get("result") != previous_result:
            raise VerificationFailure("previous statistical result changed after new cycle")
        if previous_policy is not None and preserved.get("policy_decision") != previous_policy:
            raise VerificationFailure("previous policy decision changed after new cycle")

    new_cycle = _cycle(base_url, new_opportunity_id)
    if new_cycle.get("audit_chain_valid") is not True:
        raise VerificationFailure("new cycle read model reports an invalid audit chain")

    final_state = after_status.get("state")
    if final_state not in TERMINAL_STATES:
        raise VerificationFailure(f"final state is not terminal: {final_state!r}")

    experiment = new_cycle.get("experiment")
    result = new_cycle.get("result")
    policy = new_cycle.get("policy_decision")
    resource = new_cycle.get("razorpay_resource")

    if isinstance(resource, dict):
        external_id = resource.get("razorpay_id")
        if not isinstance(external_id, str) or not external_id.startswith("demo_"):
            raise VerificationFailure(
                "hosted release unexpectedly created a non-demo Razorpay resource"
            )

    terminal_outcome: str
    if isinstance(result, dict) and isinstance(result.get("decision"), str):
        terminal_outcome = result["decision"]
    elif isinstance(policy, dict) and policy.get("decision") == "REJECT":
        terminal_outcome = "POLICY_REJECTED"
    elif final_state == "DEPLOYMENT_BLOCKED":
        terminal_outcome = "DEPLOYMENT_BLOCKED"
    else:
        terminal_outcome = str(final_state)

    if isinstance(before_experiment_count, int) and experiment is not None:
        after_experiment_count = after_status.get("experiment_count")
        if not isinstance(after_experiment_count, int) or after_experiment_count < before_experiment_count + 1:
            raise VerificationFailure("experiment history did not grow for the new cycle")

    return {
        "previous_opportunity_id": previous_opportunity_id,
        "new_opportunity_id": new_opportunity_id,
        "steps": [step.get("step") for step in step_history],
        "skip_guard_verified": skip_guard_verified,
        "final_state": final_state,
        "terminal_outcome": terminal_outcome,
        "audit_chain_valid": True,
        "simulated_resource": isinstance(resource, dict),
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Controlled production lifecycle verification (writes demo state)"
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BASE_URL"),
        help="Backend HTTPS origin, or set BASE_URL",
    )
    parser.add_argument(
        "--confirm-writes",
        action="store_true",
        help="Required acknowledgement that this starts and advances a new demo cycle",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if not args.confirm_writes:
        print(
            "PRODUCTION JOURNEY: REFUSED - pass --confirm-writes to mutate demo lifecycle state",
            file=sys.stderr,
        )
        return 2
    if not isinstance(args.base_url, str) or not args.base_url.strip():
        print("PRODUCTION JOURNEY: FAIL - BASE_URL is required", file=sys.stderr)
        return 2

    try:
        summary = run_verification(args.base_url.strip())
    except VerificationFailure as exc:
        print(f"PRODUCTION JOURNEY: FAIL - {exc}", file=sys.stderr)
        return 1

    print("PRODUCTION JOURNEY: PASS")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
