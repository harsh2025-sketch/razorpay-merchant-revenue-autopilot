"""Structured hypothesis proposal schema (Task 08).

Pydantic models describing the ONLY shape the AI diagnosis layer is allowed
to produce.  The model output must pass this strict schema and the
deterministic semantic validation in ``app.engines.diagnosis`` before a
Hypothesis can be persisted.

This module must never import the simulation/causal layer or the Razorpay
service.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Allowed intervention universe
# ---------------------------------------------------------------------------

#: The only intervention types the AI may ever propose.
INTERVENTION_TYPES: tuple[str, ...] = (
    "payment_method_config",
    "offer_discount",
    "partial_payment",
    "expiry_config",
)

INTERVENTION_TYPE_SET: frozenset[str] = frozenset(INTERVENTION_TYPES)

AllowedInterventionType = Literal[
    "payment_method_config",
    "offer_discount",
    "partial_payment",
    "expiry_config",
]

AllowedConfidence = Literal["low", "medium", "high"]

# ---------------------------------------------------------------------------
# Semantic intervention parameter contracts
#
# These are *semantic* parameters only. No Razorpay payloads are described
# or validated here. Merchant policy limits (e.g. max discount) are NOT
# enforced at this layer - that is Task 10's job.
# ---------------------------------------------------------------------------

#: payment_method_config - boolean enable/disable flags per payment method.
PAYMENT_METHOD_KEYS: tuple[str, ...] = ("card", "upi", "netbanking", "wallet")

#: offer_discount - fractional discount percentage (schema bound: 0 < x <= 0.50).
OFFER_DISCOUNT_MAX_PCT: float = 0.50

#: partial_payment - fractional minimum first installment (0 < x <= 1).
PARTIAL_PAYMENT_MIN_MAX_PCT: float = 1.0

#: expiry_config - link/payment expiry in hours (0 < x <= 180 days).
EXPIRY_MAX_HOURS: float = 24 * 180

#: Maximum externally visible explanation length (no chain-of-thought storage).
REASONING_SUMMARY_MAX_LENGTH: int = 600
DIAGNOSIS_MAX_LENGTH: int = 1000
HYPOTHESIS_TEXT_MAX_LENGTH: int = 2000
EVIDENCE_REFS_MAX_ITEMS: int = 20

#: Structured description of the semantic parameter contracts, shared with
#: the model prompt so proposals are well-formed on the first try.
INTERVENTION_PARAM_CONTRACTS: dict[str, dict[str, str]] = {
    "payment_method_config": {
        "allowed_keys": "card, upi, netbanking, wallet",
        "value_types": "each value must be a boolean (true/false)",
        "rule": "at least one payment method key must be present",
    },
    "offer_discount": {
        "allowed_keys": "discount_pct",
        "value_types": "number",
        "rule": "0 < discount_pct <= 0.50",
    },
    "partial_payment": {
        "allowed_keys": "accept_partial, first_min_partial_amount_pct",
        "value_types": "accept_partial: boolean; first_min_partial_amount_pct: number",
        "rule": (
            "0 < first_min_partial_amount_pct <= 1; "
            "if first_min_partial_amount_pct is set, accept_partial must be true"
        ),
    },
    "expiry_config": {
        "allowed_keys": "expiry_hours",
        "value_types": "number",
        "rule": "0 < expiry_hours <= 4320 (180 days)",
    },
}


# ---------------------------------------------------------------------------
# Structured proposal schema
# ---------------------------------------------------------------------------


class HypothesisProposal(BaseModel):
    """Structured, externally-visible output of the AI diagnosis step.

    ``reasoning_summary`` is a short decision explanation intended for
    merchant/operator consumption. It is NOT chain-of-thought: the model is
    instructed to keep it concise and bounded.
    """

    model_config = ConfigDict(extra="forbid")

    diagnosis: str = Field(
        ...,
        min_length=1,
        max_length=DIAGNOSIS_MAX_LENGTH,
        description="Merchant-visible diagnosis of the observed anomaly.",
    )
    hypothesis_text: str = Field(
        ...,
        min_length=1,
        max_length=HYPOTHESIS_TEXT_MAX_LENGTH,
        description="One experimentable hypothesis phrased without causal certainty.",
    )
    intervention_type: AllowedInterventionType = Field(
        ...,
        description="The single intervention type proposed for the experiment.",
    )
    intervention_params: dict[str, object] = Field(
        ...,
        description="Semantic intervention parameters (never Razorpay payloads).",
    )
    confidence: AllowedConfidence = Field(
        ...,
        description="Qualitative confidence in the diagnosis.",
    )
    reasoning_summary: str = Field(
        ...,
        min_length=1,
        max_length=REASONING_SUMMARY_MAX_LENGTH,
        description=(
            "Concise externally visible decision explanation (<= 600 chars), "
            "not hidden chain-of-thought."
        ),
    )
    evidence_refs: list[str] = Field(
        ...,
        min_length=1,
        max_length=EVIDENCE_REFS_MAX_ITEMS,
        description=(
            "Keys from the supplied evidence catalog that support the "
            "diagnosis. Must match catalog keys exactly."
        ),
    )
