"""Regression: destructive TechBazaar reset clears Task 21B revision state."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from app.db.models import OperationExecution
from app.engines.opportunities import run_opportunity_detection
from app.services.incremental_data import append_next_demo_period, detection_readiness
from tests.test_autopilot_service import db_session


SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from seed_demo import seed_demo  # noqa: E402


MERCHANT_ID = "merchant_techbazaar"


def _task21b_marker_count(db) -> int:
    scope = hashlib.sha256(MERCHANT_ID.encode("utf-8")).hexdigest()
    prefix = f"merchant:{scope}:"
    return (
        db.query(OperationExecution)
        .filter(OperationExecution.operation_key.like(prefix + "%"))
        .count()
    )


def test_seed_demo_reset_clears_incremental_revision_and_detection_markers(db_session):
    seed_demo(db=db_session)

    # Consume the baseline revision and append one deterministic later period.
    run_opportunity_detection(db_session, MERCHANT_ID)
    append_next_demo_period(db_session)
    db_session.commit()
    assert _task21b_marker_count(db_session) >= 2

    # Destructive reset must make the replacement canonical baseline a fresh,
    # unconsumed revision rather than inheriting stale Task 21B state.
    seed_demo(db=db_session)

    assert _task21b_marker_count(db_session) == 0
    readiness = detection_readiness(db_session, MERCHANT_ID)
    assert readiness.ready is True
    assert readiness.reason == "INITIAL_DATA"
