"""Tests for the Task 15 FastAPI product API and its one-step orchestration.

Everything is offline: a temporary SQLite database is wired in through the
standard ``get_db`` dependency override, and both external boundaries are
injected fakes (fake OpenAI structured output, fake Razorpay client). No live
credentials and no network access are required.

API requirements covered, in order:

21. health preserved
22. merchant GET 200
23. missing merchant 404
24. overview response valid
25. opportunities list valid
26. missing opportunity 404
27. experiment GET valid
28. merchant audit route valid
29. experiment audit route valid
30. detect POST commits a successful operation
31. a failing detect rolls the transaction back
32. diagnose works with the injected fake OpenAI
33. plan works
34. policy approve route works
35. policy reject response works
36. deploy works with the injected fake Razorpay client
37. deployment authentication failure is mapped safely
38. run endpoint works
39. invalid batch sizes are mapped safely
40. evaluate before the sample target conflicts
41. evaluate at the fixed horizon works
42. rollback is allowed for a ROLLBACK decision
43. KEEP never permits cancellation
44. autopilot step endpoint works
45. no API key is ever exposed
46. no hidden causal field is ever exposed
47. CORS local origin configured
48. invalid ids do not leak stack traces

Plus Task 13's operation ledger across external failures: an ambiguous deploy
or rollback keeps its ``pending`` record (so no second external write happens),
a definitive 4xx keeps its ``failed`` record, and ordinary domain errors still
roll back completely.

Plus the full HTTP lifecycle chain, the transaction-boundary rules, merchant
isolation over HTTP, and the documented route surface.

Plus Task 17A's frontend-readiness read models: overview segment and
payment-method metrics from the Task 07 engine, and the one composite
``GET /opportunities/{id}/cycle`` lifecycle read model that a detail-page
refresh can rebuild the whole persisted cycle from (partial stages included).
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Point the default engine at a throwaway file before any app import touches
# app.db.session; every route below uses the overridden test session anyway.
_TMP = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP.name}"
os.environ.pop("CORS_ALLOWED_ORIGINS", None)
os.environ.pop("CORS_ORIGINS", None)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.engines.planner as planner_module  # noqa: E402
from app.api import schemas  # noqa: E402
from app.api.router import get_openai_client, get_razorpay_client  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.models import (  # noqa: E402
    AuditEvent,
    Experiment,
    ExperimentResult,
    Hypothesis,
    Merchant,
    MerchantPolicy,
    OperationExecution,
    Opportunity,
    PaymentAttempt,
    PolicyDecision,
    RazorpayResource,
)
from app.db.session import get_db  # noqa: E402
from app.main import DEFAULT_CORS_ORIGINS, app, create_app, parse_cors_origins  # noqa: E402
from app.services import autopilot  # noqa: E402
from app.services.executor import (  # noqa: E402
    DEPLOY_OPERATION_TYPE,
    ROLLBACK_OPERATION_TYPE,
)
from app.services.razorpay import (  # noqa: E402
    RazorpayAuthenticationError,
    RazorpayBadRequestError,
    RazorpayError,
)
from tests.test_autopilot_service import (  # noqa: E402
    ALL_INTERVENTIONS,
    MERCHANT,
    OTHER_MERCHANT,
    SEGMENT,
    add_attempts,
    add_policy_decision,
    add_resource,
    add_result,
    make_experiment,
    make_hypothesis,
    make_merchant,
    make_opportunity,
    seed_baseline,
)
from tests.test_diagnosis_engine import MOCK_MODEL_RESPONSE, FakeOpenAIClient  # noqa: E402
from tests.test_experiment_executor import FakeRazorpayClient  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]

API = "/api/v1"

#: Values that must never appear anywhere in an API response body.
SENTINEL_OPENAI_KEY = "sk-sentinel-openai-key-never-leak"
SENTINEL_RAZORPAY_ID = "rzp_test_sentinelkeyid0000"
SENTINEL_RAZORPAY_SECRET = "sentinel-razorpay-secret-never-leak"
FORBIDDEN_BODY_MARKERS = (
    SENTINEL_OPENAI_KEY,
    SENTINEL_RAZORPAY_ID,
    SENTINEL_RAZORPAY_SECRET,
    "sk-",
    "rzp_test_",
    "api_key",
    "key_secret",
)
FORBIDDEN_CAUSAL_MARKERS = (
    "causal",
    "hidden_",
    "expected_lift",
    "treatment_effect",
    "simulator_truth",
    "chain_of_thought",
    "prompt",
    "true_uplift",
    "intervention_effect",
    "sealed",
)

#: Canonical TechBazaar segments and payment methods produced by the
#: deterministic baseline generator (Task 05/07).
CANONICAL_SEGMENTS = {
    "android_mid",
    "android_budget",
    "web_general",
    "repeat_buyer",
    "ios_premium",
}
CANONICAL_PAYMENT_METHODS = ["upi", "card", "netbanking", "wallet"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_session(tmp_path):
    db_file = tmp_path / "test_api.db"
    engine = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def committed_view(db_session):
    """A second session on the same file, so commits can be proven."""
    other = sessionmaker(bind=db_session.get_bind(), autocommit=False, autoflush=False)()
    try:
        yield other
    finally:
        other.close()


@pytest.fixture
def openai_spy():
    return FakeOpenAIClient(payload=MOCK_MODEL_RESPONSE)


@pytest.fixture
def razorpay_spy():
    return FakeRazorpayClient()


@pytest.fixture
def client(db_session, openai_spy, razorpay_spy):
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_openai_client] = lambda: openai_spy
    app.dependency_overrides[get_razorpay_client] = lambda: razorpay_spy
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def merchant(db_session):
    make_merchant(db_session)
    db_session.commit()
    return MERCHANT


@pytest.fixture
def small_horizon(monkeypatch):
    """Shrink the fixed sample horizon so the offline chain stays fast."""
    monkeypatch.setattr(planner_module, "DEFAULT_MIN_SAMPLE_PER_VARIANT", 20)


def override_razorpay(monkeypatch, razorpay_client):
    """Swap only the Razorpay dependency for one test, then restore it."""
    monkeypatch.setattr(
        app,
        "dependency_overrides",
        {**app.dependency_overrides, get_razorpay_client: lambda: razorpay_client},
    )


class FailThenSucceedRazorpayClient(FakeRazorpayClient):
    """Refuses exactly the first external write.

    Used to prove that a *definitive* failure is recorded without wedging the
    operation, while the API itself never retries on the caller's behalf.
    """

    def create_payment_link(self, **kwargs):
        error, self.create_error = self.create_error, None
        if error is not None:
            self.create_calls.append(kwargs)
            raise error
        return super().create_payment_link(**kwargs)


def ledger_rows(session):
    return (
        session.query(OperationExecution)
        .order_by(OperationExecution.operation_key)
        .all()
    )


def ambiguous_timeout(message="Razorpay request did not complete: connection timed out"):
    """A failure whose status code proves nothing about the external outcome."""
    return RazorpayError(message, status_code=None)


def deployable_experiment(db, **kwargs):
    """An approved experiment with a real treatment resource, ready for traffic."""
    experiment = make_experiment(db, status="approved", **kwargs)
    add_policy_decision(db, experiment, decision="APPROVE")
    add_resource(db, experiment)
    return experiment


# ---------------------------------------------------------------------------
# 21-29. Health and read routes
# ---------------------------------------------------------------------------


def test_health_is_preserved(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "merchant-revenue-autopilot",
    }


def test_merchant_get(client, merchant):
    response = client.get(f"{API}/merchants/{merchant}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["merchant_id"] == MERCHANT
    assert payload["name"] == f"Merchant {MERCHANT}"
    assert payload["category"] == "consumer_electronics"
    assert payload["monthly_gmv_paise"] == 500_000_000
    assert set(payload) == set(schemas.MerchantSummary.model_fields)


def test_missing_merchant_is_404_everywhere(client):
    for path in (
        f"{API}/merchants/ghost",
        f"{API}/merchants/ghost/overview",
        f"{API}/merchants/ghost/opportunities",
        f"{API}/merchants/ghost/audit",
    ):
        response = client.get(path)
        assert response.status_code == 404, path
        assert response.json()["detail"]["code"] == "NOT_FOUND"

    assert client.post(f"{API}/merchants/ghost/detect").status_code == 404
    assert client.post(f"{API}/merchants/ghost/autopilot/step").status_code == 404


def test_overview_response(client, merchant, db_session):
    rows = seed_baseline(db_session)
    db_session.commit()
    attempts = db_session.query(PaymentAttempt)
    captured = attempts.filter_by(status="captured").count()
    failed = attempts.filter_by(status="failed").count()
    abandoned = attempts.filter_by(status="abandoned").count()

    response = client.get(f"{API}/merchants/{merchant}/overview")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == set(schemas.MerchantOverviewResponse.model_fields)
    assert payload["metrics"] == {
        "attempts": rows,
        "captured": captured,
        "failed": failed,
        "abandoned": abandoned,
        "conversion_rate": pytest.approx(captured / rows),
    }
    assert payload["merchant"]["merchant_id"] == MERCHANT
    assert payload["attempted_gmv_paise"] > payload["captured_gmv_paise"] > 0
    assert payload["active_opportunity_count"] == 0
    assert payload["active_experiment_count"] == 0
    assert payload["latest_experiment"] is None
    assert payload["latest_result"] is None
    assert payload["audit_chain_valid"] is True
    status = payload["autopilot_status"]
    assert set(status) == set(schemas.AutopilotStatusResponse.model_fields)
    assert status["state"] == autopilot.STATE_IDLE
    assert status["next_action"] == autopilot.ACTION_DETECT
    assert status["progress"] is None
    # Only observed money is reported: no revenue-loss estimate is invented.
    assert "₹" not in json.dumps(payload)
    assert "lost" not in json.dumps(payload).lower()


def test_opportunities_list(client, merchant, db_session):
    make_merchant(db_session, OTHER_MERCHANT)
    make_opportunity(db_session, MERCHANT, severity=0.5)
    make_opportunity(db_session, MERCHANT, severity=0.1, segment="ios_premium")
    make_opportunity(db_session, OTHER_MERCHANT, severity=0.9)
    db_session.commit()

    response = client.get(f"{API}/merchants/{merchant}/opportunities")

    assert response.status_code == 200
    rows = response.json()
    assert [row["severity"] for row in rows] == [0.5, 0.1]
    assert {row["merchant_id"] for row in rows} == {MERCHANT}
    assert set(rows[0]) == set(schemas.OpportunityResponse.model_fields)
    assert rows[0]["status"] == "detected"
    # Evidence stays observable: the sanitizer keeps causal-shaped keys out.
    assert "payment_method_metrics" in rows[0]["evidence"]


def test_single_opportunity_and_missing_404(client, merchant, db_session):
    opportunity = make_opportunity(db_session, MERCHANT)
    db_session.commit()

    found = client.get(f"{API}/opportunities/{opportunity.id}")
    assert found.status_code == 200
    assert found.json()["id"] == opportunity.id
    assert found.json()["segment"] == SEGMENT

    missing = client.get(f"{API}/opportunities/00000000-0000-4000-8000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "NOT_FOUND"


def test_experiment_get(client, merchant, db_session):
    experiment = make_experiment(db_session, status="approved")
    db_session.commit()

    response = client.get(f"{API}/experiments/{experiment.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == experiment.id
    assert payload["status"] == "approved"
    assert payload["opportunity_id"] == experiment.opportunity_id
    assert payload["hypothesis_id"] == experiment.hypothesis_id
    assert payload["primary_metric"] == "conversion_rate"
    # Semantic configs only: no Razorpay payload, no internals.
    assert payload["treatment_config"] == {"payment_methods": {"card": False, "upi": True}}
    assert set(payload) == set(schemas.ExperimentResponse.model_fields)
    assert client.get(f"{API}/experiments/nope").status_code == 404


def test_merchant_and_experiment_audit_routes(client, merchant, db_session):
    opportunity = make_opportunity(db_session, MERCHANT)
    db_session.commit()

    diagnosed = client.post(f"{API}/opportunities/{opportunity.id}/diagnose")
    assert diagnosed.status_code == 200

    merchant_audit = client.get(f"{API}/merchants/{merchant}/audit")
    assert merchant_audit.status_code == 200
    events = merchant_audit.json()
    assert [event["event_type"] for event in events][-2:] == [
        "AI_DIAGNOSIS_CREATED",
        "HYPOTHESIS_PROPOSED",
    ]
    # Only the contracted audit fields are exposed - no internal columns.
    assert set(events[0]) == {
        "id",
        "event_type",
        "actor",
        "entity_type",
        "entity_id",
        "data",
        "created_at",
        "prev_hash",
        "event_hash",
    }
    assert events[0]["prev_hash"] is None
    assert all(event["event_hash"] for event in events)

    hypothesis_id = diagnosed.json()["id"]
    planned = client.post(f"{API}/hypotheses/{hypothesis_id}/plan")
    assert planned.status_code == 200
    experiment_id = planned.json()["id"]

    experiment_audit = client.get(f"{API}/experiments/{experiment_id}/audit")
    assert experiment_audit.status_code == 200
    assert [event["event_type"] for event in experiment_audit.json()] == [
        "EXPERIMENT_PLANNED"
    ]

    assert len(client.get(f"{API}/merchants/{merchant}/audit?limit=1").json()) == 1
    assert client.get(f"{API}/merchants/{merchant}/audit?limit=0").status_code == 422
    assert client.get(f"{API}/merchants/{merchant}/audit?limit=99999").status_code == 422
    assert client.get(f"{API}/experiments/nope/audit").status_code == 404


# ---------------------------------------------------------------------------
# 30-37. Write routes
# ---------------------------------------------------------------------------


def test_detect_commits_a_successful_operation(client, merchant, db_session, committed_view):
    seed_baseline(db_session)
    db_session.commit()
    assert committed_view.query(Opportunity).count() == 0

    response = client.post(f"{API}/merchants/{merchant}/detect")

    assert response.status_code == 200
    rows = response.json()
    assert rows
    committed_view.expire_all()
    assert committed_view.query(Opportunity).count() == len(rows)
    assert {row["merchant_id"] for row in rows} == {MERCHANT}


def test_detect_is_json_safe_and_idempotent(client, merchant, db_session):
    seed_baseline(db_session)
    db_session.commit()

    first = client.post(f"{API}/merchants/{merchant}/detect")
    second = client.post(f"{API}/merchants/{merchant}/detect")

    assert first.status_code == 200
    assert json.dumps(first.json())
    assert [row["id"] for row in second.json()] == [row["id"] for row in first.json()]
    assert db_session.query(Opportunity).count() == len(first.json())


def test_failed_detect_rolls_back_uncommitted_state(
    client, merchant, db_session, committed_view, monkeypatch
):
    def exploding_detection(db, merchant_id, **_kwargs):
        db.add(
            Opportunity(
                id="doomed-opportunity",
                merchant_id=merchant_id,
                type="segment_conversion_divergence",
                segment=SEGMENT,
                severity=0.9,
                detected_metric="conversion_rate",
                detected_value=0.1,
                baseline_value=0.2,
                evidence={},
                status="detected",
            )
        )
        db.flush()
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(autopilot, "run_detection", exploding_detection)

    response = client.post(f"{API}/merchants/{merchant}/detect")

    assert response.status_code == 500
    assert response.json()["detail"] == {
        "code": "INTERNAL_ERROR",
        "message": "The request could not be completed.",
    }
    assert "RuntimeError" not in response.text
    assert "engine exploded" not in response.text
    committed_view.expire_all()
    assert committed_view.query(Opportunity).count() == 0
    db_session.expire_all()
    assert db_session.query(Opportunity).count() == 0


def test_diagnose_uses_the_injected_fake_openai(client, merchant, db_session, openai_spy):
    opportunity = make_opportunity(db_session, MERCHANT)
    db_session.commit()

    response = client.post(f"{API}/opportunities/{opportunity.id}/diagnose")

    assert response.status_code == 200
    payload = response.json()
    assert payload["opportunity_id"] == opportunity.id
    assert payload["intervention_type"] == "payment_method_config"
    assert payload["status"] == "proposed"
    assert payload["ai_model"]
    assert set(payload) == set(schemas.HypothesisResponse.model_fields)
    assert len(openai_spy.chat.completions.calls) == 1

    # The route stays idempotent because the engine suppresses duplicates.
    again = client.post(f"{API}/opportunities/{opportunity.id}/diagnose")
    assert again.json()["id"] == payload["id"]
    assert db_session.query(Hypothesis).count() == 1


def test_diagnose_without_openai_configuration_is_503(client, merchant, db_session, monkeypatch):
    opportunity = make_opportunity(db_session, MERCHANT)
    db_session.commit()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()
    app.dependency_overrides[get_openai_client] = lambda: None

    try:
        response = client.post(f"{API}/opportunities/{opportunity.id}/diagnose")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "OPENAI_NOT_CONFIGURED"
    # A configuration error may name the setting, never its value.
    assert SENTINEL_OPENAI_KEY not in response.text
    assert db_session.query(Hypothesis).count() == 0


def test_diagnose_missing_opportunity_is_404(client, merchant):
    assert client.post(f"{API}/opportunities/nope/diagnose").status_code == 404


def test_plan_route(client, merchant, db_session):
    opportunity = make_opportunity(db_session, MERCHANT)
    hypothesis = make_hypothesis(db_session, opportunity)
    db_session.commit()

    response = client.post(f"{API}/hypotheses/{hypothesis.id}/plan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["hypothesis_id"] == hypothesis.id
    assert payload["opportunity_id"] == opportunity.id
    assert payload["status"] == "proposed"
    assert payload["min_sample_per_variant"] > 0
    assert payload["max_duration_hours"] > 0
    assert set(payload) == set(schemas.ExperimentResponse.model_fields)
    assert db_session.query(Experiment).count() == 1

    assert client.post(f"{API}/hypotheses/no-such-hypothesis/plan").status_code == 404


def test_policy_approve_and_reject(client, merchant, db_session):
    approved_experiment = make_experiment(db_session, status="proposed")
    rejected_experiment = make_experiment(
        db_session,
        intervention_type="offer_discount",
        treatment_config={"discount_pct": 0.40},
        status="proposed",
    )
    db_session.commit()

    approved = client.post(f"{API}/experiments/{approved_experiment.id}/policy")
    assert approved.status_code == 200
    assert approved.json()["decision"] == "APPROVE"
    assert approved.json()["violations"] == []
    assert approved.json()["experiment_id"] == approved_experiment.id
    assert set(approved.json()) == set(schemas.PolicyDecisionResponse.model_fields)
    assert db_session.get(Experiment, approved_experiment.id).status == "approved"

    rejected = client.post(f"{API}/experiments/{rejected_experiment.id}/policy")
    assert rejected.status_code == 200
    assert rejected.json()["decision"] == "REJECT"
    assert "DISCOUNT_LIMIT_EXCEEDED" in rejected.json()["violations"]
    assert db_session.get(Experiment, rejected_experiment.id).status == "rejected"

    # The persisted decision is returned; the engine is never re-run twice.
    again = client.post(f"{API}/experiments/{approved_experiment.id}/policy")
    assert again.json()["id"] == approved.json()["id"]
    assert db_session.query(PolicyDecision).count() == 2


def test_policy_route_reports_missing_experiment_and_policy(client, merchant, db_session):
    assert client.post(f"{API}/experiments/nope/policy").status_code == 404

    db_session.add(Merchant(id="merchant_without_policy", name="No policy"))
    opportunity = make_opportunity(db_session, "merchant_without_policy")
    experiment = make_experiment(db_session, opportunity=opportunity)
    db_session.commit()

    response = client.post(f"{API}/experiments/{experiment.id}/policy")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "MERCHANT_POLICY_NOT_CONFIGURED"


def test_deploy_creates_one_real_test_resource(client, merchant, db_session, razorpay_spy):
    experiment = make_experiment(db_session, status="approved")
    add_policy_decision(db_session, experiment, decision="APPROVE")
    db_session.commit()

    response = client.post(f"{API}/experiments/{experiment.id}/deploy")

    assert response.status_code == 200
    payload = response.json()
    assert payload["razorpay_id"] == "plink_1"
    assert payload["status"] == "active"
    assert payload["variant"] == "treatment"
    assert payload["resource_type"] == "payment_link"
    assert payload["experiment_id"] == experiment.id
    assert set(payload) == set(schemas.RazorpayResourceResponse.model_fields)
    assert len(razorpay_spy.create_calls) == 1

    # Repeated deploy reuses the successful operation: no second write.
    again = client.post(f"{API}/experiments/{experiment.id}/deploy")
    assert again.json()["id"] == payload["id"]
    assert len(razorpay_spy.create_calls) == 1
    assert db_session.query(RazorpayResource).count() == 1


def test_deploy_requires_policy_authorization(client, merchant, db_session, razorpay_spy):
    rejected = make_experiment(db_session, status="rejected")
    add_policy_decision(db_session, rejected, decision="REJECT", violations=["X"])
    unapproved = make_experiment(db_session, status="approved")
    db_session.commit()

    for experiment in (rejected, unapproved):
        response = client.post(f"{API}/experiments/{experiment.id}/deploy")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "EXECUTION_NOT_AUTHORIZED"

    assert razorpay_spy.create_calls == []
    assert db_session.query(RazorpayResource).count() == 0


def test_offer_discount_deploy_is_a_controlled_configuration_error(
    client, merchant, db_session, razorpay_spy
):
    experiment = make_experiment(
        db_session, intervention_type="offer_discount", status="approved"
    )
    add_policy_decision(db_session, experiment, decision="APPROVE")
    db_session.commit()

    response = client.post(f"{API}/experiments/{experiment.id}/deploy")

    # Task 13 refuses to invent an Offer id; the API reports that refusal
    # instead of pretending a resource exists.
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "DEPLOYMENT_CONFIG_UNSUPPORTED"
    assert "offer" in response.json()["detail"]["message"].lower()
    assert razorpay_spy.create_calls == []
    assert db_session.query(RazorpayResource).count() == 0


def test_missing_razorpay_configuration_is_503(client, merchant, db_session, monkeypatch):
    experiment = make_experiment(db_session, status="approved")
    add_policy_decision(db_session, experiment, decision="APPROVE")
    db_session.commit()
    for name in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    app.dependency_overrides[get_razorpay_client] = lambda: None

    try:
        response = client.post(f"{API}/experiments/{experiment.id}/deploy")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "RAZORPAY_NOT_CONFIGURED"
    assert SENTINEL_RAZORPAY_SECRET not in response.text


def test_deployment_auth_failure_is_mapped_without_leaks(
    client, merchant, db_session, monkeypatch
):
    # A real upstream error text can contain the app's own credentials, so the
    # API scrubs configured settings as well as secret-shaped substrings.
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI_KEY)
    monkeypatch.setenv("RAZORPAY_KEY_ID", SENTINEL_RAZORPAY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", SENTINEL_RAZORPAY_SECRET)
    get_settings.cache_clear()
    experiment = make_experiment(db_session, status="approved")
    add_policy_decision(db_session, experiment, decision="APPROVE")
    db_session.commit()

    failing = FakeRazorpayClient(
        create_error=RazorpayAuthenticationError(
            "Razorpay API error (HTTP 401): Authorization failed for "
            f"{SENTINEL_RAZORPAY_ID} / {SENTINEL_RAZORPAY_SECRET}",
            status_code=401,
        )
    )
    monkeypatch.setattr(app, "dependency_overrides", {**app.dependency_overrides, get_razorpay_client: lambda: failing})

    try:
        response = client.post(f"{API}/experiments/{experiment.id}/deploy")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "RAZORPAY_API_FAILURE"
    assert SENTINEL_OPENAI_KEY not in response.text
    assert SENTINEL_RAZORPAY_SECRET not in response.text
    assert SENTINEL_RAZORPAY_ID not in response.text
    assert "[redacted]" in response.text
    assert "Traceback" not in response.text
    assert db_session.query(RazorpayResource).count() == 0


def test_run_endpoint_uses_runtime_validation(client, merchant, db_session):
    experiment = deployable_experiment(db_session, min_sample=200)
    db_session.commit()

    response = client.post(
        f"{API}/experiments/{experiment.id}/run",
        json={"batch_size": 25, "seed": 20260827},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == set(schemas.ExperimentRunResponse.model_fields)
    assert payload["generated_attempts"] == 25
    assert payload["control_attempts"] + payload["treatment_attempts"] == 25
    assert payload["sample_target_per_variant"] == 200
    assert payload["treatment_attempts"] + payload["treatment_remaining"] == 200
    assert payload["status"] == "running"
    # Nothing statistical leaks into a runtime summary.
    for forbidden in ("p_value", "lift", "significan"):
        assert forbidden not in json.dumps(payload).lower()

    # An omitted body falls back to the documented default batch size, and the
    # runtime stops as soon as both variants reach the fixed horizon.
    default_run = client.post(f"{API}/experiments/{experiment.id}/run")
    assert default_run.status_code == 200
    follow_up = default_run.json()
    assert 0 < follow_up["generated_attempts"] <= 500
    assert follow_up["control_attempts"] > payload["control_attempts"]
    assert follow_up["sample_target_reached"] if "sample_target_reached" in follow_up else True
    total = follow_up["control_attempts"] + follow_up["treatment_attempts"]
    assert total == payload["control_attempts"] + payload["treatment_attempts"] + follow_up["generated_attempts"]


@pytest.mark.parametrize(
    "body",
    [
        {"batch_size": 0},
        {"batch_size": -5},
        {"batch_size": 10**9},
        {"batch_size": "many"},
        {"batch_size": True},
        {"unexpected": 1},
        {"seed": None},
        {"batch_size": 10, "seed": "abc"},
    ],
)
def test_invalid_run_requests_are_rejected(client, merchant, db_session, body):
    experiment = deployable_experiment(db_session, min_sample=200)
    db_session.commit()

    response = client.post(f"{API}/experiments/{experiment.id}/run", json=body)

    assert response.status_code == 422
    assert "Traceback" not in response.text
    assert db_session.query(PaymentAttempt).count() == 0


def test_run_on_a_rejected_experiment_conflicts(client, merchant, db_session):
    experiment = make_experiment(db_session, status="rejected")
    db_session.commit()

    response = client.post(f"{API}/experiments/{experiment.id}/run", json={"batch_size": 10})

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "INVALID_TRANSITION"


# ---------------------------------------------------------------------------
# 40-43. Evaluation and rollback
# ---------------------------------------------------------------------------


def test_evaluate_before_the_sample_target_conflicts(client, merchant, db_session):
    experiment = make_experiment(db_session, status="running", min_sample=100)
    add_resource(db_session, experiment)
    add_attempts(db_session, experiment, variant="control", count=10, captured=5)
    add_attempts(db_session, experiment, variant="treatment", count=4, captured=2)
    db_session.commit()

    response = client.post(f"{API}/experiments/{experiment.id}/evaluate")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_TRANSITION"
    assert "fixed horizon" in detail["message"]
    assert db_session.query(ExperimentResult).count() == 0


def test_evaluate_at_the_horizon_records_the_decision(client, merchant, db_session):
    experiment = make_experiment(db_session, status="running", min_sample=10)
    add_resource(db_session, experiment)
    add_attempts(db_session, experiment, variant="control", count=10, captured=3)
    add_attempts(db_session, experiment, variant="treatment", count=10, captured=8)
    db_session.commit()

    response = client.post(f"{API}/experiments/{experiment.id}/evaluate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision"] == "KEEP"
    assert payload["control_count"] == 10
    assert payload["treatment_conversions"] == 8
    assert payload["experiment_id"] == experiment.id
    assert set(payload) == set(schemas.ExperimentResultResponse.model_fields)
    assert db_session.get(Experiment, experiment.id).status == "completed"

    # Re-evaluation is idempotent: the persisted decision is returned.
    again = client.post(f"{API}/experiments/{experiment.id}/evaluate")
    assert again.json()["decision"] == "KEEP"
    assert db_session.query(ExperimentResult).count() == 1


def test_rollback_requires_an_explicit_rollback_decision(
    client, merchant, db_session, razorpay_spy
):
    keep_experiment = make_experiment(db_session, status="completed", min_sample=10)
    keep_resource = add_resource(db_session, keep_experiment)
    add_result(db_session, keep_experiment, decision="KEEP")
    inconclusive = make_experiment(db_session, status="completed", min_sample=10)
    add_resource(db_session, inconclusive)
    add_result(db_session, inconclusive, decision="INCONCLUSIVE")
    without_result = make_experiment(db_session, status="completed", min_sample=10)
    db_session.commit()

    for experiment in (keep_experiment, inconclusive, without_result):
        response = client.post(f"{API}/experiments/{experiment.id}/rollback")
        assert response.status_code == 403, experiment.id
        assert response.json()["detail"]["code"] == "EXECUTION_NOT_AUTHORIZED"

    assert razorpay_spy.cancel_calls == []
    db_session.expire_all()
    assert db_session.get(RazorpayResource, keep_resource.id).status == "active"

    rollback_experiment = make_experiment(db_session, status="completed", min_sample=10)
    resource = add_resource(db_session, rollback_experiment)
    add_result(db_session, rollback_experiment, decision="ROLLBACK")
    db_session.commit()

    allowed = client.post(f"{API}/experiments/{rollback_experiment.id}/rollback")
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "rolled_back"
    assert allowed.json()["resource"]["razorpay_id"] == resource.razorpay_id
    assert allowed.json()["experiment_id"] == rollback_experiment.id
    assert razorpay_spy.cancel_calls == [resource.razorpay_id]
    db_session.expire_all()
    assert db_session.get(RazorpayResource, resource.id).status == "cancelled"

    # A second rollback is idempotent and never cancels twice.
    repeated = client.post(f"{API}/experiments/{rollback_experiment.id}/rollback")
    assert repeated.json()["status"] == "rolled_back"
    assert razorpay_spy.cancel_calls == [resource.razorpay_id]

    no_resource = make_experiment(db_session, status="completed", min_sample=10)
    add_result(db_session, no_resource, decision="ROLLBACK")
    db_session.commit()
    nothing = client.post(f"{API}/experiments/{no_resource.id}/rollback")
    assert nothing.status_code == 200
    assert nothing.json() == {
        "experiment_id": no_resource.id,
        "status": "no_active_resource",
        "resource": None,
    }


# ---------------------------------------------------------------------------
# 44. Autopilot stepping
# ---------------------------------------------------------------------------


def test_autopilot_step_advances_one_stage_at_a_time(client, merchant, db_session):
    seed_baseline(db_session)
    db_session.commit()

    steps = [
        client.post(f"{API}/merchants/{merchant}/autopilot/step").json()
        for _ in range(5)
    ]

    assert [step["step"] for step in steps] == [
        "OPPORTUNITY_DETECTED",
        "HYPOTHESIS_PROPOSED",
        "EXPERIMENT_PLANNED",
        "POLICY_APPROVED",
        "RESOURCE_DEPLOYED",
    ]
    for step in steps:
        assert set(step) == set(schemas.AutopilotStepResponse.model_fields)
        assert step["merchant_id"] == MERCHANT
        assert step["message"]
        assert step["next_action"]
    # Ids connect: the planned experiment is the one policy approved and the
    # one that now owns a resource.
    assert steps[2]["entity_id"] == steps[3]["entity_id"]
    experiment_id = steps[2]["entity_id"]
    assert client.get(f"{API}/experiments/{experiment_id}").json()["status"] == "approved"
    assert client.get(f"{API}/experiments/{experiment_id}").json()["id"] == experiment_id

    status = client.get(f"{API}/merchants/{merchant}/overview").json()["autopilot_status"]
    assert status["state"] == "RUNNING"
    assert status["next_action"] == "RUN_EXPERIMENT_BATCH"
    assert status["latest_experiment_id"] == experiment_id
    assert status["latest_decision"] == "APPROVE"
    assert status["latest_resource_status"] == "active"
    assert status["progress"]["sample_target_per_variant"] > 0
    assert status["progress"]["treatment_attempts"] == 0

    batched = client.post(f"{API}/merchants/{merchant}/autopilot/step").json()
    assert batched["step"] == "EXPERIMENT_BATCH_RUN"
    assert client.get(f"{API}/merchants/{merchant}/overview").json()["autopilot_status"][
        "progress"
    ]["treatment_attempts"] > 0


def test_autopilot_step_reports_policy_rejection_without_replanning(
    client, merchant, db_session
):
    make_experiment(
        db_session,
        intervention_type="offer_discount",
        treatment_config={"discount_pct": 0.40},
        status="proposed",
    )
    db_session.commit()

    rejected = client.post(f"{API}/merchants/{merchant}/autopilot/step").json()
    again = client.post(f"{API}/merchants/{merchant}/autopilot/step").json()

    assert rejected["step"] == "POLICY_REJECTED"
    assert rejected["status"] == "POLICY_REJECTED"
    assert rejected["next_action"] == "STOP"
    assert again == rejected
    assert db_session.query(Hypothesis).count() == 1
    assert db_session.query(Experiment).count() == 1


def test_autopilot_step_blocks_offer_discount_deployment(client, merchant, db_session):
    experiment = make_experiment(
        db_session, intervention_type="offer_discount", status="approved"
    )
    add_policy_decision(db_session, experiment, decision="APPROVE")
    db_session.commit()

    step = client.post(f"{API}/merchants/{merchant}/autopilot/step")

    # A blocked real deployment is a visible state, never an HTTP 500.
    assert step.status_code == 200
    assert step.json()["step"] == "DEPLOYMENT_BLOCKED"
    assert step.json()["status"] == "DEPLOYMENT_BLOCKED"
    assert step.json()["next_action"] == "CONFIGURE_OFFER_MAPPING"
    assert db_session.get(Experiment, experiment.id).status == "approved"


def test_merchant_isolation_over_http(client, db_session):
    make_merchant(db_session, MERCHANT)
    make_merchant(db_session, OTHER_MERCHANT)
    mine = make_opportunity(db_session, MERCHANT, severity=0.2)
    theirs = make_opportunity(db_session, OTHER_MERCHANT, severity=0.9)
    db_session.commit()

    mine_rows = client.get(f"{API}/merchants/{MERCHANT}/opportunities").json()
    their_rows = client.get(f"{API}/merchants/{OTHER_MERCHANT}/opportunities").json()

    assert [row["id"] for row in mine_rows] == [mine.id]
    assert [row["id"] for row in their_rows] == [theirs.id]
    assert client.get(f"{API}/merchants/{OTHER_MERCHANT}/audit").json() == []
    assert client.get(f"{API}/merchants/{OTHER_MERCHANT}/overview").json()[
        "active_experiment_count"
    ] == 0
    # Stepping one merchant never advances the other.
    step = client.post(f"{API}/merchants/{OTHER_MERCHANT}/autopilot/step").json()
    assert step["step"] == "HYPOTHESIS_PROPOSED"
    assert step["merchant_id"] == OTHER_MERCHANT
    created = db_session.get(Hypothesis, step["entity_id"])
    assert created.opportunity_id == theirs.id
    assert created.merchant_id == OTHER_MERCHANT
    assert db_session.query(Hypothesis).filter_by(merchant_id=MERCHANT).count() == 0
    assert client.get(f"{API}/merchants/{MERCHANT}/opportunities").json()[0]["id"] == mine.id


# ---------------------------------------------------------------------------
# 45-48. Safety, CORS, error hygiene
# ---------------------------------------------------------------------------


def test_api_never_exposes_keys_or_causal_fields(
    client, merchant, db_session, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", SENTINEL_OPENAI_KEY)
    monkeypatch.setenv("RAZORPAY_KEY_ID", SENTINEL_RAZORPAY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", SENTINEL_RAZORPAY_SECRET)
    get_settings.cache_clear()
    try:
        seed_baseline(db_session)
        opportunity = make_opportunity(db_session, MERCHANT)
        db_session.commit()

        bodies = [
            client.post(f"{API}/merchants/{merchant}/detect").text,
            client.post(f"{API}/opportunities/{opportunity.id}/diagnose").text,
        ]
        hypothesis_id = db_session.query(Hypothesis).one().id
        bodies.append(client.post(f"{API}/hypotheses/{hypothesis_id}/plan").text)
        experiment_id = db_session.query(Experiment).one().id
        bodies.append(client.post(f"{API}/experiments/{experiment_id}/policy").text)
        bodies.append(client.post(f"{API}/experiments/{experiment_id}/deploy").text)
        bodies.append(
            client.post(f"{API}/experiments/{experiment_id}/run", json={"batch_size": 5}).text
        )
        bodies.append(client.post(f"{API}/merchants/{merchant}/autopilot/step").text)
        # Failure paths are the likeliest leak, so they are scanned too.
        bodies.append(client.post(f"{API}/experiments/nope/deploy").text)
        bodies.append(client.post(f"{API}/experiments/{experiment_id}/evaluate").text)
        bodies.append(client.post(f"{API}/experiments/{experiment_id}/rollback").text)
        bodies.append(client.get(f"{API}/merchants/ghost/overview").text)
        bodies.append(client.get(f"{API}/opportunities/no-such-id").text)
        for path in (
            "/health",
            f"{API}/merchants/{merchant}",
            f"{API}/merchants/{merchant}/overview",
            f"{API}/merchants/{merchant}/opportunities",
            f"{API}/merchants/{merchant}/audit",
            f"{API}/opportunities/{opportunity.id}",
            f"{API}/experiments/{experiment_id}",
            f"{API}/experiments/{experiment_id}/audit",
        ):
            bodies.append(client.get(path).text)

        joined = "\n".join(bodies)
        for marker in FORBIDDEN_BODY_MARKERS:
            assert marker not in joined, marker
        lowered = joined.lower()
        for marker in FORBIDDEN_CAUSAL_MARKERS:
            assert marker not in lowered, marker
    finally:
        get_settings.cache_clear()


def test_invalid_ids_do_not_leak_stack_traces(client, merchant):
    paths = [
        ("get", f"{API}/opportunities/not-a-uuid"),
        ("get", f"{API}/experiments/not-a-uuid"),
        ("get", f"{API}/experiments/not-a-uuid/audit"),
        ("post", f"{API}/experiments/not-a-uuid/policy"),
        ("post", f"{API}/experiments/not-a-uuid/deploy"),
        ("post", f"{API}/experiments/not-a-uuid/run"),
        ("post", f"{API}/experiments/not-a-uuid/evaluate"),
        ("post", f"{API}/experiments/not-a-uuid/rollback"),
        ("post", f"{API}/hypotheses/not-a-uuid/plan"),
        ("post", f"{API}/opportunities/not-a-uuid/diagnose"),
    ]

    for method, path in paths:
        response = getattr(client, method)(path)
        assert response.status_code in (404, 409, 422), (path, response.status_code)
        text = response.text
        for forbidden in ("Traceback", "File \"", "sqlalchemy", "psycopg", "Session object"):
            assert forbidden not in text, (path, forbidden)
        if response.status_code == 404:
            body = response.json()
            assert set(body) == {"detail"}
            assert set(body["detail"]) == {"code", "message"}
            assert body["detail"]["code"] == "NOT_FOUND"


def test_unknown_request_fields_are_rejected(client, merchant, db_session):
    experiment = deployable_experiment(db_session, min_sample=200)
    db_session.commit()

    response = client.post(
        f"{API}/experiments/{experiment.id}/run",
        json={"batch_size": 10, "expected_lift": 0.4},
    )

    assert response.status_code == 422


def test_get_routes_never_commit(client, merchant, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("GET routes must not commit")

    # Instance-level patch only: the test session is the one routes share.
    monkeypatch.setattr(db_session, "commit", forbidden, raising=False)

    for path in (
        f"{API}/merchants/{merchant}",
        f"{API}/merchants/{merchant}/overview",
        f"{API}/merchants/{merchant}/opportunities",
        f"{API}/merchants/{merchant}/audit",
    ):
        assert client.get(path).status_code == 200, path


# ---------------------------------------------------------------------------
# 47. CORS
# ---------------------------------------------------------------------------


def test_cors_allows_the_local_dashboard_origin(client, merchant):
    preflight = client.options(
        f"{API}/merchants/{merchant}/detect",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    actual = client.get(
        f"{API}/merchants/{merchant}", headers={"Origin": "http://localhost:3000"}
    )

    assert parse_cors_origins(None) == list(DEFAULT_CORS_ORIGINS) == ["http://localhost:3000"]
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert preflight.headers["access-control-allow-methods"] == "GET, POST, OPTIONS"
    assert actual.status_code == 200
    assert actual.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_rejects_other_origins(client, merchant):
    response = client.get(f"{API}/merchants/{merchant}", headers={"Origin": "http://evil.test"})

    assert response.status_code in (200, 404)
    assert "access-control-allow-origin" not in response.headers


def test_cors_parsing_and_wildcard_rule():
    assert parse_cors_origins("https://dash.vercel.app") == ["https://dash.vercel.app"]
    assert parse_cors_origins(" http://a.test , http://a.test ,, http://b.test ") == [
        "http://a.test",
        "http://b.test",
    ]
    assert parse_cors_origins("") == list(DEFAULT_CORS_ORIGINS)
    assert parse_cors_origins("   ") == list(DEFAULT_CORS_ORIGINS)

    explicit = create_app(cors_origins=["http://localhost:3000"]).user_middleware[0]
    assert explicit.kwargs["allow_origins"] == ["http://localhost:3000"]
    # The dashboard does not use credentialed browser requests.
    assert explicit.kwargs["allow_credentials"] is False

    wildcard = create_app(cors_origins=["*"]).user_middleware[0]
    assert wildcard.kwargs["allow_origins"] == ["*"]
    # A wildcard is never combined with credentialed requests.
    assert wildcard.kwargs["allow_credentials"] is False


def test_cors_allows_a_configured_single_origin(merchant):
    configured_client = TestClient(create_app(cors_origins=["https://dash.example.com"]))

    preflight = configured_client.options(
        f"{API}/merchants/{merchant}/detect",
        headers={
            "Origin": "https://dash.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://dash.example.com"


def test_cors_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(
        "CORS_ALLOWED_ORIGINS", "https://mra.onrender.com, http://localhost:3000"
    )
    monkeypatch.delenv("CORS_ORIGINS", raising=False)

    middleware = create_app().user_middleware[0]

    assert middleware.kwargs["allow_origins"] == [
        "https://mra.onrender.com",
        "http://localhost:3000",
    ]
    assert middleware.kwargs["allow_credentials"] is False


def test_cors_legacy_environment_alias_still_works(monkeypatch):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("CORS_ORIGINS", "https://legacy.example.com")

    middleware = create_app().user_middleware[0]

    assert middleware.kwargs["allow_origins"] == ["https://legacy.example.com"]
    assert middleware.kwargs["allow_credentials"] is False


def test_application_boots_without_openai_or_razorpay_credentials(monkeypatch):
    for name in ("OPENAI_API_KEY", "RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()

    try:
        local_client = TestClient(create_app())
        response = local_client.get("/health")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Task 13 operation ledger across external failures
# ---------------------------------------------------------------------------


def test_ambiguous_deploy_timeout_preserves_the_pending_ledger(
    client, merchant, db_session, committed_view, monkeypatch
):
    experiment = make_experiment(db_session, status="approved")
    add_policy_decision(db_session, experiment, decision="APPROVE")
    db_session.commit()
    razorpay = FakeRazorpayClient(create_error=ambiguous_timeout())
    override_razorpay(monkeypatch, razorpay)

    first = client.post(f"{API}/experiments/{experiment.id}/deploy")

    # The request itself still fails the documented way...
    assert first.status_code == 502
    assert first.json()["detail"]["code"] == "RAZORPAY_API_FAILURE"
    assert len(razorpay.create_calls) == 1

    # ...while Task 13's record of the ambiguous write survives the request.
    committed_view.expire_all()
    rows = ledger_rows(committed_view)
    assert len(rows) == 1
    assert rows[0].operation_type == DEPLOY_OPERATION_TYPE
    assert experiment.id in rows[0].operation_key
    assert rows[0].status == "pending"
    assert rows[0].response_json == {"error": "ambiguous_network_failure"}
    assert rows[0].razorpay_resource_id is None

    # Nothing else was persisted: no resource, no audit event, no state move.
    assert committed_view.query(RazorpayResource).count() == 0
    assert committed_view.get(Experiment, experiment.id).status == "approved"
    assert (
        committed_view.query(AuditEvent)
        .filter_by(event_type="RAZORPAY_RESOURCE_CREATED")
        .count()
        == 0
    )

    second = client.post(f"{API}/experiments/{experiment.id}/deploy")

    # A repeated deploy is refused from the ledger instead of calling out again.
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "EXECUTION_STATE_CONFLICT"
    assert "already pending or ambiguous" in second.json()["detail"]["message"]
    assert len(razorpay.create_calls) == 1
    assert ledger_rows(committed_view)[0].status == "pending"


def test_ambiguous_deploy_is_reported_by_the_read_routes_too(
    client, merchant, db_session, monkeypatch
):
    experiment = make_experiment(db_session, status="approved")
    add_policy_decision(db_session, experiment, decision="APPROVE")
    db_session.commit()
    override_razorpay(monkeypatch, FakeRazorpayClient(create_error=ambiguous_timeout()))

    assert client.post(f"{API}/experiments/{experiment.id}/deploy").status_code == 502
    blocked = client.post(f"{API}/experiments/{experiment.id}/deploy")

    # A GET stays read-only even when the lifecycle is blocked this way.
    overview = client.get(f"{API}/merchants/{merchant}/overview").json()
    assert overview["active_experiment_count"] == 1
    assert overview["latest_experiment"]["id"] == experiment.id
    # "none" is the documented sentinel for "no resource exists yet".
    assert overview["autopilot_status"]["latest_resource_status"] == "none"
    assert overview["audit_chain_valid"] is True
    assert blocked.status_code == 409


def test_ambiguous_deploy_inside_an_autopilot_step_does_not_advance(
    client, merchant, db_session, committed_view, monkeypatch
):
    experiment = make_experiment(db_session, status="approved")
    add_policy_decision(db_session, experiment, decision="APPROVE")
    db_session.commit()
    razorpay = FakeRazorpayClient(create_error=ambiguous_timeout())
    override_razorpay(monkeypatch, razorpay)

    step = client.post(f"{API}/merchants/{merchant}/autopilot/step")

    assert step.status_code == 502
    assert step.json()["detail"]["code"] == "RAZORPAY_API_FAILURE"
    assert len(razorpay.create_calls) == 1
    committed_view.expire_all()
    assert ledger_rows(committed_view)[0].status == "pending"
    assert committed_view.query(RazorpayResource).count() == 0

    again = client.post(f"{API}/merchants/{merchant}/autopilot/step")

    # The step is blocked by the ledger; the external write is not repeated.
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "EXECUTION_STATE_CONFLICT"
    assert len(razorpay.create_calls) == 1


def test_definitive_4xx_deploy_failure_persists_as_failed(
    client, merchant, db_session, committed_view, monkeypatch
):
    experiment = make_experiment(db_session, status="approved")
    add_policy_decision(db_session, experiment, decision="APPROVE")
    db_session.commit()
    razorpay = FailThenSucceedRazorpayClient(
        create_error=RazorpayBadRequestError(
            "Razorpay API error (HTTP 400): amount is below the minimum",
            status_code=400,
        )
    )
    override_razorpay(monkeypatch, razorpay)

    failed = client.post(f"{API}/experiments/{experiment.id}/deploy")

    assert failed.status_code == 502
    assert failed.json()["detail"]["code"] == "RAZORPAY_API_FAILURE"
    assert len(razorpay.create_calls) == 1

    committed_view.expire_all()
    rows = ledger_rows(committed_view)
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert rows[0].response_json == {"error": "definitive_api_failure", "status_code": 400}
    assert rows[0].razorpay_resource_id is None
    assert committed_view.query(RazorpayResource).count() == 0
    assert committed_view.get(Experiment, experiment.id).status == "approved"

    # The API never retried on its own; the next explicit request is what moves
    # the operation forward, reusing the same ledger row.
    deployed = client.post(f"{API}/experiments/{experiment.id}/deploy")

    assert deployed.status_code == 200
    # The explicit retry created its own resource (the fake numbers ids by call).
    assert deployed.json()["razorpay_id"] == "plink_2"
    assert len(razorpay.create_calls) == 2
    committed_view.expire_all()
    rows = ledger_rows(committed_view)
    assert len(rows) == 1
    assert rows[0].status == "succeeded"
    assert rows[0].razorpay_resource_id == "plink_2"
    assert committed_view.query(RazorpayResource).count() == 1


def test_ambiguous_rollback_preserves_the_pending_ledger(
    client, merchant, db_session, committed_view, monkeypatch
):
    experiment = make_experiment(db_session, status="completed", min_sample=10)
    resource = add_resource(db_session, experiment)
    add_result(db_session, experiment, decision="ROLLBACK")
    db_session.commit()
    razorpay = FakeRazorpayClient(cancel_error=ambiguous_timeout())
    override_razorpay(monkeypatch, razorpay)

    first = client.post(f"{API}/experiments/{experiment.id}/rollback")

    assert first.status_code == 502
    assert first.json()["detail"]["code"] == "RAZORPAY_API_FAILURE"
    assert razorpay.cancel_calls == [resource.razorpay_id]

    committed_view.expire_all()
    rows = ledger_rows(committed_view)
    assert len(rows) == 1
    assert rows[0].operation_type == ROLLBACK_OPERATION_TYPE
    assert rows[0].status == "pending"
    assert rows[0].response_json == {"error": "ambiguous_network_failure"}
    # The resource stays active: a cancel that may not have happened is not
    # rewritten into a cancelled row, and no rollback is audited.
    assert committed_view.get(RazorpayResource, resource.id).status == "active"
    assert (
        committed_view.query(AuditEvent)
        .filter_by(event_type="EXPERIMENT_ROLLED_BACK")
        .count()
        == 0
    )

    second = client.post(f"{API}/experiments/{experiment.id}/rollback")

    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "EXECUTION_STATE_CONFLICT"
    assert razorpay.cancel_calls == [resource.razorpay_id]


def test_ordinary_errors_still_roll_back_everything(
    client, merchant, db_session, committed_view, monkeypatch
):
    unapproved = make_experiment(db_session, status="approved")
    db_session.commit()

    refused = client.post(f"{API}/experiments/{unapproved.id}/deploy")

    # A refusal raised before the external boundary writes nothing at all.
    assert refused.status_code == 403
    assert refused.json()["detail"]["code"] == "EXECUTION_NOT_AUTHORIZED"
    committed_view.expire_all()
    assert ledger_rows(committed_view) == []
    assert committed_view.query(RazorpayResource).count() == 0

    def exploding_batch(db, experiment_id, **_kwargs):
        add_attempts(
            db, db.get(Experiment, experiment_id), variant="control", count=3, captured=1
        )
        db.flush()
        raise RuntimeError("runtime exploded")

    monkeypatch.setattr(autopilot, "execute_experiment_batch", exploding_batch)
    experiment = deployable_experiment(db_session, min_sample=200)
    db_session.commit()
    before = committed_view.query(PaymentAttempt).count()

    broken = client.post(
        f"{API}/experiments/{experiment.id}/run", json={"batch_size": 10}
    )

    # /run never opts into ledger preservation: a mid-flight failure is
    # all-or-nothing, and no ledger row is invented for it.
    assert broken.status_code == 500
    assert broken.json()["detail"]["code"] == "INTERNAL_ERROR"
    committed_view.expire_all()
    assert committed_view.query(PaymentAttempt).count() == before
    assert ledger_rows(committed_view) == []


def test_step_route_rolls_back_ordinary_engine_failures(
    client, merchant, db_session, committed_view, monkeypatch
):
    def exploding_detection(db, merchant_id, **_kwargs):
        db.add(
            Opportunity(
                id="doomed-in-a-step",
                merchant_id=merchant_id,
                type="segment_conversion_divergence",
                segment=SEGMENT,
                severity=0.8,
                detected_metric="conversion_rate",
                detected_value=0.1,
                baseline_value=0.2,
                evidence={},
                status="detected",
            )
        )
        db.flush()
        raise ValueError("detector exploded")

    monkeypatch.setattr(autopilot, "run_detection", exploding_detection)

    step = client.post(f"{API}/merchants/{merchant}/autopilot/step")

    assert step.status_code == 500
    committed_view.expire_all()
    assert committed_view.query(Opportunity).count() == 0
    assert ledger_rows(committed_view) == []


def test_failed_external_write_persists_only_the_ledger_row(
    client, merchant, db_session, monkeypatch
):
    """The ledger commit cannot smuggle lifecycle state past the rollback.

    Captured at the driver level: during a failed deploy the only statements
    issued are writes to the operation ledger itself.
    """
    experiment = make_experiment(db_session, status="approved")
    add_policy_decision(db_session, experiment, decision="APPROVE")
    db_session.commit()
    override_razorpay(monkeypatch, FakeRazorpayClient(create_error=ambiguous_timeout()))

    engine = db_session.get_bind()
    statements: list[str] = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    try:
        assert client.post(f"{API}/experiments/{experiment.id}/deploy").status_code == 502
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    written = set()
    for statement in statements:
        match = re.match(r"(?i)^\s*(INSERT INTO|UPDATE|DELETE FROM)\s+([a-z_]+)", statement)
        if match:
            written.add(match.group(2))
    assert written == {"operation_executions"}


def test_a_failing_ledger_commit_never_replaces_the_external_error(
    client, merchant, db_session, committed_view, monkeypatch
):
    experiment = make_experiment(db_session, status="approved")
    add_policy_decision(db_session, experiment, decision="APPROVE")
    db_session.commit()
    razorpay = FakeRazorpayClient(create_error=ambiguous_timeout())
    override_razorpay(monkeypatch, razorpay)

    def refusing_commit():
        raise RuntimeError("connection lost while writing the ledger")

    monkeypatch.setattr(db_session, "commit", refusing_commit, raising=False)

    response = client.post(f"{API}/experiments/{experiment.id}/deploy")

    # The caller still sees the mapped Razorpay failure, not a database detail.
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "RAZORPAY_API_FAILURE"
    assert "connection lost" not in response.text
    assert "Traceback" not in response.text
    assert len(razorpay.create_calls) == 1
    # Nothing half-landed: the discarded session took the marker with it.
    committed_view.expire_all()
    assert committed_view.query(RazorpayResource).count() == 0
    assert committed_view.get(Experiment, experiment.id).status == "approved"


def test_only_external_capable_routes_opt_into_ledger_preservation():
    source = (BACKEND_DIR / "app" / "api" / "router.py").read_text(encoding="utf-8")

    preserving = set()
    for block in source.split("@router.")[1:]:
        declared = re.search(r'(?:get|post)\(\s*"([^"]+)"', block)
        if declared and "preserve_external=True" in block:
            preserving.add(declared.group(1))

    assert preserving == {
        "/experiments/{experiment_id}/deploy",
        "/experiments/{experiment_id}/rollback",
        "/merchants/{merchant_id}/autopilot/step",
    }


# ---------------------------------------------------------------------------
# Route surface and layering
# ---------------------------------------------------------------------------


def test_documented_route_surface_is_exactly_the_planned_one(client):
    schema = client.get("/openapi.json").json()

    paths = {path for path in schema["paths"] if path.startswith(API)}
    assert paths == {
        f"{API}/merchants/{{merchant_id}}",
        f"{API}/merchants/{{merchant_id}}/overview",
        f"{API}/merchants/{{merchant_id}}/intelligence",
        f"{API}/merchants/{{merchant_id}}/opportunities",
        f"{API}/merchants/{{merchant_id}}/audit",
        f"{API}/merchants/{{merchant_id}}/detect",
        f"{API}/merchants/{{merchant_id}}/autopilot/step",
        f"{API}/opportunities/{{opportunity_id}}",
        f"{API}/opportunities/{{opportunity_id}}/cycle",
        f"{API}/opportunities/{{opportunity_id}}/diagnose",
        f"{API}/hypotheses/{{hypothesis_id}}/plan",
        f"{API}/experiments/{{experiment_id}}",
        f"{API}/experiments/{{experiment_id}}/audit",
        f"{API}/experiments/{{experiment_id}}/policy",
        f"{API}/experiments/{{experiment_id}}/deploy",
        f"{API}/experiments/{{experiment_id}}/run",
        f"{API}/experiments/{{experiment_id}}/evaluate",
        f"{API}/experiments/{{experiment_id}}/rollback",
    }
    assert schema["paths"]["/health"]["get"]["responses"]["200"]["description"]
    cycle_response = schema["paths"][f"{API}/opportunities/{{opportunity_id}}/cycle"][
        "get"
    ]["responses"]["200"]
    assert "AutopilotCycleResponse" in json.dumps(cycle_response)
    step_response = schema["paths"][f"{API}/merchants/{{merchant_id}}/autopilot/step"][
        "post"
    ]["responses"]["200"]
    assert "AutopilotStepResponse" in json.dumps(step_response)
    # The only POSTs that accept a body are the ones documented as taking one.
    run_body = schema["paths"][f"{API}/experiments/{{experiment_id}}/run"]["post"]
    assert "RunBatchRequest" in json.dumps(run_body)


def test_api_router_file_does_not_own_business_logic():
    source = (BACKEND_DIR / "app" / "api" / "router.py").read_text(encoding="utf-8")

    for forbidden in (
        "math.sqrt",
        "erfc",
        "p_value",
        "VIOLATION_",
        "create_payment_link",
        "chat.completions",
        "simulate_outcome",
        "SELECT",
        "db.query(",
    ):
        assert forbidden not in source, forbidden


def test_api_router_never_operates_the_idempotency_ledger_itself():
    """The boundary keeps the ledger row; it never writes or reads it.

    Checked on identifiers rather than raw text so that documentation naming
    Task 13's model cannot satisfy or break this guard.
    """
    tree = ast.parse(
        (BACKEND_DIR / "app" / "api" / "router.py").read_text(encoding="utf-8")
    )

    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            identifiers.update(
                alias.asname or alias.name.split(".")[-1] for alias in node.names
            )
            if isinstance(node, ast.ImportFrom) and node.module:
                identifiers.add(node.module)

    forbidden = {
        "OperationExecution",
        "idempotency",
        "begin_operation",
        "mark_operation_ambiguous",
        "mark_operation_failed",
        "mark_operation_succeeded",
        "compute_request_hash",
        "RazorpayClient",
    }
    assert not identifiers & forbidden, identifiers & forbidden


@pytest.mark.parametrize(
    "entry_point",
    ["app.simulation", "app.engines.opportunities", "app.api.router"],
)
def test_app_package_import_order_stays_acyclic(entry_point: str):
    """Importing any layer first must work (scripts do not import app.main)."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {entry_point}"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr[-800:]


