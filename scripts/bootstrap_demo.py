#!/usr/bin/env python3
"""Idempotent production bootstrap for the canonical TechBazaar demo.

This command is safe to run once after provisioning a database, and safe to run
again during deployment checks. It creates missing SQLAlchemy tables and the
canonical baseline merchant/policy/payment attempts only. It does not reset or
advance lifecycle state, does not call OpenAI, and does not create or cancel
Razorpay resources.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Make the backend package and sibling seed helpers importable regardless of cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlalchemy.orm import Session

from app.config import get_settings
from seed_demo import ensure_demo_baseline

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"postgres(?:ql)?(?:\+psycopg)?://[^\s]+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_.\-]+\b"),
    re.compile(r"\brzp_(?:test|live)_[A-Za-z0-9]+\b"),
    re.compile(r"(?i)(password|pwd|pass|key_secret)=([^\s&]+)"),
)


def safe_error_message(exc: BaseException) -> str:
    """Return a compact console error with credentials redacted."""
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    try:
        settings = get_settings()
        sensitive_values = (
            settings.DATABASE_URL,
            settings.OPENAI_API_KEY,
            settings.RAZORPAY_KEY_ID,
            settings.RAZORPAY_KEY_SECRET,
            settings.RAZORPAY_TEST_OFFER_ID,
        )
    except Exception:  # noqa: BLE001 - settings should not mask the root error
        sensitive_values = ()
    for value in sensitive_values:
        if isinstance(value, str) and value:
            text = text.replace(value, "[redacted]")
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:300]


def bootstrap_demo(db: Session | None = None, seed: int = 20260827, days: int = 30) -> dict:
    """Create missing tables and ensure the canonical baseline exists."""
    return ensure_demo_baseline(db=db, seed=seed, days=days)


def main() -> int:
    try:
        summary = bootstrap_demo()
    except Exception as exc:  # noqa: BLE001 - CLI boundary prints a safe failure
        print(f"DEMO BOOTSTRAP: FAIL - {safe_error_message(exc)}", file=sys.stderr)
        return 1

    print("DEMO BOOTSTRAP: PASS")
    print("Tables: verified")
    print(f"Merchant: {summary['merchant_name']} ({summary['merchant_id']})")
    print(
        "Merchant row: "
        f"{'created' if summary['merchant_created'] else 'already present'}"
    )
    print(
        "Merchant policy: "
        f"{'created' if summary['policy_created'] else 'already present'}"
    )
    print(f"Canonical payment attempts: {summary['baseline_attempts_total']}")
    print(f"Payment attempts inserted: {summary['baseline_attempts_inserted']}")
    print(f"Payment attempts already present: {summary['baseline_attempts_existing']}")
    print("Lifecycle state: preserved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
