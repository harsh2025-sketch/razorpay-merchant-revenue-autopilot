"""Tests for Task 08: structured AI diagnosis + hypothesis layer.

All tests are fully offline. The OpenAI network boundary
(``engines.diagnosis._request_proposal``) is exercised through an injected
fake client that mimics ``client.chat.completions.parse`` structured-output
responses. No real OpenAI call is ever made, and no API key is required.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.db.base import Base
from app.db.models import (
    Hypothesis,
    Merchant,
    MerchantPolicy,
    Opportunity,
    PaymentAttempt,
)
from app.engines.diagnosis import (
    DiagnosisConfigurationError,
    DiagnosisError,
    DiagnosisOutputInvalidError,
    _resolve_allowed_interventions,
    build_evidence_catalog,
    diagnose_opportunity,
    persist_hypothesis,
    validate_proposal,
)
from app.engines.opportunities import run_opportunity_detection
from app.schemas.hypothesis import (
    INTERVENTION_TYPE_SET,
    INTERVENTION_TYPES,
    HypothesisProposal,
)
from app.simulation.generator import generate_baseline_events
from app.simulation.merchant import TECHBAZAAR_PROFILE

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = BACKEND_DIR / "app" / "schemas" / "hypothesis.py"
ENGINE_PATH = BACKEND_DIR / "app" / "engines" / "diagnosis.py"

ALL_INTERVENTIONS = [
    "payment_method_config",
    "offer_discount",
    "partial_payment",
    "expiry_config",
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_db_session(tmp_path):
    db_file = tmp_path / "test_diagnosis.db"
    db_url = f"sqlite:///{db_file}"
    engine = create_engine(db_url, connect_args={"check_same_thread": False})

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
def clean_settings(monkeypatch):
    """Isolate the cached settings from any ambient OPENAI_* env vars."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Fake OpenAI client (structured-output stub)
# ---------------------------------------------------------------------------


MOCK_MODEL_RESPONSE = {
    "diagnosis": (
        "Payment completion is materially weaker in this segment than the "
        "comparison population, with variation across payment methods."
    ),
    "hypothesis_text": (
        "Changing the available checkout payment methods may improve "
        "completion for this segment."
    ),
    "intervention_type": "payment_method_config",
    "intervention_params": {"card": False, "upi": True},
    "confidence": "medium",
    "reasoning_summary": (
        "The segment conversion gap and payment-method success-rate "
        "differences justify a controlled checkout-method experiment."
    ),
    "evidence_refs": [
        "segment_conversion_rate",
        "comparison_conversion_rate",
        "payment_method.card.success_rate",
        "payment_method.upi.success_rate",
    ],
}


class FakeCompletions:
    """Mimics ``openai.OpenAI().chat.completions.parse``."""

    def __init__(self, payload: dict | None, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.calls: list[dict] = []

    def parse(self, *, messages, model, response_format, **kwargs):
        self.calls.append(
            {
                "messages": messages,
                "model": model,
                "response_format": response_format,
                "kwargs": kwargs,
            }
        )
        if self.error is not None:
            raise self.error
        parsed = (
            response_format.model_validate(self.payload)
            if self.payload is not None
            else None
        )
        message = SimpleNamespace(parsed=parsed)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])


class FakeOpenAIClient:
    """Drop-in OpenAI client stub for offline diagnosis tests."""

    def __init__(self, payload: dict | None = None, error: Exception | None = None):
        self.completions = FakeCompletions(payload, error)
        self.chat = SimpleNamespace(completions=self.completions)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_evidence() -> dict:
    """Observable evidence shaped like Task 07 detector output."""
    return {
        "segment": "android_budget",
        "segment_attempts": 200,
        "segment_captured": 94,
        "segment_conversion_rate": 0.472,
        "comparison_attempts": 800,
        "comparison_captured": 469,
        "comparison_conversion_rate": 0.586,
        "absolute_gap": 0.114,
        "payment_method_metrics": {
            "upi": {
                "attempts": 90,
                "captured": 46,
                "failed": 30,
                "abandoned": 14,
                "success_rate": 0.51,
            },
            "card": {
                "attempts": 70,
                "captured": 27,
                "failed": 28,
                "abandoned": 15,
                "success_rate": 0.39,
            },
            "netbanking": {
                "attempts": 25,
                "captured": 13,
                "failed": 8,
                "abandoned": 4,
                "success_rate": 0.52,
            },
            "wallet": {
                "attempts": 15,
                "captured": 8,
                "failed": 4,
                "abandoned": 3,
                "success_rate": 0.53,
            },
        },
        "failure_reasons": {
            "bank_declined": 140,
            "authentication_failed": 38,
            "network_error": 20,
        },
    }