# ---------------------------------------------------------------------------
# Full HTTP chain
# ---------------------------------------------------------------------------


def test_full_api_chain_over_http(
    client, merchant, db_session, committed_view, small_horizon
):
    """seed -> detect -> diagnose -> plan -> policy -> deploy -> run -> evaluate."""
    seed_baseline(db_session)
    db_session.commit()

    assert client.get(f"{API}/merchants/{merchant}").json()["merchant_id"] == MERCHANT

    detected = client.post(f"{API}/merchants/{merchant}/detect").json()
    assert detected, "the seeded baseline must yield an opportunity"
    opportunity_id = detected[0]["id"]

    hypothesis = client.post(f"{API}/opportunities/{opportunity_id}/diagnose").json()
    assert hypothesis["opportunity_id"] == opportunity_id
    assert hypothesis["evidence_refs"]

    hypothesis_id = hypothesis["id"]
    experiment = client.post(f"{API}/hypotheses/{hypothesis_id}/plan").json()
    assert experiment["hypothesis_id"] == hypothesis_id
    assert experiment["opportunity_id"] == opportunity_id
    experiment_id = experiment["id"]

    decision = client.post(f"{API}/experiments/{experiment_id}/policy").json()
    assert decision["decision"] == "APPROVE"
    assert decision["experiment_id"] == experiment_id
    assert decision["merchant_id"] == MERCHANT

    resource = client.post(f"{API}/experiments/{experiment_id}/deploy").json()
    assert resource["status"] == "active"
    assert resource["experiment_id"] == experiment_id

    # Batches until the fixed horizon is reached, then evaluation.
    too_early = client.post(f"{API}/experiments/{experiment_id}/evaluate")
    summary = None
    for _ in range(40):
        summary = client.post(
            f"{API}/experiments/{experiment_id}/run",
            json={"batch_size": 500, "seed": 20260827},
        ).json()
        if summary["control_remaining"] == 0 and summary["treatment_remaining"] == 0:
            break
    assert summary is not None
    assert summary["control_remaining"] == 0
    assert summary["treatment_remaining"] == 0
    assert summary["status"] == "running"
    assert summary["sample_target_per_variant"] == 20
    assert too_early.status_code == 409

    result = client.post(f"{API}/experiments/{experiment_id}/evaluate").json()
    assert result["experiment_id"] == experiment_id
    assert result["decision"] in {"KEEP", "ROLLBACK", "INCONCLUSIVE"}
    assert result["control_count"] >= 20

    assert client.get(f"{API}/experiments/{experiment_id}").json()["status"] == "completed"

    overview_payload = client.get(f"{API}/merchants/{merchant}/overview").json()
    assert overview_payload["latest_experiment"]["id"] == experiment_id
    assert overview_payload["latest_result"]["decision"] == result["decision"]
    assert overview_payload["audit_chain_valid"] is True
    assert overview_payload["active_experiment_count"] == 0
    assert overview_payload["autopilot_status"]["latest_statistical_decision"] == (
        result["decision"]
    )

    # The lifecycle then reports its terminal state through the step endpoint.
    closing = client.post(f"{API}/merchants/{merchant}/autopilot/step").json()
    assert closing["step"] in {"RESOURCE_ROLLED_BACK", "COMPLETED"}
    if result["decision"] == "ROLLBACK":
        assert closing["step"] == "RESOURCE_ROLLED_BACK"
        assert client.get(f"{API}/experiments/{experiment_id}").json()["status"] == (
            "completed"
        )
        rolled_back = client.get(f"{API}/experiments/{experiment_id}/audit").json()
        assert rolled_back[-1]["event_type"] == "EXPERIMENT_ROLLED_BACK"
    repeated = client.post(f"{API}/merchants/{merchant}/autopilot/step").json()
    assert repeated == closing

    expected_experiment_events = [
        "EXPERIMENT_PLANNED",
        "POLICY_APPROVED",
        "RAZORPAY_RESOURCE_CREATED",
        "EXPERIMENT_STARTED",
        "EXPERIMENT_COMPLETED",
    ]
    if result["decision"] == "KEEP":
        expected_experiment_events.append("TREATMENT_PROMOTED")
    if result["decision"] == "ROLLBACK":
        expected_experiment_events += [
            "RAZORPAY_RESOURCE_CANCELLED",
            "EXPERIMENT_ROLLED_BACK",
        ]
    assert [
        event["event_type"] for event in client.get(f"{API}/experiments/{experiment_id}/audit").json()
    ] == expected_experiment_events

    merchant_events = client.get(f"{API}/merchants/{merchant}/audit").json()
    merchant_types = [event["event_type"] for event in merchant_events]
    for expected in (
        "OPPORTUNITY_DETECTED",
        "AI_DIAGNOSIS_CREATED",
        "HYPOTHESIS_PROPOSED",
        *expected_experiment_events,
    ):
        assert expected in merchant_types, expected
    assert merchant_types[0] == "OPPORTUNITY_DETECTED"
    assert merchant_events[0]["prev_hash"] is None
    assert all(event["event_hash"] for event in merchant_events)

    from app.services.audit import verify_merchant_audit_chain

    assert verify_merchant_audit_chain(db_session, MERCHANT) is True

    # Everything that came back was JSON-safe, and ids cross-reference.
    combined = json.dumps(
        {
            "detected": detected,
            "hypothesis": hypothesis,
            "experiment": experiment,
            "decision": decision,
            "resource": resource,
            "summary": summary,
            "result": result,
            "overview": overview_payload,
            "events": merchant_events,
        }
    )
    assert combined
    committed_view.expire_all()
    assert committed_view.get(Experiment, experiment_id).status == "completed"
    assert (
        committed_view.query(Opportunity).filter_by(id=opportunity_id).one().merchant_id
        == MERCHANT
    )
    assert (
        committed_view.query(RazorpayResource)
        .filter_by(experiment_id=experiment_id)
        .one()
        .razorpay_id
        == resource["razorpay_id"]
    )
    assert (
        committed_view.query(PolicyDecision)
        .filter_by(experiment_id=experiment_id)
        .one()
        .decision
        == "APPROVE"
    )
    simulated = (
        committed_view.query(PaymentAttempt)
        .filter_by(experiment_id=experiment_id)
        .count()
    )
    assert simulated == summary["control_attempts"] + summary["treatment_attempts"]
    assert all(
        attempt.is_simulated
        for attempt in committed_view.query(PaymentAttempt).filter_by(
            experiment_id=experiment_id
        )
    )


