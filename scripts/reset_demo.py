#!/usr/bin/env python3
"""Explicit reset for the TechBazaar demo merchant and local lifecycle state.

This command is intentionally destructive for the canonical demo merchant's
local database rows. It never runs automatically and never calls OpenAI or
Razorpay; any external Test Mode resources must be handled manually.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add backend directory to sys.path so app modules can be imported
backend_dir = Path(__file__).resolve().parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from sqlalchemy.orm import Session

from seed_demo import DEFAULT_DEMO_DAYS, DEFAULT_DEMO_SEED, print_demo_summary, seed_demo


def reset_demo(
    db: Session | None = None, seed: int = DEFAULT_DEMO_SEED, days: int = DEFAULT_DEMO_DAYS
) -> dict:
    """Reset the demo merchant data by deleting records and regenerating baseline."""
    return seed_demo(db=db, seed=seed, days=days)


def main() -> None:
    summary = reset_demo()

    print("DEMO RESET: PASS")
    print(
        "Warning: reset_demo.py rewrites the canonical demo merchant's local "
        "database state only; it does not contact Razorpay or OpenAI."
    )
    print_demo_summary(summary)


if __name__ == "__main__":
    main()
