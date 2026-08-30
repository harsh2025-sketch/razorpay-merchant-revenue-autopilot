#!/usr/bin/env python3
"""Read-only smoke checks for an already deployed backend.

Usage:

    BASE_URL=https://example.onrender.com python scripts/smoke_deployment.py

The script performs only GET requests. It does not advance Autopilot, call
OpenAI, create Razorpay resources, run experiments, or reset the database.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

MERCHANT_ID = "merchant_techbazaar"


class SmokeFailure(Exception):
    """A deployment smoke check failed."""


JsonFetcher = Callable[[str, str], Any]
Validator = Callable[[Any], None]


def _join_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/") + "/"
    return urllib.parse.urljoin(base, path.lstrip("/"))


def request_json(base_url: str, path: str) -> Any:
    """Fetch one endpoint as JSON using a read-only GET request."""
    url = _join_url(base_url, path)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            status = response.status
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise SmokeFailure(f"GET {path}: HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        reason = str(exc.reason).splitlines()[0] if getattr(exc, "reason", None) else "failed"
        raise SmokeFailure(f"GET {path}: {reason}") from None

    if not 200 <= status < 300:
        raise SmokeFailure(f"GET {path}: HTTP {status}")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeFailure(f"GET {path}: invalid JSON ({exc.__class__.__name__})") from None


def _expect_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SmokeFailure(f"{label}: expected object")
    return value


def _expect_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise SmokeFailure(f"{label}: expected list")
    return value


def _expect_merchant_id(value: Any, label: str) -> None:
    if value != MERCHANT_ID:
        raise SmokeFailure(f"{label}: expected merchant_techbazaar")


def validate_health(payload: Any) -> None:
    body = _expect_dict(payload, "/health")
    if body.get("status") != "ok":
        raise SmokeFailure("/health: status is not ok")
    if not isinstance(body.get("service"), str):
        raise SmokeFailure("/health: service missing")


def validate_overview(payload: Any) -> None:
    body = _expect_dict(payload, "overview")
    merchant = _expect_dict(body.get("merchant"), "overview.merchant")
    _expect_merchant_id(merchant.get("merchant_id"), "overview.merchant.merchant_id")
    for key in ("metrics", "segment_metrics", "payment_method_metrics", "autopilot_status"):
        if key not in body:
            raise SmokeFailure(f"overview: missing {key}")
    _expect_dict(body.get("metrics"), "overview.metrics")
    _expect_list(body.get("segment_metrics"), "overview.segment_metrics")
    _expect_list(body.get("payment_method_metrics"), "overview.payment_method_metrics")
    _expect_dict(body.get("autopilot_status"), "overview.autopilot_status")


def validate_opportunities(payload: Any) -> None:
    rows = _expect_list(payload, "opportunities")
    for index, row in enumerate(rows):
        item = _expect_dict(row, f"opportunities[{index}]")
        _expect_merchant_id(item.get("merchant_id"), f"opportunities[{index}].merchant_id")
        for key in ("id", "type", "severity", "status"):
            if key not in item:
                raise SmokeFailure(f"opportunities[{index}]: missing {key}")


def validate_audit(payload: Any) -> None:
    rows = _expect_list(payload, "audit")
    for index, row in enumerate(rows):
        item = _expect_dict(row, f"audit[{index}]")
        for key in ("id", "event_type", "actor", "created_at"):
            if key not in item:
                raise SmokeFailure(f"audit[{index}]: missing {key}")


READ_ONLY_CHECKS: tuple[tuple[str, str, Validator], ...] = (
    ("health", "/health", validate_health),
    ("overview", f"/api/v1/merchants/{MERCHANT_ID}/overview", validate_overview),
    (
        "opportunities",
        f"/api/v1/merchants/{MERCHANT_ID}/opportunities",
        validate_opportunities,
    ),
    ("audit", f"/api/v1/merchants/{MERCHANT_ID}/audit?limit=5", validate_audit),
)


def run_smoke(base_url: str, fetch_json: JsonFetcher = request_json) -> None:
    if not base_url or not base_url.strip():
        raise SmokeFailure("BASE_URL is required")
    for _name, path, validator in READ_ONLY_CHECKS:
        payload = fetch_json(base_url.strip(), path)
        validator(payload)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only backend deployment smoke test")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BASE_URL"),
        help="Backend HTTPS origin, or set BASE_URL",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        run_smoke(args.base_url)
    except SmokeFailure as exc:
        print(f"DEPLOYMENT SMOKE: FAIL - {exc}", file=sys.stderr)
        return 1
    print("DEPLOYMENT SMOKE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