def create_merchant(db, merchant_id: str, *, with_policy: bool = True, **policy_kwargs) -> Merchant:
    merchant = Merchant(id=merchant_id, name=f"Merchant {merchant_id}")
    db.add(merchant)
    if with_policy:
        defaults = {
            "max_experiment_exposure_pct": 0.10,
            "max_discount_pct": 0.15,
            "min_margin_pct": 0.05,
            "max_concurrent_experiments": 3,
            "max_experiment_duration_hours": 168,
            "min_sample_size": 30,
            "max_financial_exposure": 50000,
            "allowed_interventions": list(ALL_INTERVENTIONS),
        }
        defaults.update(policy_kwargs)
        db.add(
            MerchantPolicy(
                id=f"policy_{merchant_id}",
                merchant_id=merchant_id,
                **defaults,
            )
        )
    db.flush()
    return merchant


def make_opportunity(
    db,
    merchant_id: str,
    *,
    segment: str = "android_budget",
    evidence: dict | None = None,
    status: str = "detected",
) -> Opportunity:
    opp = Opportunity(
        merchant_id=merchant_id,
        type="segment_conversion_divergence",
        segment=segment,
        severity=0.07,
        detected_metric="conversion_rate",
        detected_value=0.472,
        baseline_value=0.586,
        evidence=evidence if evidence is not None else make_evidence(),
        status=status,
    )
    db.add(opp)
    db.flush()
    return opp


def make_proposal(**overrides) -> HypothesisProposal:
    payload = {
        "diagnosis": "Checkout completion is weaker in this segment.",
        "hypothesis_text": "Adjusting available payment methods may improve completion.",
        "intervention_type": "payment_method_config",
        "intervention_params": {"card": False, "upi": True},
        "confidence": "medium",
        "reasoning_summary": (
            "The conversion gap and payment-method success-rate differences "
            "justify a controlled experiment."
        ),
        "evidence_refs": [
            "segment_conversion_rate",
            "comparison_conversion_rate",
            "payment_method.card.success_rate",
        ],
    }
    payload.update(overrides)
    return HypothesisProposal.model_validate(payload)


# ===========================================================================
# 1-3. Evidence catalog
# ===========================================================================


def _catalog_for(db, merchant_id="m_catalog") -> dict:
    merchant = create_merchant(db, merchant_id, with_policy=False)
    opp = make_opportunity(db, merchant.id)
    return build_evidence_catalog(opp), opp


def test_evidence_catalog_is_deterministic(temp_db_session):
    db = temp_db_session
    catalog1, _ = _catalog_for(db, "m_det1")
    catalog2, _ = _catalog_for(db, "m_det2")

    assert list(catalog1.keys()) == list(catalog2.keys())
    assert catalog1 == catalog2