# ---------------------------------------------------------------------------
# Task 17A: overview segment / payment-method readiness read models
# ---------------------------------------------------------------------------


def _segment_truth(db, merchant_id: str) -> dict[str, dict[str, int]]:
    """Independent per-segment truth, computed straight from PaymentAttempts."""
    truth: dict[str, dict[str, int]] = {}
    for attempt in db.query(PaymentAttempt).filter_by(merchant_id=merchant_id).all():
        if attempt.segment is None:
            continue
        entry = truth.setdefault(
            attempt.segment,
            {
                "attempts": 0,
                "captured": 0,
                "failed": 0,
                "abandoned": 0,
                "gmv": 0,
                "captured_gmv": 0,
            },
        )
        entry["attempts"] += 1
        entry["gmv"] += attempt.amount
        if attempt.status == "captured":
            entry["captured"] += 1
            entry["captured_gmv"] += attempt.amount
        elif attempt.status == "failed":
            entry["failed"] += 1
        elif attempt.status == "abandoned":
            entry["abandoned"] += 1
    return truth


def test_overview_includes_segment_metrics_reflecting_persisted_attempts(
    client, merchant, db_session
):
    rows = seed_baseline(db_session)
    db_session.commit()
    assert rows > 0

    payload = client.get(f"{API}/merchants/{merchant}/overview").json()

    segment_metrics = payload["segment_metrics"]
    assert segment_metrics
    for row in segment_metrics:
        assert set(row) == set(schemas.SegmentMetricsResponse.model_fields)

    # Every seeded TechBazaar segment appears, unlabelled and unsorted-for
    # importance: the frontend sorts and displays them itself.
    assert {row["segment"] for row in segment_metrics} == CANONICAL_SEGMENTS

    # Values reflect the persisted PaymentAttempt rows, not recomputed trends.
    truth = _segment_truth(db_session, MERCHANT)
    by_segment = {row["segment"]: row for row in segment_metrics}
    for segment, entry in truth.items():
        row = by_segment[segment]
        assert row["attempts"] == entry["attempts"]
        assert row["captured"] == entry["captured"]
        assert row["failed"] == entry["failed"]
        assert row["abandoned"] == entry["abandoned"]
        assert row["gmv_paise"] == entry["gmv"]
        assert row["captured_gmv_paise"] == entry["captured_gmv"]
        assert row["conversion_rate"] == pytest.approx(
            entry["captured"] / entry["attempts"]
        )
        assert row["average_captured_order_value_paise"] == pytest.approx(
            entry["captured_gmv"] / entry["captured"]
        )

    # The breakdown stays consistent with the overall metrics block.
    assert (
        sum(row["attempts"] for row in segment_metrics) == payload["metrics"]["attempts"]
    )


