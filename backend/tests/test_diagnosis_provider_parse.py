"""Regression coverage for provider-side structured-output parsing failures."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.engines.diagnosis import (
    DiagnosisOutputInvalidError,
    _provider_parse_kwargs,
    _request_proposal,
)
from app.schemas.hypothesis import HypothesisProposal


VALID_PAYLOAD = {
    "diagnosis": "Checkout completion is weaker in this segment.",
    "hypothesis_text": "Adjusting available payment methods may improve completion.",
    "intervention_type": "payment_method_config",
    "intervention_params": {"card": False, "upi": True},
    "confidence": "medium",
    "reasoning_summary": "Observed conversion and payment-method differences justify a controlled experiment.",
    "evidence_refs": ["segment_conversion_rate"],
}


class SequencedCompletions:
    def __init__(self, outcomes: list[object]):
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, object]] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if outcome == "validation_error":
            # Mirrors the OpenAI SDK's local Pydantic failure after a provider
            # returns a response that does not match the requested schema.
            HypothesisProposal.model_validate({})
        parsed = HypothesisProposal.model_validate(outcome)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))]
        )


class FakeClient:
    def __init__(self, outcomes: list[object], *, base_url: str = "https://api.openai.com/v1"):
        self.base_url = base_url
        self.completions = SequencedCompletions(outcomes)
        self.chat = SimpleNamespace(completions=self.completions)


def test_pydantic_parse_failure_retries_once_then_succeeds():
    client = FakeClient(["validation_error", VALID_PAYLOAD])

    proposal = _request_proposal(client, "test-model", [{"role": "user", "content": "x"}])

    assert proposal.intervention_type == "payment_method_config"
    assert len(client.completions.calls) == 2


def test_repeated_pydantic_parse_failure_fails_closed():
    client = FakeClient(["validation_error", "validation_error"])

    with pytest.raises(DiagnosisOutputInvalidError, match="bounded retry"):
        _request_proposal(client, "test-model", [{"role": "user", "content": "x"}])

    assert len(client.completions.calls) == 2


def test_openrouter_requires_structured_output_capability():
    client = FakeClient([VALID_PAYLOAD], base_url="https://openrouter.ai/api/v1")

    assert _provider_parse_kwargs(client) == {
        "extra_body": {"provider": {"require_parameters": True}}
    }

    _request_proposal(client, "openrouter/free", [{"role": "user", "content": "x"}])
    assert client.completions.calls[0]["extra_body"] == {
        "provider": {"require_parameters": True}
    }


def test_native_openai_does_not_receive_openrouter_routing_options():
    client = FakeClient([VALID_PAYLOAD])

    assert _provider_parse_kwargs(client) == {}

    _request_proposal(client, "gpt-test", [{"role": "user", "content": "x"}])
    assert "extra_body" not in client.completions.calls[0]