def test_evidence_catalog_deterministic_regardless_of_input_order(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(db, "m_order", with_policy=False)

    evidence = make_evidence()
    opp = make_opportunity(db, merchant.id, evidence=evidence)
    catalog_a = build_evidence_catalog(opp)

    # Same values, different insertion orders in nested dicts.
    shuffled = dict(reversed(list(evidence.items())))
    shuffled["payment_method_metrics"] = dict(
        reversed(list(evidence["payment_method_metrics"].items()))
    )
    shuffled["failure_reasons"] = dict(reversed(list(evidence["failure_reasons"].items())))
    opp_b = make_opportunity(db, merchant.id, segment="ios_premium", evidence=shuffled)
    catalog_b = build_evidence_catalog(opp_b)

    assert list(catalog_a.keys()) == list(catalog_b.keys())
    assert catalog_a == catalog_b


def test_catalog_contains_segment_comparison_gap_and_method_stats(temp_db_session):
    db = temp_db_session
    catalog, _ = _catalog_for(db, "m_keys")

    # Segment rate, comparison rate, absolute gap.
    assert catalog["segment_conversion_rate"] == pytest.approx(0.472)
    assert catalog["comparison_conversion_rate"] == pytest.approx(0.586)
    assert catalog["absolute_gap"] == pytest.approx(0.114)
    assert catalog["segment_attempts"] == 200
    assert catalog["comparison_attempts"] == 800

    # Payment method stats.
    assert catalog["payment_method.upi.success_rate"] == pytest.approx(0.51)
    assert catalog["payment_method.card.success_rate"] == pytest.approx(0.39)
    assert catalog["payment_method.upi.attempts"] == 90
    assert catalog["payment_method.card.captured"] == 27

    # Failure reasons.
    assert catalog["failure_reason.bank_declined"] == 140
    assert catalog["failure_reason.authentication_failed"] == 38
    assert catalog["failure_reason.network_error"] == 20


def test_catalog_derives_absolute_gap_when_missing(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(db, "m_gap", with_policy=False)
    evidence = make_evidence()
    del evidence["absolute_gap"]
    opp = make_opportunity(db, merchant.id, evidence=evidence)
    catalog = build_evidence_catalog(opp)
    assert catalog["absolute_gap"] == pytest.approx(0.586 - 0.472)


def test_catalog_contains_no_hidden_or_causal_keys(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(db, "m_hidden", with_policy=False)
    evidence = make_evidence()
    # Even if hidden keys somehow leaked into the evidence JSON, they must
    # never reach the model-visible catalog.
    evidence["causal_true_uplift"] = 0.25
    evidence["hidden_effect"] = 0.4
    evidence["simulator_truth"] = {"upi": 0.9}
    evidence["true_intervention_effect"] = 0.11
    opp = make_opportunity(db, merchant.id, evidence=evidence)
    catalog = build_evidence_catalog(opp)

    banned = ("causal", "hidden", "simulator", "truth", "true_", "effect")
    for key in catalog:
        assert not any(term in key.lower() for term in banned), key
    assert "causal_true_uplift" not in catalog
    assert "hidden_effect" not in catalog
    assert "simulator_truth" not in catalog
    assert "true_intervention_effect" not in catalog


# ===========================================================================
# 4-20. Proposal validation (pure, no OpenAI, no DB)
# ===========================================================================

DEFAULT_CATALOG = {
    "segment_conversion_rate": 0.472,
    "comparison_conversion_rate": 0.586,
    "absolute_gap": 0.114,
    "segment_attempts": 200,
    "comparison_attempts": 800,
    "payment_method.upi.success_rate": 0.51,
    "payment_method.card.success_rate": 0.39,
    "payment_method.netbanking.success_rate": 0.52,
    "failure_reason.bank_declined": 140,
}


def _validate(proposal: HypothesisProposal, allowed=None):
    return validate_proposal(
        proposal,
        DEFAULT_CATALOG,
        set(allowed) if allowed is not None else set(INTERVENTION_TYPE_SET),
    )


def test_valid_payment_method_config_proposal_passes():
    proposal = make_proposal(
        intervention_params={"card": False, "upi": True, "netbanking": True, "wallet": False}
    )
    result = _validate(proposal)
    assert result.intervention_params == {
        "card": False,
        "upi": True,
        "netbanking": True,
        "wallet": False,
    }


def test_unknown_payment_method_key_rejected():
    for params in (
        {"paypal": True},
        {"options": {"upi": True}},
        {"checkout": {"order": {"amount": 100000}}},
        {"card": True, "razorpay_payload": {}},
    ):
        with pytest.raises(DiagnosisOutputInvalidError, match="unsupported"):
            _validate(make_proposal(intervention_params=params))


def test_non_boolean_payment_method_value_rejected():
    for bad_value in ("yes", 1, 0, None, [True]):
        with pytest.raises(DiagnosisOutputInvalidError, match="boolean"):
            _validate(make_proposal(intervention_params={"upi": bad_value}))


def test_empty_intervention_params_rejected():
    with pytest.raises(DiagnosisOutputInvalidError, match="non-empty"):
        _validate(make_proposal(intervention_params={}))


def test_valid_offer_discount_proposal_passes():
    proposal = make_proposal(
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.10},
    )
    result = _validate(proposal)
    assert result.intervention_params == {"discount_pct": 0.10}


def test_discount_zero_or_negative_rejected():
    for bad in (0, -0.05, 0.0):
        with pytest.raises(DiagnosisOutputInvalidError, match="discount_pct"):
            _validate(
                make_proposal(
                    intervention_type="offer_discount",
                    intervention_params={"discount_pct": bad},
                )
            )


def test_discount_above_half_rejected():
    for bad in (0.51, 0.6, 1.0):
        with pytest.raises(DiagnosisOutputInvalidError, match="discount_pct"):
            _validate(
                make_proposal(
                    intervention_type="offer_discount",
                    intervention_params={"discount_pct": bad},
                )
            )


def test_discount_non_numeric_rejected():
    for bad in ("10", True, None):
        with pytest.raises(DiagnosisOutputInvalidError, match="number"):
            _validate(
                make_proposal(
                    intervention_type="offer_discount",
                    intervention_params={"discount_pct": bad},
                )
            )


def test_twenty_percent_discount_is_schema_valid():
    """20% is above the typical merchant policy cap (15%) but Task 08 must
    NOT apply merchant policy - unsafe-but-well-formed proposals pass."""
    proposal = make_proposal(
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.20},
    )
    result = _validate(proposal)
    assert result.intervention_params["discount_pct"] == pytest.approx(0.20)


def test_twenty_percent_discount_passes_even_with_policy_row_present(temp_db_session):
    """End-to-end proof that MerchantPolicy.max_discount_pct is not consulted."""
    db = temp_db_session
    merchant = create_merchant(db, "m_policy20", max_discount_pct=0.15)
    opp = make_opportunity(db, merchant.id)

    client = FakeOpenAIClient(
        {
            **MOCK_MODEL_RESPONSE,
            "intervention_type": "offer_discount",
            "intervention_params": {"discount_pct": 0.20},
            "evidence_refs": ["segment_conversion_rate", "absolute_gap"],
        }
    )
    hypothesis = diagnose_opportunity(db, opp.id, client=client)
    assert hypothesis.intervention_params["discount_pct"] == pytest.approx(0.20)
    assert hypothesis.status == "proposed"


def test_valid_partial_payment_proposal_passes():
    proposal = make_proposal(
        intervention_type="partial_payment",
        intervention_params={"accept_partial": True, "first_min_partial_amount_pct": 0.3},
    )
    result = _validate(proposal)
    assert result.intervention_params["accept_partial"] is True


def test_partial_pct_without_accept_partial_true_rejected():
    # accept_partial missing entirely
    with pytest.raises(DiagnosisOutputInvalidError, match="accept_partial"):
        _validate(
            make_proposal(
                intervention_type="partial_payment",
                intervention_params={"first_min_partial_amount_pct": 0.3},
            )
        )
    # accept_partial explicitly False
    with pytest.raises(DiagnosisOutputInvalidError, match="accept_partial"):
        _validate(
            make_proposal(
                intervention_type="partial_payment",
                intervention_params={
                    "accept_partial": False,
                    "first_min_partial_amount_pct": 0.3,
                },
            )
        )


def test_partial_pct_above_one_rejected():
    with pytest.raises(DiagnosisOutputInvalidError, match="first_min_partial_amount_pct"):
        _validate(
            make_proposal(
                intervention_type="partial_payment",
                intervention_params={
                    "accept_partial": True,
                    "first_min_partial_amount_pct": 1.5,
                },
            )
        )


def test_partial_absolute_rupee_amount_rejected():
    with pytest.raises(DiagnosisOutputInvalidError, match="unsupported"):
        _validate(
            make_proposal(
                intervention_type="partial_payment",
                intervention_params={
                    "accept_partial": True,
                    "first_min_partial_amount_paise": 50000,
                },
            )
        )


def test_valid_expiry_config_proposal_passes():
    proposal = make_proposal(
        intervention_type="expiry_config",
        intervention_params={"expiry_hours": 48},
    )
    result = _validate(proposal)
    assert result.intervention_params == {"expiry_hours": 48}


def test_expiry_hours_nonpositive_rejected():
    for bad in (0, -6):
        with pytest.raises(DiagnosisOutputInvalidError, match="expiry_hours"):
            _validate(
                make_proposal(
                    intervention_type="expiry_config",
                    intervention_params={"expiry_hours": bad},
                )
            )


def test_expiry_above_180_days_rejected():
    for bad in (4321, 24 * 181, 100000):
        with pytest.raises(DiagnosisOutputInvalidError, match="expiry_hours"):
            _validate(
                make_proposal(
                    intervention_type="expiry_config",
                    intervention_params={"expiry_hours": bad},
                )
            )


def test_unknown_intervention_type_rejected_by_schema():
    with pytest.raises(PydanticValidationError, match="intervention_type"):
        HypothesisProposal.model_validate(
            {**MOCK_MODEL_RESPONSE, "intervention_type": "send_email"}
        )


def test_intervention_type_outside_allowed_set_rejected():
    """Allowed set may be narrower than the global universe (merchant policy)."""
    proposal = make_proposal()  # payment_method_config
    with pytest.raises(DiagnosisOutputInvalidError, match="not in the allowed"):
        _validate(proposal, allowed=["offer_discount", "expiry_config"])


def test_unknown_evidence_ref_rejected():
    with pytest.raises(DiagnosisOutputInvalidError, match="not present"):
        _validate(
            make_proposal(
                evidence_refs=["segment_conversion_rate", "payment_method.crypto.success_rate"]
            )
        )


def test_empty_evidence_refs_rejected():
    with pytest.raises(PydanticValidationError):
        make_proposal(evidence_refs=[])
    # Even bypassing pydantic, deterministic validation must reject it.
    raw = HypothesisProposal.model_construct(**{**MOCK_MODEL_RESPONSE, "evidence_refs": []})
    with pytest.raises(DiagnosisOutputInvalidError, match="at least one"):
        _validate(raw)


def test_proposal_cannot_reference_invented_metric():
    for invented in (
        "segment_true_uplift",
        "invented_metric_xyz",
        "overall_conversion_rate",  # plausible-sounding but not in catalog
        "payment_method.upi.failure_rate",  # not a catalog key
    ):
        with pytest.raises(DiagnosisOutputInvalidError, match="not present"):
            _validate(make_proposal(evidence_refs=[invented]))


def test_missing_required_fields_rejected_by_schema():
    for field in (
        "diagnosis",
        "hypothesis_text",
        "intervention_type",
        "intervention_params",
        "confidence",
        "reasoning_summary",
        "evidence_refs",
    ):
        payload = {k: v for k, v in MOCK_MODEL_RESPONSE.items() if k != field}
        with pytest.raises(PydanticValidationError):
            HypothesisProposal.model_validate(payload)


def test_invalid_confidence_rejected():
    with pytest.raises(PydanticValidationError):
        HypothesisProposal.model_validate({**MOCK_MODEL_RESPONSE, "confidence": "certain"})
    raw = HypothesisProposal.model_construct(**{**MOCK_MODEL_RESPONSE, "confidence": "certain"})
    with pytest.raises(DiagnosisOutputInvalidError, match="confidence"):
        _validate(raw)


def test_empty_diagnosis_hypothesis_reasoning_rejected():
    for field in ("diagnosis", "hypothesis_text", "reasoning_summary"):
        raw = HypothesisProposal.model_construct(**{**MOCK_MODEL_RESPONSE, field: "   "})
        with pytest.raises(DiagnosisOutputInvalidError, match=field):
            _validate(raw)


def test_oversized_reasoning_summary_rejected():
    long_summary = "x" * 601
    raw = HypothesisProposal.model_construct(
        **{**MOCK_MODEL_RESPONSE, "reasoning_summary": long_summary}
    )
    with pytest.raises(DiagnosisOutputInvalidError, match="reasoning_summary"):
        _validate(raw)


def test_validation_returns_normalized_proposal():
    proposal = make_proposal(
        diagnosis="  Completion gap in segment.  ",
        reasoning_summary="  Evidence supports an experiment.  ",
    )
    result = _validate(proposal)
    assert result.diagnosis == "Completion gap in segment."
    assert result.reasoning_summary == "Evidence supports an experiment."


# ===========================================================================
# 21-31. Persistence via diagnose_opportunity (fake client, no API key)
# ===========================================================================


def test_diagnose_persists_hypothesis_correctly(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(db, "m_persist")
    opp = make_opportunity(db, merchant.id)

    hypothesis = diagnose_opportunity(db, opp.id, client=FakeOpenAIClient(MOCK_MODEL_RESPONSE))

    assert hypothesis.id is not None
    assert hypothesis.opportunity_id == opp.id
    assert hypothesis.merchant_id == merchant.id
    assert hypothesis.hypothesis_text == MOCK_MODEL_RESPONSE["hypothesis_text"]
    assert hypothesis.intervention_type == "payment_method_config"
    assert hypothesis.intervention_params == {"card": False, "upi": True}
    assert hypothesis.confidence == "medium"
    assert hypothesis.status == "proposed"

    # Flush, not commit: row is visible in the same transaction but the
    # caller still controls the commit.
    assert db.query(Hypothesis).filter(Hypothesis.id == hypothesis.id).count() == 1


def test_ai_model_stored(temp_db_session, monkeypatch):
    db = temp_db_session
    merchant = create_merchant(db, "m_model")
    opp = make_opportunity(db, merchant.id)

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "test-model-xyz")
    get_settings.cache_clear()
    try:
        client = FakeOpenAIClient(MOCK_MODEL_RESPONSE)
        hypothesis = diagnose_opportunity(db, opp.id, client=client)
        assert hypothesis.ai_model == "test-model-xyz"
        # The configured model is also what the request used.
        assert client.completions.calls[0]["model"] == "test-model-xyz"
    finally:
        get_settings.cache_clear()


def test_openai_model_has_sensible_default(clean_settings):
    settings = get_settings()
    assert isinstance(settings.OPENAI_MODEL, str)
    assert settings.OPENAI_MODEL.strip() != ""


def test_evidence_refs_stored(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(db, "m_refs")
    opp = make_opportunity(db, merchant.id)

    hypothesis = diagnose_opportunity(db, opp.id, client=FakeOpenAIClient(MOCK_MODEL_RESPONSE))

    assert hypothesis.evidence_refs == MOCK_MODEL_RESPONSE["evidence_refs"]
    catalog = build_evidence_catalog(opp)
    assert set(hypothesis.evidence_refs) <= set(catalog)


def test_reasoning_summary_contains_concise_diagnosis_and_rationale(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(db, "m_reason")
    opp = make_opportunity(db, merchant.id)

    hypothesis = diagnose_opportunity(db, opp.id, client=FakeOpenAIClient(MOCK_MODEL_RESPONSE))

    assert hypothesis.reasoning_summary.startswith("Diagnosis: ")
    assert "| Rationale: " in hypothesis.reasoning_summary
    assert MOCK_MODEL_RESPONSE["diagnosis"] in hypothesis.reasoning_summary
    assert MOCK_MODEL_RESPONSE["reasoning_summary"] in hypothesis.reasoning_summary
    assert len(hypothesis.reasoning_summary) <= 900


def test_invalid_proposal_does_not_persist_row(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(db, "m_invalid")
    opp = make_opportunity(db, merchant.id)

    bad_payload = {
        **MOCK_MODEL_RESPONSE,
        "intervention_params": {"upi": "true"},  # non-boolean
    }
    with pytest.raises(DiagnosisOutputInvalidError):
        diagnose_opportunity(db, opp.id, client=FakeOpenAIClient(bad_payload))
    assert db.query(Hypothesis).count() == 0

    bad_ref_payload = {
        **MOCK_MODEL_RESPONSE,
        "evidence_refs": ["segment_conversion_rate", "made_up_metric"],
    }
    with pytest.raises(DiagnosisOutputInvalidError):
        diagnose_opportunity(db, opp.id, client=FakeOpenAIClient(bad_ref_payload))
    assert db.query(Hypothesis).count() == 0


def test_structured_output_validated_before_persistence(temp_db_session):
    """A schema-parseable but semantically invalid response is rejected and
    nothing is persisted."""
    db = temp_db_session
    merchant = create_merchant(db, "m_schema_valid")
    opp = make_opportunity(db, merchant.id)

    payload = {
        **MOCK_MODEL_RESPONSE,
        "intervention_type": "expiry_config",
        "intervention_params": {"expiry_hours": 5000},  # > 180 days
    }
    client = FakeOpenAIClient(payload)
    with pytest.raises(DiagnosisOutputInvalidError):
        diagnose_opportunity(db, opp.id, client=client)
    assert db.query(Hypothesis).count() == 0
    # The structured-output call did happen; validation is what rejected it.
    assert len(client.completions.calls) == 1
    assert client.completions.calls[0]["response_format"] is HypothesisProposal


def test_structured_parse_failure_raises_output_invalid(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(db, "m_parsefail")
    opp = make_opportunity(db, merchant.id)

    client = FakeOpenAIClient(payload=None)  # parsed is None
    with pytest.raises(DiagnosisOutputInvalidError):
        diagnose_opportunity(db, opp.id, client=client)
    assert db.query(Hypothesis).count() == 0


def test_openai_api_failure_raises_diagnosis_error(temp_db_session):
    from openai import OpenAIError

    db = temp_db_session
    merchant = create_merchant(db, "m_apierr")
    opp = make_opportunity(db, merchant.id)

    client = FakeOpenAIClient(error=OpenAIError("rate limited"))
    with pytest.raises(DiagnosisError, match="OpenAI request failed"):
        diagnose_opportunity(db, opp.id, client=client)
    assert db.query(Hypothesis).count() == 0


def test_duplicate_proposed_hypothesis_suppressed(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(db, "m_dup")
    opp = make_opportunity(db, merchant.id)

    first = diagnose_opportunity(db, opp.id, client=FakeOpenAIClient(MOCK_MODEL_RESPONSE))

    # Second call with a DIFFERENT payload must still return the existing
    # active proposal without calling the model again.
    client = FakeOpenAIClient(
        {**MOCK_MODEL_RESPONSE, "intervention_type": "offer_discount",
         "intervention_params": {"discount_pct": 0.05}}
    )
    second = diagnose_opportunity(db, opp.id, client=client)

    assert second.id == first.id
    assert db.query(Hypothesis).filter(Hypothesis.opportunity_id == opp.id).count() == 1
    assert client.completions.calls == []  # no regeneration


def test_rejected_hypothesis_allows_new_proposal(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(db, "m_rejected")
    opp = make_opportunity(db, merchant.id)

    first = diagnose_opportunity(db, opp.id, client=FakeOpenAIClient(MOCK_MODEL_RESPONSE))
    first.status = "rejected"
    db.flush()

    second = diagnose_opportunity(db, opp.id, client=FakeOpenAIClient(MOCK_MODEL_RESPONSE))
    assert second.id != first.id
    assert second.status == "proposed"
    assert db.query(Hypothesis).filter(Hypothesis.opportunity_id == opp.id).count() == 2


def test_merchant_isolation(temp_db_session):
    db = temp_db_session
    merchant_a = create_merchant(
        db, "merchant_a", allowed_interventions=["offer_discount"]
    )
    merchant_b = create_merchant(db, "merchant_b", allowed_interventions=ALL_INTERVENTIONS)
    opp_a = make_opportunity(db, merchant_a.id, segment="android_budget")
    opp_b = make_opportunity(db, merchant_b.id, segment="ios_premium")

    # Merchant A's policy (not merchant B's) governs A's opportunity.
    with pytest.raises(DiagnosisOutputInvalidError, match="not in the allowed"):
        diagnose_opportunity(db, opp_a.id, client=FakeOpenAIClient(MOCK_MODEL_RESPONSE))

    offer_payload = {
        **MOCK_MODEL_RESPONSE,
        "intervention_type": "offer_discount",
        "intervention_params": {"discount_pct": 0.10},
    }
    hypothesis = diagnose_opportunity(db, opp_a.id, client=FakeOpenAIClient(offer_payload))
    assert hypothesis.merchant_id == merchant_a.id
    assert hypothesis.opportunity_id == opp_a.id

    # Merchant B's opportunity is untouched.
    assert db.query(Hypothesis).filter(Hypothesis.merchant_id == merchant_b.id).count() == 0
    assert db.query(Hypothesis).filter(Hypothesis.opportunity_id == opp_b.id).count() == 0


def test_missing_opportunity_raises_clear_error(temp_db_session):
    with pytest.raises(DiagnosisError, match="not found"):
        diagnose_opportunity(
            temp_db_session,
            "opportunity_does_not_exist",
            client=FakeOpenAIClient(MOCK_MODEL_RESPONSE),
        )


def test_missing_api_key_raises_configuration_error(temp_db_session, clean_settings):
    db = temp_db_session
    merchant = create_merchant(db, "m_nokey")
    opp = make_opportunity(db, merchant.id)

    assert get_settings().OPENAI_API_KEY is None
    with pytest.raises(DiagnosisConfigurationError, match="OPENAI_API_KEY"):
        diagnose_opportunity(db, opp.id)  # no injected client
    assert db.query(Hypothesis).count() == 0


def test_injected_fake_client_works_without_api_key(temp_db_session, clean_settings):
    db = temp_db_session
    merchant = create_merchant(db, "m_injected")
    opp = make_opportunity(db, merchant.id)

    assert get_settings().OPENAI_API_KEY is None
    hypothesis = diagnose_opportunity(db, opp.id, client=FakeOpenAIClient(MOCK_MODEL_RESPONSE))
    assert hypothesis.status == "proposed"


def test_prompt_receives_only_observable_evidence(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(db, "m_prompt")
    evidence = make_evidence()
    evidence["causal_true_uplift"] = 0.9  # must never reach the model
    evidence["hidden_effect"] = 0.5
    opp = make_opportunity(db, merchant.id, evidence=evidence)

    client = FakeOpenAIClient(MOCK_MODEL_RESPONSE)
    diagnose_opportunity(db, opp.id, client=client)

    assert len(client.completions.calls) == 1
    call = client.completions.calls[0]
    messages = call["messages"]
    assert messages[0]["role"] == "system"
    assert call["response_format"] is HypothesisProposal

    prompt_text = json.dumps([m["content"] for m in messages])

    # Observable context is present.
    assert "segment_conversion_rate" in prompt_text
    assert "comparison_conversion_rate" in prompt_text
    assert "payment_method.upi.success_rate" in prompt_text
    assert "failure_reason.bank_declined" in prompt_text
    assert "payment_method_config" in prompt_text  # allowed interventions
    assert opp.id in prompt_text

    # Hidden/causal data is absent.
    for forbidden in (
        "causal_true_uplift",
        "hidden_effect",
        "simulate_outcome",
        "causal_model_fingerprint",
    ):
        assert forbidden not in prompt_text


def test_persist_hypothesis_helper_is_pure_flush(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(db, "m_helper", with_policy=False)
    opp = make_opportunity(db, merchant.id)
    proposal = validate_proposal(
        make_proposal(), build_evidence_catalog(opp), set(INTERVENTION_TYPE_SET)
    )

    hypothesis = persist_hypothesis(
        db, opportunity=opp, proposal=proposal, ai_model="helper-model"
    )
    assert hypothesis.ai_model == "helper-model"
    assert hypothesis.status == "proposed"
    assert db.query(Hypothesis).filter(Hypothesis.id == hypothesis.id).one() is hypothesis


# ===========================================================================
# Fail-closed intervention allow-list resolution
# ===========================================================================


def test_resolve_allowed_interventions_no_policy_returns_full_set():
    # No merchant-specific allow-list configured → full supported set.
    assert _resolve_allowed_interventions(None) == set(INTERVENTION_TYPE_SET)
    assert _resolve_allowed_interventions(None) == {
        "payment_method_config",
        "offer_discount",
        "partial_payment",
        "expiry_config",
    }


def test_resolve_allowed_interventions_returns_exactly_configured_subset():
    policy = MerchantPolicy(
        allowed_interventions=["payment_method_config", "offer_discount"]
    )
    assert _resolve_allowed_interventions(policy) == {
        "payment_method_config",
        "offer_discount",
    }


def test_resolve_allowed_interventions_empty_allow_list_returns_empty_set():
    # Explicit empty allow-list must NOT fall back to all interventions.
    policy = MerchantPolicy(allowed_interventions=[])
    assert _resolve_allowed_interventions(policy) == set()


def test_resolve_allowed_interventions_unsupported_only_returns_empty_set():
    policy = MerchantPolicy(allowed_interventions=["unknown_action"])
    assert _resolve_allowed_interventions(policy) == set()


def test_resolve_allowed_interventions_malformed_allow_list_fails_closed():
    # Non-collection allow-lists must fail closed (never grant all).
    for malformed in ("payment_method_config", 42, {"payment_method_config": True}, None):
        policy = MerchantPolicy(allowed_interventions=malformed)
        assert _resolve_allowed_interventions(policy) == set()


def test_diagnose_with_empty_allow_list_fails_closed_before_model(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(db, "m_empty_allow", allowed_interventions=[])
    opp = make_opportunity(db, merchant.id)

    client = FakeOpenAIClient(MOCK_MODEL_RESPONSE)
    with pytest.raises(DiagnosisConfigurationError, match="No interventions are enabled"):
        diagnose_opportunity(db, opp.id, client=client)

    assert client.completions.calls == []  # the model is never called
    assert db.query(Hypothesis).count() == 0  # nothing persisted


def test_diagnose_with_unsupported_only_allow_list_fails_closed(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(
        db, "m_unknown_allow", allowed_interventions=["unknown_action", "send_email"]
    )
    opp = make_opportunity(db, merchant.id)

    client = FakeOpenAIClient(MOCK_MODEL_RESPONSE)
    with pytest.raises(DiagnosisConfigurationError, match="No interventions are enabled"):
        diagnose_opportunity(db, opp.id, client=client)

    assert client.completions.calls == []
    assert db.query(Hypothesis).count() == 0


def test_diagnose_with_malformed_allow_list_fails_closed(temp_db_session):
    db = temp_db_session
    merchant = create_merchant(
        db, "m_malformed_allow", allowed_interventions="payment_method_config"
    )
    opp = make_opportunity(db, merchant.id)

    client = FakeOpenAIClient(MOCK_MODEL_RESPONSE)
    with pytest.raises(DiagnosisConfigurationError, match="No interventions are enabled"):
        diagnose_opportunity(db, opp.id, client=client)

    assert client.completions.calls == []
    assert db.query(Hypothesis).count() == 0


# ===========================================================================
# 32-36. Source hygiene: causal isolation, Razorpay isolation, no commit
# ===========================================================================


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def test_no_causal_model_imports():
    for path in (SCHEMA_PATH, ENGINE_PATH):
        modules = _imported_modules(path)
        for module in modules:
            assert "causal_model" not in module, f"{path.name} imports {module}"
            assert "simulation" not in module, f"{path.name} imports {module}"


def test_source_contains_no_causal_symbols():
    for path in (SCHEMA_PATH, ENGINE_PATH):
        source = path.read_text(encoding="utf-8")
        assert "simulate_outcome" not in source, path
        assert "causal_model_fingerprint" not in source, path


def test_source_does_not_import_razorpay():
    for path in (SCHEMA_PATH, ENGINE_PATH):
        modules = _imported_modules(path)
        for module in modules:
            assert "razorpay" not in module.lower(), f"{path.name} imports {module}"
        source = path.read_text(encoding="utf-8")
        assert "RazorpayClient" not in source, path


def test_source_does_not_call_database_commit():
    for path in (SCHEMA_PATH, ENGINE_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "commit"
            ):
                pytest.fail(f"{path.name} calls .commit()")


def test_openai_dependency_declared():
    pyproject = (BACKEND_DIR / "pyproject.toml").read_text(encoding="utf-8")
    assert 'openai' in pyproject


def test_env_example_documents_openai_model():
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=" in env_example
    assert "OPENAI_MODEL=" in env_example


# ===========================================================================
# TechBazaar integration: Task 05 seed -> Task 07 detection -> Task 08 diagnosis
# ===========================================================================


def test_techbazaar_diagnosis_end_to_end(temp_db_session):
    db = temp_db_session

    # Task 05: deterministic TechBazaar baseline.
    events = generate_baseline_events(
        profile=TECHBAZAAR_PROFILE, seed=20260827, days=30
    )
    db.add(
        Merchant(
            id=TECHBAZAAR_PROFILE.merchant_id,
            name=TECHBAZAAR_PROFILE.name,
            category=TECHBAZAAR_PROFILE.category,
            monthly_gmv=TECHBAZAAR_PROFILE.monthly_gmv_paise,
        )
    )
    db.add(
        MerchantPolicy(
            id="policy_techbazaar",
            merchant_id=TECHBAZAAR_PROFILE.merchant_id,
            allowed_interventions=list(ALL_INTERVENTIONS),
        )
    )
    db.add_all(
        [
            PaymentAttempt(
                id=evt.id,
                merchant_id=evt.merchant_id,
                customer_ref=evt.customer_ref,
                amount=evt.amount,
                currency=evt.currency,
                payment_method=evt.payment_method,
                status=evt.status,
                failure_reason=evt.failure_reason,
                device_type=evt.device_type,
                segment=evt.segment,
                source=evt.source,
                created_at=evt.created_at,
                completed_at=evt.completed_at,
                is_simulated=evt.is_simulated,
            )
            for evt in events
        ]
    )
    db.flush()

    # Task 07: opportunity detection + persistence.
    opportunities = run_opportunity_detection(db, TECHBAZAAR_PROFILE.merchant_id)
    assert opportunities, "TechBazaar baseline should yield at least one opportunity"
    opportunity = opportunities[0]

    # Task 08: diagnosis with a FAKE structured model response (no live API).
    catalog = build_evidence_catalog(opportunity)
    assert "segment_conversion_rate" in catalog
    assert "comparison_conversion_rate" in catalog

    payload = {
        **MOCK_MODEL_RESPONSE,
        "evidence_refs": [
            "segment_conversion_rate",
            "comparison_conversion_rate",
            "absolute_gap",
            "payment_method.upi.success_rate",
            "payment_method.card.success_rate",
        ],
    }
    hypothesis = diagnose_opportunity(
        db, opportunity.id, client=FakeOpenAIClient(payload)
    )

    # Hypothesis row created and linked to the correct opportunity.
    assert hypothesis.id is not None
    assert hypothesis.opportunity_id == opportunity.id
    assert hypothesis.merchant_id == TECHBAZAAR_PROFILE.merchant_id
    assert hypothesis.status == "proposed"
    assert hypothesis.ai_model == get_settings().OPENAI_MODEL

    # Only observable evidence refs; every ref exists in the catalog.
    assert hypothesis.evidence_refs
    assert set(hypothesis.evidence_refs) <= set(catalog)

    # Allowed intervention type only.
    assert hypothesis.intervention_type in INTERVENTION_TYPES
    assert hypothesis.intervention_params == {"card": False, "upi": True}

    # Exactly one hypothesis for this opportunity (duplicate suppressed).
    assert (
        db.query(Hypothesis).filter(Hypothesis.opportunity_id == opportunity.id).count()
        == 1
    )