def test_overview_includes_payment_method_metrics_reflecting_persisted_attempts(
    client, merchant, db_session
):
    seed_baseline(db_session)
    db_session.commit()

    payload = client.get(f"{API}/merchants/{merchant}/overview").json()

    method_metrics = payload["payment_method_metrics"]
    assert method_metrics
    for row in method_metrics:
        assert set(row) == set(schemas.PaymentMethodMetricsResponse.model_fields)

    # Canonical methods are present, in the engine's deterministic order.
    assert [row["payment_method"] for row in method_metrics] == CANONICAL_PAYMENT_METHODS

    for row in method_metrics:
        base = (
            db_session.query(PaymentAttempt)
            .filter_by(merchant_id=MERCHANT, payment_method=row["payment_method"])
        )
        attempts = base.count()
        captured = base.filter_by(status="captured").count()
        assert row["attempts"] == attempts
        assert row["captured"] == captured
        assert row["success_rate"] == pytest.approx(captured / attempts)

    assert (
        sum(row["attempts"] for row in method_metrics)
        == payload["metrics"]["attempts"]
    )


def test_overview_breakdowns_are_empty_without_attempts(client, merchant):
    payload = client.get(f"{API}/merchants/{merchant}/overview").json()

    assert payload["segment_metrics"] == []
    assert payload["payment_method_metrics"] == []


