"""Task 19D tests for deterministic memory-aware diagnosis."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import (
    Experiment,
    ExperimentResult,
    Hypothesis,
    Merchant,
    Opportunity,
    PolicyDecision,
)
from app.engines.diagnosis import (
    DiagnosisOutputInvalidError,
    build_evidence_catalog,
    diagnose_opportunity,
)
from app.schemas.hypothesis import HypothesisProposal
from app.services.diagnosis_memory import (
    build_diagnosis_memory,
    material_evidence_change,
    prompt_memory_payload,
    stale_repeat_reason,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]
MEMORY_PATH = BACKEND_DIR / "app" / "services" / "diagnosis_memory.py"


class SequencedCompletions:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if not self.payloads:
            raise AssertionError("fake model was called more times than expected")
        parsed = HypothesisProposal.model_validate(self.payloads.pop(0))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))]
        )


class FakeClient:
    base_url = "https://api.openai.com/v1"

    def __init__(self, payloads):
        self.completions = SequencedCompletions(payloads)
        self.chat = SimpleNamespace(completions=self.completions)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'diagnosis-memory.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([Merchant(id="merchant-a", name="A"), Merchant(id="merchant-b", name="B")])
    session.flush()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _evidence(
    *,
    attempts: int = 500,
    segment_rate: float = 0.45,
    comparison_rate: float = 0.55,
) -> dict:
    return {
        "segment_attempts": attempts,
        "segment_captured": int(round(attempts * segment_rate)),
        "segment_conversion_rate": segment_rate,
        "comparison_attempts": 1000,
        "comparison_captured": int(round(1000 * comparison_rate)),
        "comparison_conversion_rate": comparison_rate,
        "absolute_gap": comparison_rate - segment_rate,
        "payment_method_metrics": {
            "upi": {
                "attempts": attempts,
                "captured": int(round(attempts * segment_rate)),
                "failed": 0,
                "abandoned": 0,
                "success_rate": segment_rate,
            }
        },
        "failure_reasons": {},
    }


def _opportunity(
    db,
    *,
    opportunity_id: str,
    merchant_id: str = "merchant-a",
    segment: str = "android_budget",
    evidence: dict | None = None,
    created_at: datetime | None = None,
) -> Opportunity:
    evidence = evidence or _evidence()
    row = Opportunity(
        id=opportunity_id,
        merchant_id=merchant_id,
        type="segment_conversion_divergence",
        segment=segment,
        severity=0.1,
        detected_metric="conversion_rate",
        detected_value=evidence["segment_conversion_rate"],
        baseline_value=evidence["comparison_conversion_rate"],
        evidence=evidence,
        status="detected",
        created_at=created_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def _prior_trial(
    db,
    *,
    suffix: str,
    outcome: str,
    params: dict | None = None,
    intervention_type: str = "offer_discount",
    evidence: dict | None = None,
    merchant_id: str = "merchant-a",
    segment: str = "android_budget",
    created_at: datetime | None = None,
) -> Experiment:
    when = created_at or datetime(2026, 1, 1, tzinfo=timezone.utc)
    opportunity = _opportunity(
        db,
        opportunity_id=f"opp-{suffix}",
        merchant_id=merchant_id,
        segment=segment,
        evidence=evidence,
        created_at=when,
    )
    semantic_params = params or {"discount_pct": 0.10}
    hypothesis = Hypothesis(
        id=f"hyp-{suffix}",
        opportunity_id=opportunity.id,
        merchant_id=merchant_id,
        hypothesis_text="prior hypothesis",
        intervention_type=intervention_type,
        intervention_params=semantic_params,
        evidence_refs=["segment_conversion_rate"],
        status="proposed",
        created_at=when,
    )
    db.add(hypothesis)
    db.flush()
    experiment = Experiment(
        id=f"exp-{suffix}",
        merchant_id=merchant_id,
        hypothesis_id=hypothesis.id,
        opportunity_id=opportunity.id,
        name=f"{segment}-{intervention_type}",
        segment=segment,
        intervention_type=intervention_type,
        control_config={"offer": None},
        treatment_config=semantic_params,
        traffic_split_treatment_pct=0.10,
        primary_metric="conversion_rate",
        guardrail_metrics=[],
        min_sample_per_variant=200,
        max_duration_hours=72,
        status="rejected" if outcome == "POLICY_REJECTED" else "completed",
        created_at=when,
    )
    db.add(experiment)
    db.flush()

    if outcome == "POLICY_REJECTED":
        db.add(
            PolicyDecision(
                id=f"policy-{suffix}",
                experiment_id=experiment.id,
                merchant_id=merchant_id,
                decision="REJECT",
                violations=["DISCOUNT_CAP_EXCEEDED"],
                original_params=semantic_params,
                final_params=None,
                evaluated_at=when + timedelta(minutes=1),
            )
        )
    else:
        db.add(
            ExperimentResult(
                id=f"result-{suffix}",
                experiment_id=experiment.id,
                control_count=200,
                treatment_count=200,
                control_conversions=100,
                treatment_conversions=100,
                control_rate=0.50,
                treatment_rate=0.50,
                absolute_lift=(0.05 if outcome == "KEEP" else (-0.05 if outcome == "ROLLBACK" else 0.0)),
                relative_lift=0.0,
                p_value=(0.001 if outcome in {"KEEP", "ROLLBACK"} else 0.80),
                confidence_interval_lower=-0.02,
                confidence_interval_upper=0.02,
                is_significant=outcome in {"KEEP", "ROLLBACK"},
                decision=outcome,
                decided_at=when + timedelta(minutes=1),
            )
        )
    db.flush()
    return experiment


def _proposal(*, discount: float = 0.10) -> dict:
    return {
        "diagnosis": "The segment underperforms its comparison cohort.",
        "hypothesis_text": "Test a bounded offer to improve conversion.",
        "intervention_type": "offer_discount",
        "intervention_params": {"discount_pct": discount},
        "confidence": "medium",
        "reasoning_summary": "Observed conversion is lower than the comparison cohort.",
        "evidence_refs": ["segment_conversion_rate", "comparison_conversion_rate"],
    }


def _memory_for(db, current: Opportunity):
    catalog = build_evidence_catalog(current)
    return build_diagnosis_memory(
        db,
        current,
        catalog,
        evidence_catalog_builder=build_evidence_catalog,
    )


def test_material_evidence_change_ignores_small_noise():
    prior = {
        "segment_attempts": 500,
        "segment_conversion_rate": 0.45,
        "comparison_conversion_rate": 0.55,
        "absolute_gap": 0.10,
    }
    current = {
        "segment_attempts": 580,
        "segment_conversion_rate": 0.461,
        "comparison_conversion_rate": 0.555,
        "absolute_gap": 0.094,
    }
    changed, reasons = material_evidence_change(current, prior)
    assert changed is False
    assert reasons == ()


def test_material_rate_change_at_two_points_allows_reconsideration():
    prior = {
        "segment_attempts": 500,
        "segment_conversion_rate": 0.45,
        "comparison_conversion_rate": 0.55,
        "absolute_gap": 0.10,
    }
    current = dict(prior, segment_conversion_rate=0.47, absolute_gap=0.08)
    changed, reasons = material_evidence_change(current, prior)
    assert changed is True
    assert any(reason.startswith("segment_conversion_rate_delta=") for reason in reasons)


def test_material_sample_growth_requires_absolute_and_relative_thresholds():
    prior = {"segment_attempts": 500}
    changed, _ = material_evidence_change({"segment_attempts": 599}, prior)
    assert changed is False
    changed, _ = material_evidence_change({"segment_attempts": 600}, prior)
    assert changed is True


def test_unchanged_exact_rollback_is_blocked_but_different_params_are_allowed(db):
    _prior_trial(db, suffix="rollback", outcome="ROLLBACK", params={"discount_pct": 0.10})
    current = _opportunity(db, opportunity_id="opp-current")
    memory = _memory_for(db, current)

    assert len(memory) == 1
    assert memory[0].repeat_blocked is True
    assert stale_repeat_reason(
        memory,
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.10},
    ) is not None
    assert stale_repeat_reason(
        memory,
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.05},
    ) is None


def test_inconclusive_exact_repeat_is_allowed_after_material_evidence_change(db):
    _prior_trial(db, suffix="inc", outcome="INCONCLUSIVE", params={"discount_pct": 0.10})
    current = _opportunity(
        db,
        opportunity_id="opp-current-changed",
        evidence=_evidence(segment_rate=0.41, comparison_rate=0.55),
    )
    memory = _memory_for(db, current)
    assert memory[0].evidence_materially_changed is True
    assert memory[0].repeat_blocked is False
    assert stale_repeat_reason(
        memory,
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.10},
    ) is None


def test_policy_rejected_exact_repeat_remains_blocked_after_evidence_change(db):
    _prior_trial(db, suffix="reject", outcome="POLICY_REJECTED", params={"discount_pct": 0.20})
    current = _opportunity(
        db,
        opportunity_id="opp-current-policy",
        evidence=_evidence(segment_rate=0.35, comparison_rate=0.55),
    )
    memory = _memory_for(db, current)
    assert memory[0].evidence_materially_changed is True
    assert memory[0].repeat_blocked is True
    reason = stale_repeat_reason(
        memory,
        intervention_type="offer_discount",
        intervention_params={"discount_pct": 0.20},
    )
    assert reason is not None and "policy-rejected" in reason


def test_memory_is_scoped_to_same_merchant_segment_and_recent_first(db):
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _prior_trial(db, suffix="old", outcome="ROLLBACK", created_at=t0)
    _prior_trial(db, suffix="new", outcome="INCONCLUSIVE", created_at=t0 + timedelta(days=1))
    _prior_trial(db, suffix="foreign", outcome="ROLLBACK", merchant_id="merchant-b")
    _prior_trial(db, suffix="other-segment", outcome="ROLLBACK", segment="web_general")
    current = _opportunity(db, opportunity_id="opp-current-scope")

    memory = _memory_for(db, current)
    assert [trial.experiment_id for trial in memory] == ["exp-new", "exp-old"]


def test_prompt_memory_payload_is_compact_and_contains_no_hidden_truth(db):
    _prior_trial(db, suffix="prompt", outcome="ROLLBACK")
    current = _opportunity(db, opportunity_id="opp-current-prompt")
    payload = prompt_memory_payload(_memory_for(db, current))
    assert payload[0]["repeat_blocked"] is True
    assert payload[0]["outcome"] == "ROLLBACK"
    serialized = json.dumps(payload, sort_keys=True).lower()
    for forbidden in ("causal", "simulator", "true_effect", "hidden", "chain_of_thought"):
        assert forbidden not in serialized


def test_diagnosis_retries_stale_exact_proposal_then_persists_alternative(db):
    _prior_trial(db, suffix="retry", outcome="ROLLBACK", params={"discount_pct": 0.10})
    current = _opportunity(db, opportunity_id="opp-current-retry")
    client = FakeClient([_proposal(discount=0.10), _proposal(discount=0.05)])

    hypothesis = diagnose_opportunity(db, current.id, client=client)

    assert hypothesis.intervention_params == {"discount_pct": 0.05}
    assert len(client.completions.calls) == 2
    assert db.query(Hypothesis).filter(Hypothesis.opportunity_id == current.id).count() == 1

    first_prompt = client.completions.calls[0]["messages"]
    prompt_text = "\n".join(message["content"] for message in first_prompt)
    assert '"experiment_memory"' in prompt_text
    assert '"repeat_blocked": true' in prompt_text.lower()

    second_prompt = client.completions.calls[1]["messages"]
    assert any("deterministic memory validation rejected" in message["content"].lower() for message in second_prompt)


def test_two_stale_exact_proposals_fail_without_persisting_current_hypothesis(db):
    _prior_trial(db, suffix="reject-repeat", outcome="INCONCLUSIVE", params={"discount_pct": 0.10})
    current = _opportunity(db, opportunity_id="opp-current-reject-repeat")
    client = FakeClient([_proposal(discount=0.10), _proposal(discount=0.10)])

    with pytest.raises(DiagnosisOutputInvalidError, match="repeats blocked prior experiment"):
        diagnose_opportunity(db, current.id, client=client)

    assert len(client.completions.calls) == 2
    assert db.query(Hypothesis).filter(Hypothesis.opportunity_id == current.id).count() == 0


def test_no_history_preserves_single_request_behavior(db):
    current = _opportunity(db, opportunity_id="opp-current-clean")
    client = FakeClient([_proposal(discount=0.10)])

    hypothesis = diagnose_opportunity(db, current.id, client=client)

    assert hypothesis.intervention_params == {"discount_pct": 0.10}
    assert len(client.completions.calls) == 1


def test_diagnosis_memory_service_is_read_only_and_execution_isolated():
    source = MEMORY_PATH.read_text(encoding="utf-8")
    assert ".commit(" not in source
    assert "db.add(" not in source
    assert "db.delete(" not in source
    for forbidden in (
        "OpenAI",
        "RazorpayClient",
        "simulate_outcome",
        "causal_model",
        "engines.policy",
        "record_audit_event",
    ):
        assert forbidden not in source
