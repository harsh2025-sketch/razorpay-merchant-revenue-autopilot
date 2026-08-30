"""Offline regression for scripts/verify_razorpay_autopilot.py.

The manual verifier is credential-gated and performs real Test Mode writes, so
CI injects a tiny stateful fake Razorpay client while keeping the real planner,
policy, executor persistence/idempotency, sealed runtime, statistics, rollback,
and audit code paths intact.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from tests.test_autopilot_service import db_session


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "verify_razorpay_autopilot.py"


def _load_verifier_module():
    spec = importlib.util.spec_from_file_location("verify_razorpay_autopilot", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StatefulFakeRazorpayClient:
    def __init__(self):
        self.links: dict[str, dict] = {}
        self.create_calls = 0
        self.cancel_calls = 0

    def create_payment_link(self, **kwargs):
        self.create_calls += 1
        link_id = "plink_task20_executor_proof"
        payload = {
            "id": link_id,
            "status": "created",
            "reference_id": kwargs.get("reference_id", ""),
        }
        self.links[link_id] = dict(payload)
        return dict(payload)

    def fetch_payment_link(self, payment_link_id: str):
        return dict(self.links[payment_link_id])

    def cancel_payment_link(self, payment_link_id: str):
        self.cancel_calls += 1
        self.links[payment_link_id]["status"] = "cancelled"
        return dict(self.links[payment_link_id])


def test_controlled_proof_runs_real_domain_chain_offline(db_session):
    verifier = _load_verifier_module()
    client = StatefulFakeRazorpayClient()

    report = verifier.run_proof(db_session, client)

    assert report.policy_decision == "APPROVE"
    assert report.razorpay_id == "plink_task20_executor_proof"
    assert report.fetched_status_after_deploy == "created"
    assert report.control_attempts >= verifier.VERIFY_SAMPLE_TARGET
    assert report.treatment_attempts >= verifier.VERIFY_SAMPLE_TARGET
    assert report.statistical_decision == "ROLLBACK"
    assert report.p_value is not None and report.p_value < 0.05
    assert report.absolute_lift is not None and report.absolute_lift <= -0.02
    assert report.rollback_operation_status == "succeeded"
    assert report.fetched_status_after_rollback == "cancelled"
    assert report.audit_chain_valid is True
    assert client.create_calls == 1
    assert client.cancel_calls == 1