def test_overview_breakdowns_carry_no_causal_or_secret_shaped_fields(
    client, merchant, db_session
):
    seed_baseline(db_session)
    db_session.commit()

    body = client.get(f"{API}/merchants/{merchant}/overview").text.lower()

    for marker in FORBIDDEN_CAUSAL_MARKERS:
        assert marker not in body, marker
    for marker in FORBIDDEN_BODY_MARKERS:
        assert marker not in body, marker
    # No invented analysis on top of the observable counts.
    for invented in ("weakest", "trend", "at_risk", "recoverable", "lost"):
        assert invented not in body, invented


# ---------------------------------------------------------------------------
# Task 17A: composite Autopilot cycle read model
# ---------------------------------------------------------------------------


def test_cycle_missing_opportunity_is_404(client, merchant):
    for bad in ("nope", "not-a-uuid", "00000000-0000-4000-8000-000000000000"):
        response = client.get(f"{API}/opportunities/{bad}/cycle")
        assert response.status_code == 404, bad
        assert response.json()["detail"]["code"] == "NOT_FOUND"
        assert "Traceback" not in response.text


def test_cycle_opportunity_only_stage_has_every_later_stage_none(
    client, merchant, db_session
):
    seed_baseline(db_session)
    db_session.commit()
    opportunity_id = client.post(f"{API}/merchants/{merchant}/detect").json()[0]["id"]

    response = client.get(f"{API}/opportunities/{opportunity_id}/cycle")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == set(schemas.AutopilotCycleResponse.model_fields)
    assert set(payload["opportunity"]) == set(schemas.OpportunityResponse.model_fields)
    assert payload["opportunity"]["id"] == opportunity_id
    for stage in (
        "hypothesis",
        "experiment",
        "policy_decision",
        "razorpay_resource",
        "progress",
        "result",
    ):
        assert payload[stage] is None, stage
    # Safe policy limits are readable from the very first stage.
    assert payload["merchant_policy"]["merchant_id"] == MERCHANT
    # Before an experiment exists the audit trail is the opportunity's own
    # event, not another lifecycle's.
    assert [event["event_type"] for event in payload["audit_events"]] == [
        "OPPORTUNITY_DETECTED"
    ]
    assert all(event["entity_id"] == opportunity_id for event in payload["audit_events"])
    assert payload["audit_chain_valid"] is True


