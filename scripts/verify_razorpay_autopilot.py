#!/usr/bin/env python
"""Controlled end-to-end Razorpay Test Mode proof for Revenue Autopilot.

This is a MANUAL verification script. It is intentionally separate from pytest
because it performs real external writes against Razorpay Test Mode.

It proves the judge-facing execution chain rather than only the raw client:

    persisted experiment
      -> deterministic merchant policy APPROVE
      -> real Task 13 executor deploy
      -> persisted RazorpayResource + idempotency operation
      -> independent Razorpay fetch
      -> existing Task 11 fixed-horizon evaluation traffic
      -> existing Task 12 statistical ROLLBACK
      -> real Task 13 executor cancellation
      -> independent Razorpay cancellation verification
      -> valid hash-chained audit trail

The database is a temporary local SQLite database and is deleted at exit. The
only external object created is one Test Mode Payment Link, which this script
cancels before success. If the normal rollback path cannot run, the finally
block still attempts direct Test Mode cleanup and reports any cleanup failure.

Requirements:
    RAZORPAY_EXECUTION_MODE=real
    RAZORPAY_KEY_ID=rzp_test_...
    RAZORPAY_KEY_SECRET=...

Run from repository root:
    python scripts/verify_razorpay_autopilot.py
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models import (  # noqa: E402
    Experiment,
    Hypothesis,
    Merchant,
    MerchantPolicy,
    OperationExecution,
    Opportunity,
    RazorpayResource,
)
from app.engines.planner import plan_experiment  # noqa: E402
from app.engines.policy import evaluate_experiment_policy  # noqa: E402
from app.services.audit import verify_merchant_audit_chain  # noqa: E402
from app.services.executor import (  # noqa: E402
    DEPLOY_OPERATION_TYPE,
    ROLLBACK_OPERATION_TYPE,
    deploy_experiment_treatment,
    rollback_experiment_treatment,
)
from app.services.one_click_experiment import run_experiment_to_decision  # noqa: E402
from app.services.onboarding import TECHBAZAAR_MERCHANT_ID  # noqa: E402
from app.services.razorpay import RazorpayClient, RazorpayError  # noqa: E402

VERIFY_SAMPLE_TARGET = 1000
VERIFY_SEGMENT = "ios_premium"
VERIFY_EXPIRY_HOURS = 2


@dataclass(frozen=True)
class ProofReport:
    experiment_id: str
    policy_decision: str
    razorpay_id: str
    fetched_status_after_deploy: str
    statistical_decision: str
    control_attempts: int
    treatment_attempts: int
    p_value: float | None
    absolute_lift: float | None
    rollback_operation_status: str
    fetched_status_after_rollback: str
    audit_chain_valid: bool


def _seed_verification_domain(db: Session) -> Experiment:
    """Create only the persisted inputs needed by planner/policy/executor."""
    merchant = Merchant(
        id=TECHBAZAAR_MERCHANT_ID,
        name="TechBazaar Razorpay Test Verification",
        category="consumer_electronics",
        monthly_gmv=500_000_000,
    )
    db.add(merchant)
    db.add(
        MerchantPolicy(
            merchant_id=merchant.id,
            max_experiment_exposure_pct=0.10,
            max_discount_pct=0.15,
            min_margin_pct=0.05,
            max_concurrent_experiments=1,
            max_experiment_duration_hours=168,
            min_sample_size=30,
            max_financial_exposure=50_000,
            allowed_interventions=[
                "payment_method_config",
                "offer_discount",
                "partial_payment",
                "expiry_config",
            ],
        )
    )
    opportunity = Opportunity(
        merchant_id=merchant.id,
        type="verification_only",
        segment=VERIFY_SEGMENT,
        severity=1.0,
        detected_metric="conversion_rate",
        detected_value=0.0,
        baseline_value=0.0,
        evidence={"verification": "razorpay_test_mode_executor_proof"},
        status="detected",
    )
    db.add(opportunity)
    db.flush()

    hypothesis = Hypothesis(
        opportunity_id=opportunity.id,
        merchant_id=merchant.id,
        ai_model="verification_fixture",
        hypothesis_text=(
            "Verification-only harmful short-expiry challenger used to exercise "
            "the real Test Mode deploy and rollback boundaries."
        ),
        intervention_type="expiry_config",
        intervention_params={"expiry_hours": VERIFY_EXPIRY_HOURS},
        confidence="verification",
        reasoning_summary="Fixed deterministic verification fixture; no LLM call.",
        evidence_refs=["verification"],
        status="proposed",
    )
    db.add(hypothesis)
    db.flush()

    experiment = plan_experiment(db, hypothesis.id)
    # Task 12 is fixed-horizon. The canonical 200/variant product default does
    # not always have enough power for the intentionally harmful ~7pp sealed
    # evaluation effect, so the proof fixture raises only the sample horizon.
    # No causal effect, alpha, practical-lift threshold, or treatment parameter
    # is changed.
    experiment.min_sample_per_variant = VERIFY_SAMPLE_TARGET
    db.flush()
    return experiment


def _operation_status(db: Session, *, experiment_id: str, operation_type: str) -> str:
    row = (
        db.query(OperationExecution)
        .filter(OperationExecution.operation_type == operation_type)
        .filter(OperationExecution.operation_key.like(f"experiment:{experiment_id}:%"))
        .order_by(OperationExecution.created_at.desc(), OperationExecution.id.desc())
        .first()
    )
    if row is None:
        raise RuntimeError(f"missing {operation_type} OperationExecution")
    return str(row.status)


def _safe_external_status(payload: dict[str, Any]) -> str:
    value = payload.get("status")
    return str(value) if value is not None else "unknown"


def run_proof(db: Session, client: RazorpayClient) -> ProofReport:
    experiment = _seed_verification_domain(db)

    decision = evaluate_experiment_policy(db, experiment.id)
    if decision.decision != "APPROVE":
        raise RuntimeError(
            f"verification experiment was not policy-approved: {decision.violations}"
        )

    resource = deploy_experiment_treatment(
        db,
        experiment.id,
        razorpay_client=client,
    )
    db.commit()

    if not resource.razorpay_id.startswith("plink_"):
        raise RuntimeError(
            f"executor returned an unexpected Razorpay resource id: {resource.razorpay_id!r}"
        )
    deploy_status = _operation_status(
        db,
        experiment_id=experiment.id,
        operation_type=DEPLOY_OPERATION_TYPE,
    )
    if deploy_status != "succeeded":
        raise RuntimeError(f"deploy operation status is {deploy_status!r}, expected 'succeeded'")

    fetched = client.fetch_payment_link(resource.razorpay_id)
    if fetched.get("id") != resource.razorpay_id:
        raise RuntimeError("independent Razorpay fetch did not return the deployed Payment Link")
    fetched_after_deploy = _safe_external_status(fetched)

    outcome = run_experiment_to_decision(db, experiment.id)
    db.commit()
    if outcome.decision != "ROLLBACK":
        raise RuntimeError(
            "verification fixture did not produce the required deterministic ROLLBACK; "
            f"got {outcome.decision!r} (lift={outcome.absolute_lift!r}, p={outcome.p_value!r})"
        )

    cancelled = rollback_experiment_treatment(
        db,
        experiment.id,
        razorpay_client=client,
    )
    if cancelled is None:
        raise RuntimeError("executor rollback returned no treatment resource")
    db.commit()

    rollback_status = _operation_status(
        db,
        experiment_id=experiment.id,
        operation_type=ROLLBACK_OPERATION_TYPE,
    )
    if rollback_status != "succeeded":
        raise RuntimeError(
            f"rollback operation status is {rollback_status!r}, expected 'succeeded'"
        )

    fetched_cancelled = client.fetch_payment_link(resource.razorpay_id)
    fetched_after_rollback = _safe_external_status(fetched_cancelled)
    if fetched_after_rollback != "cancelled":
        raise RuntimeError(
            "Razorpay Payment Link is not cancelled after executor rollback: "
            f"status={fetched_after_rollback!r}"
        )

    persisted_resource = (
        db.query(RazorpayResource)
        .filter(RazorpayResource.experiment_id == experiment.id)
        .filter(RazorpayResource.razorpay_id == resource.razorpay_id)
        .one()
    )
    if persisted_resource.status != "cancelled":
        raise RuntimeError(
            f"persisted RazorpayResource status is {persisted_resource.status!r}"
        )

    audit_valid = verify_merchant_audit_chain(db, TECHBAZAAR_MERCHANT_ID)
    if not audit_valid:
        raise RuntimeError("merchant audit chain verification failed")

    return ProofReport(
        experiment_id=experiment.id,
        policy_decision=decision.decision,
        razorpay_id=resource.razorpay_id,
        fetched_status_after_deploy=fetched_after_deploy,
        statistical_decision=outcome.decision,
        control_attempts=outcome.control_attempts,
        treatment_attempts=outcome.treatment_attempts,
        p_value=outcome.p_value,
        absolute_lift=outcome.absolute_lift,
        rollback_operation_status=rollback_status,
        fetched_status_after_rollback=fetched_after_rollback,
        audit_chain_valid=audit_valid,
    )


def _print_report(report: ProofReport) -> None:
    print("RAZORPAY AUTOPILOT TEST MODE PROOF: PASS")
    print(f"experiment_id: {report.experiment_id}")
    print(f"policy: {report.policy_decision}")
    print(f"razorpay_resource: {report.razorpay_id}")
    print(f"after_deploy_fetch_status: {report.fetched_status_after_deploy}")
    print(
        "fixed_horizon: "
        f"control={report.control_attempts} treatment={report.treatment_attempts}"
    )
    print(
        "statistics: "
        f"decision={report.statistical_decision} "
        f"lift={report.absolute_lift!r} p={report.p_value!r}"
    )
    print(f"rollback_operation: {report.rollback_operation_status}")
    print(f"after_rollback_fetch_status: {report.fetched_status_after_rollback}")
    print(f"audit_chain_valid: {report.audit_chain_valid}")


def main() -> int:
    settings = get_settings()
    if settings.RAZORPAY_EXECUTION_MODE != "real":
        print(
            "PROOF ABORTED: set RAZORPAY_EXECUTION_MODE=real. "
            "Simulated mode cannot prove Razorpay Test Mode execution."
        )
        return 2

    key_id = settings.RAZORPAY_KEY_ID
    key_secret = settings.RAZORPAY_KEY_SECRET
    if not key_id or not key_secret:
        print("PROOF ABORTED: RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are required.")
        return 2
    if not key_id.startswith("rzp_test_"):
        print(
            "PROOF ABORTED: RAZORPAY_KEY_ID must start with 'rzp_test_'. "
            "Live-mode credentials are refused."
        )
        return 2

    created_resource_id: str | None = None
    with tempfile.TemporaryDirectory(prefix="mra-razorpay-proof-") as temp_dir:
        db_path = Path(temp_dir) / "proof.db"
        engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        try:
            with RazorpayClient(key_id, key_secret) as client:
                try:
                    report = run_proof(db, client)
                    created_resource_id = report.razorpay_id
                    _print_report(report)
                    return 0
                except (RazorpayError, RuntimeError, ValueError) as exc:
                    # Recover the persisted external id, if deployment reached
                    # that point, so the cleanup block can still cancel it.
                    row = (
                        db.query(RazorpayResource)
                        .order_by(RazorpayResource.created_at.desc())
                        .first()
                    )
                    if row is not None:
                        created_resource_id = row.razorpay_id
                    print(f"RAZORPAY AUTOPILOT TEST MODE PROOF: FAIL - {exc}")
                    return 1
                finally:
                    if created_resource_id:
                        try:
                            current = client.fetch_payment_link(created_resource_id)
                            if current.get("status") == "created":
                                client.cancel_payment_link(created_resource_id)
                                print(
                                    "CLEANUP: directly cancelled still-active Test Mode "
                                    f"resource {created_resource_id}"
                                )
                        except RazorpayError as cleanup_exc:
                            print(
                                "CLEANUP WARNING: could not confirm/cancel "
                                f"{created_resource_id}: {cleanup_exc}"
                            )
        finally:
            db.close()
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