def test_cycle_opportunity_without_history_reports_an_empty_trail(
    client, merchant, db_session
):
    opportunity = make_opportunity(db_session, MERCHANT)
    db_session.commit()

    payload = client.get(f"{API}/opportunities/{opportunity.id}/cycle").json()

    assert payload["opportunity"]["id"] == opportunity.id
    assert payload["audit_events"] == []
    assert payload["audit_chain_valid"] is True


def test_cycle_diagnosed_stage_exposes_the_hypothesis(client, merchant, db_session):
    opportunity = make_opportunity(db_session, MERCHANT)
    hypothesis = make_hypothesis(db_session, opportunity)
    db_session.commit()

    payload = client.get(f"{API}/opportunities/{opportunity.id}/cycle").json()

    assert set(payload["hypothesis"]) == set(schemas.HypothesisResponse.model_fields)
    assert payload["hypothesis"]["id"] == hypothesis.id
    assert payload["hypothesis"]["opportunity_id"] == opportunity.id
    assert payload["hypothesis"]["intervention_type"] == "payment_method_config"
    for stage in (
        "experiment",
        "policy_decision",
        "razorpay_resource",
        "progress",
        "result",
    ):
        assert payload[stage] is None, stage


def test_cycle_diagnosed_over_http_filters_events_to_this_lifecycle(
    client, merchant, db_session
):
    seed_baseline(db_session)
    db_session.commit()
    opportunity_id = client.post(f"{API}/merchants/{merchant}/detect").json()[0]["id"]
    hypothesis = client.post(f"{API}/opportunities/{opportunity_id}/diagnose").json()

    payload = client.get(f"{API}/opportunities/{opportunity_id}/cycle").json()

    assert payload["hypothesis"]["id"] == hypothesis["id"]
    # The pre-experiment trail keeps the opportunity and hypothesis events
    # of *this* lifecycle, in chronological order.
    assert [event["event_type"] for event in payload["audit_events"]] == [
        "OPPORTUNITY_DETECTED",
        "AI_DIAGNOSIS_CREATED",
        "HYPOTHESIS_PROPOSED",
    ]


def test_cycle_planned_stage_exposes_the_experiment_and_progress(
    client, merchant, db_session
):
    opportunity = make_opportunity(db_session, MERCHANT)
    hypothesis = make_hypothesis(db_session, opportunity)
    experiment = make_experiment(db_session, hypothesis=hypothesis, status="proposed")
    db_session.commit()

    payload = client.get(f"{API}/opportunities/{opportunity.id}/cycle").json()

    assert set(payload["experiment"]) == set(schemas.ExperimentResponse.model_fields)
    assert payload["experiment"]["id"] == experiment.id
    assert payload["experiment"]["hypothesis_id"] == hypothesis.id
    assert payload["experiment"]["opportunity_id"] == opportunity.id
    assert payload["experiment"]["status"] == "proposed"
    assert payload["policy_decision"] is None
    assert payload["razorpay_resource"] is None
    assert payload["result"] is None
    # Progress exists as soon as the experiment exists, even at zero traffic.
    assert set(payload["progress"]) == set(schemas.ExperimentProgressResponse.model_fields)
    assert payload["progress"]["experiment_id"] == experiment.id
    assert payload["progress"]["control_attempts"] == 0
    assert payload["progress"]["treatment_attempts"] == 0
    assert payload["progress"]["sample_target_per_variant"] == (
        experiment.min_sample_per_variant
    )
    assert payload["progress"]["sample_target_reached"] is False


def test_cycle_policy_approved_stage_exposes_the_decision(client, merchant, db_session):
    experiment = make_experiment(db_session, status="approved")
    add_policy_decision(db_session, experiment, decision="APPROVE")
    db_session.commit()

    payload = client.get(f"{API}/opportunities/{experiment.opportunity_id}/cycle").json()

    assert set(payload["policy_decision"]) == set(
        schemas.PolicyDecisionResponse.model_fields
    )
    assert payload["policy_decision"]["experiment_id"] == experiment.id
    assert payload["policy_decision"]["merchant_id"] == MERCHANT
    assert payload["policy_decision"]["decision"] == "APPROVE"
    assert payload["policy_decision"]["violations"] == []
    assert payload["razorpay_resource"] is None


def test_cycle_policy_rejected_stage_exposes_reject_violations_and_limits(
    client, merchant, db_session
):
    experiment = make_experiment(
        db_session,
        intervention_type="offer_discount",
        treatment_config={"discount_pct": 0.40},
        status="proposed",
    )
    db_session.commit()

    rejected = client.post(f"{API}/experiments/{experiment.id}/policy")
    assert rejected.status_code == 200
    payload = client.get(f"{API}/opportunities/{experiment.opportunity_id}/cycle").json()

    assert payload["policy_decision"]["decision"] == "REJECT"
    assert "DISCOUNT_LIMIT_EXCEEDED" in payload["policy_decision"]["violations"]
    # A rejection can be shown as "proposed 40% against a configured maximum
    # of 15%" without inventing the maximum or parsing prose.
    assert payload["experiment"]["treatment_config"]["discount_pct"] == (
        pytest.approx(0.40)
    )
    assert payload["merchant_policy"]["max_discount_pct"] == pytest.approx(0.15)
    assert payload["razorpay_resource"] is None
    assert payload["result"] is None


def test_cycle_merchant_policy_is_public_and_safe(client, merchant, db_session):
    experiment = make_experiment(db_session, status="proposed")
    db_session.commit()

    payload = client.get(f"{API}/opportunities/{experiment.opportunity_id}/cycle").json()

    policy = payload["merchant_policy"]
    assert set(policy) == set(schemas.MerchantPolicyPublicResponse.model_fields)
    assert policy["merchant_id"] == MERCHANT
    assert policy["max_experiment_exposure_pct"] == pytest.approx(0.50)
    assert policy["max_discount_pct"] == pytest.approx(0.15)
    assert policy["min_margin_pct"] == pytest.approx(0.05)
    assert policy["max_concurrent_experiments"] == 3
    assert policy["max_experiment_duration_hours"] == 168
    assert policy["min_sample_size"] == 10
    assert policy["max_financial_exposure"] == 50_000
    assert isinstance(policy["allowed_interventions"], list)
    assert policy["allowed_interventions"] == list(ALL_INTERVENTIONS)
    # No internal row identity and no timestamps ride along.
    for internal in ("id", "created_at", "updated_at"):
        assert internal not in policy


@pytest.mark.parametrize(
    "malformed",
    ["payment_method_config", {"payment_method_config": 1}, 12, None],
)
def test_cycle_malformed_allowed_interventions_fail_closed(
    client, merchant, db_session, malformed
):
    experiment = make_experiment(db_session, status="proposed")
    policy = db_session.query(MerchantPolicy).filter_by(merchant_id=MERCHANT).one()
    policy.allowed_interventions = malformed
    db_session.commit()

    payload = client.get(f"{API}/opportunities/{experiment.opportunity_id}/cycle").json()

    assert payload["merchant_policy"]["allowed_interventions"] == []


def test_cycle_without_a_configured_policy_reports_none(client, db_session):
    db_session.add(Merchant(id="merchant_no_policy", name="No policy"))
    opportunity = make_opportunity(db_session, "merchant_no_policy")
    db_session.commit()

    payload = client.get(f"{API}/opportunities/{opportunity.id}/cycle").json()

    assert payload["opportunity"]["merchant_id"] == "merchant_no_policy"
    assert payload["merchant_policy"] is None


def test_cycle_deployed_stage_exposes_only_the_public_resource_identity(
    client, merchant, db_session
):
    experiment = make_experiment(db_session, status="approved")
    add_policy_decision(db_session, experiment, decision="APPROVE")
    db_session.commit()
    deployed = client.post(f"{API}/experiments/{experiment.id}/deploy")
    assert deployed.status_code == 200

    payload = client.get(f"{API}/opportunities/{experiment.opportunity_id}/cycle").json()

    resource = payload["razorpay_resource"]
    assert set(resource) == set(schemas.RazorpayResourceResponse.model_fields)
    assert resource["id"] == deployed.json()["id"]
    assert resource["experiment_id"] == experiment.id
    assert resource["variant"] == "treatment"
    assert resource["status"] == "active"
    # The public Razorpay id is the only external identity exposed...
    assert resource["razorpay_id"].startswith("plink_")
    # ...and no raw config, credential or API response shape rides along.
    assert "config" not in resource
    body = json.dumps(payload)
    for marker in FORBIDDEN_BODY_MARKERS:
        assert marker not in body, marker


def test_cycle_running_stage_exposes_progress(client, merchant, db_session):
    experiment = make_experiment(db_session, status="running", min_sample=50)
    add_policy_decision(db_session, experiment, decision="APPROVE")
    add_resource(db_session, experiment)
    add_attempts(db_session, experiment, variant="control", count=10, captured=5)
    add_attempts(db_session, experiment, variant="treatment", count=4, captured=2)
    db_session.commit()

    payload = client.get(f"{API}/opportunities/{experiment.opportunity_id}/cycle").json()

    progress = payload["progress"]
    assert set(progress) == set(schemas.ExperimentProgressResponse.model_fields)
    assert progress["experiment_id"] == experiment.id
    assert progress["control_attempts"] == 10
    assert progress["treatment_attempts"] == 4
    assert progress["sample_target_per_variant"] == 50
    assert progress["control_remaining"] == 40
    assert progress["treatment_remaining"] == 46
    assert progress["sample_target_reached"] is False
    assert payload["result"] is None
    assert payload["razorpay_resource"]["experiment_id"] == experiment.id


def test_cycle_completed_stage_exposes_the_persisted_statistical_result(
    client, merchant, db_session
):
    experiment = make_experiment(db_session, status="running", min_sample=10)
    add_policy_decision(db_session, experiment, decision="APPROVE")
    add_resource(db_session, experiment)
    add_attempts(db_session, experiment, variant="control", count=10, captured=3)
    add_attempts(db_session, experiment, variant="treatment", count=10, captured=8)
    db_session.commit()
    evaluated = client.post(f"{API}/experiments/{experiment.id}/evaluate")
    assert evaluated.status_code == 200
    decision = evaluated.json()

    payload = client.get(f"{API}/opportunities/{experiment.opportunity_id}/cycle").json()

    result = payload["result"]
    assert set(result) == set(schemas.ExperimentResultResponse.model_fields)
    assert result["experiment_id"] == experiment.id
    assert result["decision"] == decision["decision"] == "KEEP"
    assert result["control_rate"] == pytest.approx(0.3)
    assert result["treatment_rate"] == pytest.approx(0.8)
    assert result["absolute_lift"] == pytest.approx(0.5)
    assert result["p_value"] == decision["p_value"]
    assert result["confidence_interval_lower"] is not None
    assert result["confidence_interval_upper"] is not None
    assert result["confidence_interval_lower"] < result["confidence_interval_upper"]
    assert payload["progress"]["sample_target_reached"] is True


def test_cycle_includes_the_experiment_audit_trail_and_chain_validity(
    client, merchant, db_session, small_horizon
):
    seed_baseline(db_session)
    db_session.commit()
    opportunity_id = client.post(f"{API}/merchants/{merchant}/detect").json()[0]["id"]
    hypothesis_id = client.post(
        f"{API}/opportunities/{opportunity_id}/diagnose"
    ).json()["id"]
    experiment_id = client.post(f"{API}/hypotheses/{hypothesis_id}/plan").json()["id"]

    payload = client.get(f"{API}/opportunities/{opportunity_id}/cycle").json()

    # Once the experiment exists its own audit history is the trail, exactly
    # what the dedicated endpoint reports.
    assert payload["audit_chain_valid"] is True
    events = payload["audit_events"]
    assert events
    assert [event["event_type"] for event in events] == ["EXPERIMENT_PLANNED"]
    assert all(event["entity_id"] == experiment_id for event in events)
    dedicated = client.get(f"{API}/experiments/{experiment_id}/audit").json()
    assert [event["id"] for event in events] == [event["id"] for event in dedicated]
    for event in events:
        assert set(event) == set(schemas.AuditEventResponse.model_fields)


def test_cycle_get_never_commits(client, merchant, db_session, monkeypatch):
    experiment = make_experiment(db_session, status="running", min_sample=10)
    add_policy_decision(db_session, experiment, decision="APPROVE")
    add_resource(db_session, experiment)
    add_attempts(db_session, experiment, variant="control", count=3, captured=1)
    db_session.commit()

    def forbidden(*_args, **_kwargs):
        raise AssertionError("GET routes must not commit")

    monkeypatch.setattr(db_session, "commit", forbidden, raising=False)
    assert (
        client.get(f"{API}/opportunities/{experiment.opportunity_id}/cycle").status_code
        == 200
    )
    assert client.get(f"{API}/merchants/{merchant}/overview").status_code == 200


def test_cycle_get_mutates_no_lifecycle_state(client, merchant, db_session, committed_view):
    experiment = deployable_experiment(db_session, min_sample=50)
    add_attempts(db_session, experiment, variant="control", count=5, captured=2)
    add_attempts(db_session, experiment, variant="treatment", count=5, captured=2)
    db_session.commit()
    committed_view.expire_all()
    tables = (
        Opportunity,
        Hypothesis,
        Experiment,
        PolicyDecision,
        RazorpayResource,
        PaymentAttempt,
        ExperimentResult,
        AuditEvent,
        OperationExecution,
    )
    before = {table: committed_view.query(table).count() for table in tables}

    response = client.get(f"{API}/opportunities/{experiment.opportunity_id}/cycle")

    assert response.status_code == 200
    committed_view.expire_all()
    assert {table: committed_view.query(table).count() for table in tables} == before
    assert committed_view.get(Experiment, experiment.id).status == "approved"
    assert (
        committed_view.query(RazorpayResource)
        .filter_by(experiment_id=experiment.id)
        .one()
        .status
        == "active"
    )
    assert len(db_session.new) == 0 and len(db_session.dirty) == 0


def test_cycle_reconstructs_a_full_lifecycle_after_a_browser_refresh(
    client, merchant, db_session, small_horizon
):
    seed_baseline(db_session)
    db_session.commit()
    opportunity_id = client.post(f"{API}/merchants/{merchant}/detect").json()[0]["id"]
    hypothesis_id = client.post(
        f"{API}/opportunities/{opportunity_id}/diagnose"
    ).json()["id"]
    experiment_id = client.post(f"{API}/hypotheses/{hypothesis_id}/plan").json()["id"]
    client.post(f"{API}/experiments/{experiment_id}/policy")
    resource = client.post(f"{API}/experiments/{experiment_id}/deploy").json()
    for _ in range(40):
        summary = client.post(
            f"{API}/experiments/{experiment_id}/run",
            json={"batch_size": 500, "seed": 20260827},
        ).json()
        if summary["control_remaining"] == 0 and summary["treatment_remaining"] == 0:
            break
    result = client.post(f"{API}/experiments/{experiment_id}/evaluate").json()

    # A browser refresh serves the next request on a brand-new session with an
    # empty identity map; the one cycle response must rebuild everything.
    fresh = sessionmaker(
        bind=db_session.get_bind(), autocommit=False, autoflush=False
    )()
    app.dependency_overrides[get_db] = lambda: fresh
    try:
        refreshed = client.get(f"{API}/opportunities/{opportunity_id}/cycle")
    finally:
        app.dependency_overrides[get_db] = lambda: db_session
        fresh.close()

    assert refreshed.status_code == 200
    payload = refreshed.json()
    assert payload["opportunity"]["id"] == opportunity_id
    assert payload["hypothesis"]["id"] == hypothesis_id
    assert payload["experiment"]["id"] == experiment_id
    assert payload["policy_decision"]["experiment_id"] == experiment_id
    assert payload["merchant_policy"]["merchant_id"] == MERCHANT
    assert payload["razorpay_resource"]["razorpay_id"] == resource["razorpay_id"]
    assert payload["progress"]["experiment_id"] == experiment_id
    assert payload["progress"]["sample_target_reached"] is True
    assert payload["progress"]["control_attempts"] == summary["control_attempts"]
    assert payload["result"]["decision"] == result["decision"]
    assert payload["result"]["p_value"] == result["p_value"]
    assert payload["audit_chain_valid"] is True
    assert payload["audit_events"]
    assert all(event["entity_id"] == experiment_id for event in payload["audit_events"])
    assert [event["event_type"] for event in payload["audit_events"]] == [
        "EXPERIMENT_PLANNED",
        "POLICY_APPROVED",
        "RAZORPAY_RESOURCE_CREATED",
        "EXPERIMENT_STARTED",
        "EXPERIMENT_COMPLETED",
    ]
    # Nothing causal, nothing secret-shaped in the refreshed payload.
    body = json.dumps(payload)
    for marker in FORBIDDEN_BODY_MARKERS:
        assert marker not in body, marker
    lowered = body.lower()
    for marker in FORBIDDEN_CAUSAL_MARKERS:
        assert marker not in lowered, marker


def test_cycle_stays_isolated_per_merchant(client, db_session):
    make_merchant(db_session, MERCHANT)
    make_merchant(db_session, OTHER_MERCHANT)
    mine = make_opportunity(db_session, MERCHANT, severity=0.5)
    my_experiment = make_experiment(db_session, opportunity=mine, status="proposed")
    theirs = make_opportunity(db_session, OTHER_MERCHANT, severity=0.9)
    their_hypothesis = make_hypothesis(db_session, theirs)
    their_experiment = make_experiment(
        db_session, hypothesis=their_hypothesis, status="running", min_sample=10
    )
    add_policy_decision(db_session, their_experiment, decision="APPROVE")
    add_resource(db_session, their_experiment)
    add_attempts(db_session, their_experiment, variant="control", count=3, captured=1)
    db_session.commit()

    my_cycle = client.get(f"{API}/opportunities/{mine.id}/cycle").json()
    their_cycle = client.get(f"{API}/opportunities/{theirs.id}/cycle").json()

    assert my_cycle["opportunity"]["merchant_id"] == MERCHANT
    assert my_cycle["merchant_policy"]["merchant_id"] == MERCHANT
    assert my_cycle["experiment"]["id"] == my_experiment.id
    assert my_cycle["progress"]["experiment_id"] == my_experiment.id
    assert my_cycle["policy_decision"] is None
    assert my_cycle["razorpay_resource"] is None

    assert their_cycle["opportunity"]["merchant_id"] == OTHER_MERCHANT
    assert their_cycle["merchant_policy"]["merchant_id"] == OTHER_MERCHANT
    assert their_cycle["experiment"]["id"] == their_experiment.id
    assert their_cycle["policy_decision"]["experiment_id"] == their_experiment.id
    assert their_cycle["razorpay_resource"]["experiment_id"] == their_experiment.id
    assert their_cycle["progress"]["control_attempts"] == 3

    # Neither cycle echoes the other merchant's entities.
    assert their_experiment.id not in json.dumps(my_cycle)
    assert theirs.id not in json.dumps(my_cycle)
    assert my_experiment.id not in json.dumps(their_cycle)
    assert mine.id not in json.dumps(their_cycle)

    # Unknown identifiers never resolve into somebody's lifecycle.
    for bad in ("nope", "00000000-0000-4000-8000-000000000000"):
        assert client.get(f"{API}/opportunities/{bad}/cycle").status_code == 404


def test_readiness_read_models_still_forbid_unknown_fields(
    client, merchant, db_session
):
    seed_baseline(db_session)
    experiment = make_experiment(db_session, status="proposed")
    db_session.commit()
    cycle = client.get(f"{API}/opportunities/{experiment.opportunity_id}/cycle").json()
    overview_payload = client.get(f"{API}/merchants/{merchant}/overview").json()

    for model, payload in (
        (schemas.AutopilotCycleResponse, {**cycle, "expected_lift": 0.4}),
        (
            schemas.MerchantOverviewResponse,
            {**overview_payload, "revenue_at_risk_paise": 1},
        ),
        (
            schemas.SegmentMetricsResponse,
            {**overview_payload["segment_metrics"][0], "weakest": True},
        ),
        (
            schemas.PaymentMethodMetricsResponse,
            {**overview_payload["payment_method_metrics"][0], "trend": "down"},
        ),
        (
            schemas.MerchantPolicyPublicResponse,
            {**cycle["merchant_policy"], "internal_id": "x"},
        ),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(payload)

    for model in (
        schemas.SegmentMetricsResponse,
        schemas.PaymentMethodMetricsResponse,
        schemas.MerchantPolicyPublicResponse,
        schemas.AutopilotCycleResponse,
    ):
        assert model.model_config.get("extra") == "forbid"
        assert model.model_config.get("from_attributes") is True
