"""AI diagnosis engine - structured hypothesis proposal (Task 08).

Turns a persisted Opportunity plus its observable evidence into a validated
structured Hypothesis.

Core principle: the AI *proposes*, deterministic code *validates*.

Boundaries enforced by this module:
- The model receives ONLY merchant-visible opportunity evidence
  (the deterministic evidence catalog built here).
- The model output must satisfy the strict ``HypothesisProposal`` schema
  (via OpenAI structured outputs) AND the deterministic semantic validation
  below before anything is persisted.
- This module never imports the sealed causal/simulation model, never calls
  Razorpay, never enforces merchant financial policy, never plans
  experiments, and never commits the database transaction.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence

from openai import OpenAI, OpenAIError
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Hypothesis, MerchantPolicy, Opportunity
from app.engines.metrics import PAYMENT_METHOD_ORDER
from app.schemas.hypothesis import (
    EXPIRY_MAX_HOURS,
    INTERVENTION_PARAM_CONTRACTS,
    INTERVENTION_TYPE_SET,
    OFFER_DISCOUNT_MAX_PCT,
    PARTIAL_PAYMENT_MIN_MAX_PCT,
    PAYMENT_METHOD_KEYS,
    REASONING_SUMMARY_MAX_LENGTH,
    HypothesisProposal,
)
from app.services.audit import (
    ACTOR_AI,
    AI_DIAGNOSIS_CREATED,
    ENTITY_HYPOTHESIS,
    HYPOTHESIS_PROPOSED,
    record_audit_event_once,
)
from app.services.diagnosis_memory import (
    build_diagnosis_memory,
    prompt_memory_payload,
    stale_repeat_reason,
)


# ---------------------------------------------------------------------------
# Diagnosis-specific errors (small, no generic error framework)
# ---------------------------------------------------------------------------


class DiagnosisError(Exception):
    """Base error for the AI diagnosis engine."""


class DiagnosisConfigurationError(DiagnosisError):
    """Raised when diagnosis cannot run due to missing configuration."""


class DiagnosisOutputInvalidError(DiagnosisError):
    """Raised when model output fails deterministic validation."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Only hypotheses in this status count as "active" for duplicate suppression.
ACTIVE_HYPOTHESIS_STATUS = "proposed"

#: A malformed structured provider response is safe to request once more:
#: diagnosis has no external financial side effect, and nothing is persisted
#: until a proposal has passed schema + semantic validation.
STRUCTURED_OUTPUT_MAX_ATTEMPTS = 2
MEMORY_PROPOSAL_MAX_ATTEMPTS = 2

#: Top-level evidence keys that are flattened verbatim into the catalog.
_CATALOG_SCALAR_KEYS: tuple[str, ...] = (
    "segment_attempts",
    "segment_captured",
    "segment_conversion_rate",
    "comparison_attempts",
    "comparison_captured",
    "comparison_conversion_rate",
    "absolute_gap",
)

#: Observable per-payment-method statistics flattened into the catalog.
_PAYMENT_METHOD_STAT_KEYS: tuple[str, ...] = (
    "attempts",
    "captured",
    "failed",
    "abandoned",
    "success_rate",
)

#: Combined persisted reasoning length budget (diagnosis prefix + rationale).
_COMBINED_REASONING_MAX_LENGTH = 900

_SYSTEM_PROMPT = """You are diagnosing a merchant payment/revenue anomaly.

You have observable evidence only.

Rules you must follow:
- Respond only through the structured schema provided. No free-form text.
- Use only the provided evidence. Do not invent metrics, numbers, or keys.
- Evidence refs must exactly match the provided evidence catalog keys.
- Choose exactly one intervention from the allowed interventions list.
- intervention_params must contain only the semantic keys documented in the
  intervention param contracts. Never generate Razorpay API payloads.
- Do not claim causal certainty. Express one experimentable hypothesis.
- Do not discuss hidden simulator behavior or hidden causal effects.
- reasoning_summary is a short, externally visible decision explanation of at
  most 600 characters. Do not provide hidden chain-of-thought.
- Keep the diagnosis and explanation concise."""


# ---------------------------------------------------------------------------
# Deterministic evidence catalog
# ---------------------------------------------------------------------------


def _payment_method_sort_key(method: str) -> tuple[int, int, str]:
    """Deterministic payment-method ordering (known order first, then name)."""
    if method in PAYMENT_METHOD_ORDER:
        return (0, PAYMENT_METHOD_ORDER.index(method), method)
    return (1, len(PAYMENT_METHOD_ORDER), method)


def _is_number(value: object) -> bool:
    """True for finite ints/floats; bools are not numbers here."""
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def build_evidence_catalog(opportunity: Opportunity) -> dict[str, object]:
    """Flatten an Opportunity's evidence JSON into a deterministic catalog.

    Only a fixed allow-list of observable evidence keys is exposed. Anything
    else present in the evidence JSON (in particular hidden/causal keys that
    must never exist in this pipeline) is dropped, never forwarded to the
    model. The output key order is fully deterministic.
    """
    evidence: dict = dict(opportunity.evidence or {})
    catalog: dict[str, object] = {}

    for key in _CATALOG_SCALAR_KEYS:
        value = evidence.get(key)
        if value is not None:
            catalog[key] = value

    if "absolute_gap" not in catalog:
        segment_rate = evidence.get("segment_conversion_rate")
        comparison_rate = evidence.get("comparison_conversion_rate")
        if _is_number(segment_rate) and _is_number(comparison_rate):
            catalog["absolute_gap"] = comparison_rate - segment_rate

    method_metrics = evidence.get("payment_method_metrics")
    if isinstance(method_metrics, dict):
        for method in sorted(method_metrics, key=_payment_method_sort_key):
            stats = method_metrics[method]
            if not isinstance(stats, dict):
                continue
            for stat_key in _PAYMENT_METHOD_STAT_KEYS:
                value = stats.get(stat_key)
                if value is not None:
                    catalog[f"payment_method.{method}.{stat_key}"] = value

    failure_reasons = evidence.get("failure_reasons")
    if isinstance(failure_reasons, dict):
        for reason in sorted(failure_reasons):
            value = failure_reasons[reason]
            if _is_number(value):
                catalog[f"failure_reason.{reason}"] = value

    return catalog


# ---------------------------------------------------------------------------
# Deterministic proposal validation
# ---------------------------------------------------------------------------


def _reject_unsupported_params(
    intervention_type: str, params: dict[str, object], allowed_keys: Iterable[str]
) -> None:
    unsupported = sorted(set(params) - set(allowed_keys))
    if unsupported:
        raise DiagnosisOutputInvalidError(
            f"intervention_params for '{intervention_type}' contain unsupported "
            f"fields: {unsupported}; allowed keys: {sorted(allowed_keys)}"
        )


def _validate_intervention_params(
    intervention_type: str, params: object
) -> None:
    """Validate semantic intervention parameters for the chosen intervention.

    This is proposal-shape validation only. Unsafe-but-well-formed financial
    proposals intentionally pass here; merchant policy enforcement happens
    later.
    """
    if not isinstance(params, dict) or not params:
        raise DiagnosisOutputInvalidError(
            f"intervention_params for '{intervention_type}' must be a non-empty object"
        )

    if intervention_type == "payment_method_config":
        _reject_unsupported_params(intervention_type, params, PAYMENT_METHOD_KEYS)
        for method, value in params.items():
            if not isinstance(value, bool):
                raise DiagnosisOutputInvalidError(
                    f"payment_method_config value for '{method}' must be a boolean, "
                    f"got {type(value).__name__}"
                )

    elif intervention_type == "offer_discount":
        _reject_unsupported_params(intervention_type, params, {"discount_pct"})
        discount = params.get("discount_pct")
        if not _is_number(discount):
            raise DiagnosisOutputInvalidError(
                "offer_discount 'discount_pct' must be a number"
            )
        if not 0 < discount <= OFFER_DISCOUNT_MAX_PCT:
            raise DiagnosisOutputInvalidError(
                f"offer_discount 'discount_pct' must satisfy 0 < discount_pct <= "
                f"{OFFER_DISCOUNT_MAX_PCT}, got {discount}"
            )

    elif intervention_type == "partial_payment":
        _reject_unsupported_params(
            intervention_type, params, {"accept_partial", "first_min_partial_amount_pct"}
        )
        if "accept_partial" in params and not isinstance(params["accept_partial"], bool):
            raise DiagnosisOutputInvalidError(
                "partial_payment 'accept_partial' must be a boolean"
            )
        if "first_min_partial_amount_pct" in params:
            pct = params["first_min_partial_amount_pct"]
            if not _is_number(pct):
                raise DiagnosisOutputInvalidError(
                    "partial_payment 'first_min_partial_amount_pct' must be a number"
                )
            if not 0 < pct <= PARTIAL_PAYMENT_MIN_MAX_PCT:
                raise DiagnosisOutputInvalidError(
                    f"partial_payment 'first_min_partial_amount_pct' must satisfy "
                    f"0 < pct <= {PARTIAL_PAYMENT_MIN_MAX_PCT}, got {pct}"
                )
            if params.get("accept_partial") is not True:
                raise DiagnosisOutputInvalidError(
                    "partial_payment 'first_min_partial_amount_pct' requires "
                    "'accept_partial' to be true"
                )

    elif intervention_type == "expiry_config":
        _reject_unsupported_params(intervention_type, params, {"expiry_hours"})
        expiry_hours = params.get("expiry_hours")
        if not _is_number(expiry_hours):
            raise DiagnosisOutputInvalidError(
                "expiry_config 'expiry_hours' must be a number"
            )
        if not 0 < expiry_hours <= EXPIRY_MAX_HOURS:
            raise DiagnosisOutputInvalidError(
                f"expiry_config 'expiry_hours' must satisfy 0 < expiry_hours <= "
                f"{EXPIRY_MAX_HOURS} (180 days), got {expiry_hours}"
            )

    else:  # pragma: no cover - guarded by the schema Literal + check above
        raise DiagnosisOutputInvalidError(
            f"unknown intervention_type: {intervention_type!r}"
        )


def validate_proposal(
    proposal: HypothesisProposal,
    evidence_catalog: dict[str, object],
    allowed_interventions: set[str] | Sequence[str],
) -> HypothesisProposal:
    """Deterministically validate a structured proposal."""
    allowed = set(allowed_interventions)

    if proposal.intervention_type not in INTERVENTION_TYPE_SET:
        raise DiagnosisOutputInvalidError(
            f"unknown intervention_type: {proposal.intervention_type!r}; "
            f"allowed: {sorted(INTERVENTION_TYPE_SET)}"
        )
    if proposal.intervention_type not in allowed:
        raise DiagnosisOutputInvalidError(
            f"intervention_type {proposal.intervention_type!r} is not in the "
            f"allowed intervention set: {sorted(allowed)}"
        )

    for field_name in ("diagnosis", "hypothesis_text", "reasoning_summary"):
        value = getattr(proposal, field_name)
        if value is None or not value.strip():
            raise DiagnosisOutputInvalidError(
                f"proposal field '{field_name}' must not be empty"
            )

    if len(proposal.reasoning_summary) > REASONING_SUMMARY_MAX_LENGTH:
        raise DiagnosisOutputInvalidError(
            f"reasoning_summary exceeds {REASONING_SUMMARY_MAX_LENGTH} characters"
        )

    if proposal.confidence not in ("low", "medium", "high"):
        raise DiagnosisOutputInvalidError(
            f"invalid confidence: {proposal.confidence!r}"
        )

    if not proposal.evidence_refs:
        raise DiagnosisOutputInvalidError(
            "proposal must cite at least one evidence ref"
        )
    unknown_refs = [ref for ref in proposal.evidence_refs if ref not in evidence_catalog]
    if unknown_refs:
        raise DiagnosisOutputInvalidError(
            f"evidence refs not present in the supplied evidence catalog: "
            f"{unknown_refs}; valid keys: {sorted(evidence_catalog)}"
        )

    _validate_intervention_params(proposal.intervention_type, proposal.intervention_params)

    return proposal.model_copy(
        update={
            "diagnosis": proposal.diagnosis.strip(),
            "hypothesis_text": proposal.hypothesis_text.strip(),
            "reasoning_summary": proposal.reasoning_summary.strip(),
            "evidence_refs": list(proposal.evidence_refs),
            "intervention_params": dict(proposal.intervention_params),
        }
    )


# ---------------------------------------------------------------------------
# Prompt construction (observable context only)
# ---------------------------------------------------------------------------


def _build_prompt_messages(
    opportunity: Opportunity,
    evidence_catalog: dict[str, object],
    allowed_interventions: set[str],
    experiment_memory: Sequence[dict[str, object]] = (),
) -> list[dict[str, str]]:
    """Build the model prompt from observable opportunity data only."""
    allowed = sorted(allowed_interventions)
    context = {
        "opportunity": {
            "id": opportunity.id,
            "type": opportunity.type,
            "segment": opportunity.segment,
            "severity": opportunity.severity,
            "detected_metric": opportunity.detected_metric,
            "detected_value": opportunity.detected_value,
            "baseline_value": opportunity.baseline_value,
        },
        "evidence_catalog": evidence_catalog,
        "allowed_interventions": allowed,
        "experiment_memory": list(experiment_memory),
        "intervention_param_contracts": {
            name: INTERVENTION_PARAM_CONTRACTS[name] for name in allowed
        },
    }
    user_content = (
        "Diagnose the merchant payment anomaly described below and propose "
        "exactly one experimentable hypothesis.\n\n"
        "Rules:\n"
        "- Use only the evidence in evidence_catalog. Do not invent metrics.\n"
        "- evidence_refs must exactly match keys of evidence_catalog.\n"
        "- Choose exactly one intervention_type from allowed_interventions.\n"
        "- experiment_memory contains prior observed experiment outcomes. Exact "
        "configs with repeat_blocked=true must not be proposed again.\n"
        "- A different semantic configuration of the same intervention remains "
        "allowed when it satisfies the intervention contract.\n"
        "- Treat experiment history as observed evidence, never causal certainty.\n"
        "- intervention_params must follow intervention_param_contracts; they "
        "are semantic parameters, never Razorpay payloads.\n\n"
        f"{json.dumps(context, indent=2, sort_keys=True, default=str)}\n"
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


# ---------------------------------------------------------------------------
# OpenAI-compatible structured-output boundary (the only network call)
# ---------------------------------------------------------------------------


def _provider_parse_kwargs(client: OpenAI) -> dict[str, object]:
    """Return provider-routing safeguards only when using OpenRouter.

    OpenRouter recommends ``require_parameters`` when a request depends on
    provider-specific capabilities such as JSON-schema structured output. The
    option must not be sent to the native OpenAI API, so it is derived from the
    configured client's base URL rather than from the model name.
    """
    base_url = str(getattr(client, "base_url", ""))
    if "openrouter.ai" in base_url.lower():
        return {"extra_body": {"provider": {"require_parameters": True}}}
    return {}


def _request_proposal(
    client: OpenAI, model: str, messages: list[dict[str, str]]
) -> HypothesisProposal:
    """Request and parse a structured proposal with one bounded parse retry.

    ``chat.completions.parse`` performs Pydantic parsing inside the OpenAI
    Python SDK. A provider can therefore return HTTP 200 while local parsing
    raises ``pydantic.ValidationError``. That is model-output invalidity, not
    an application 500. We retry that *non-persisting* diagnosis request once;
    if both attempts are malformed, the deterministic API boundary returns the
    existing ``AI_OUTPUT_REJECTED`` error and no hypothesis is written.
    """
    parse_kwargs = _provider_parse_kwargs(client)
    last_invalid: BaseException | None = None

    for attempt in range(STRUCTURED_OUTPUT_MAX_ATTEMPTS):
        try:
            completion = client.chat.completions.parse(
                messages=messages,
                model=model,
                response_format=HypothesisProposal,
                **parse_kwargs,
            )
        except PydanticValidationError as exc:
            last_invalid = exc
            if attempt + 1 < STRUCTURED_OUTPUT_MAX_ATTEMPTS:
                continue
            raise DiagnosisOutputInvalidError(
                "AI structured output failed schema parsing after the bounded retry"
            ) from exc
        except OpenAIError as exc:
            raise DiagnosisError(
                f"OpenAI request failed during diagnosis: {exc}"
            ) from exc

        parsed: object = None
        choices = getattr(completion, "choices", None)
        if choices:
            message = getattr(choices[0], "message", None)
            parsed = getattr(message, "parsed", None)

        if parsed is None:
            last_invalid = DiagnosisOutputInvalidError(
                "OpenAI structured output did not produce a parsed proposal"
            )
            if attempt + 1 < STRUCTURED_OUTPUT_MAX_ATTEMPTS:
                continue
            raise DiagnosisOutputInvalidError(
                "AI structured output did not produce a parsed proposal after the bounded retry"
            ) from last_invalid

        if not isinstance(parsed, HypothesisProposal):
            try:
                parsed = HypothesisProposal.model_validate(parsed)
            except PydanticValidationError as exc:
                last_invalid = exc
                if attempt + 1 < STRUCTURED_OUTPUT_MAX_ATTEMPTS:
                    continue
                raise DiagnosisOutputInvalidError(
                    "AI structured output failed schema validation after the bounded retry"
                ) from exc
        return parsed

    # Defensive only: every branch above returns or raises.
    raise DiagnosisOutputInvalidError(
        "AI structured output could not be validated"
    ) from last_invalid


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist_hypothesis(
    db: Session,
    *,
    opportunity: Opportunity,
    proposal: HypothesisProposal,
    ai_model: str,
) -> Hypothesis:
    """Persist a validated proposal as a Hypothesis ORM row.

    Adds and flushes, but never commits - the caller controls the transaction.
    """
    diagnosis = proposal.diagnosis.strip()
    if len(diagnosis) + len(proposal.reasoning_summary) > _COMBINED_REASONING_MAX_LENGTH:
        budget = max(
            1,
            _COMBINED_REASONING_MAX_LENGTH
            - len(proposal.reasoning_summary)
            - len("Diagnosis:  | Rationale: "),
        )
        diagnosis = diagnosis[:budget].rstrip() + "…"

    reasoning_summary = (
        f"Diagnosis: {diagnosis} | Rationale: {proposal.reasoning_summary}"
    )

    hypothesis = Hypothesis(
        opportunity_id=opportunity.id,
        merchant_id=opportunity.merchant_id,
        ai_model=ai_model,
        hypothesis_text=proposal.hypothesis_text,
        intervention_type=proposal.intervention_type,
        intervention_params=dict(proposal.intervention_params),
        confidence=proposal.confidence,
        reasoning_summary=reasoning_summary,
        evidence_refs=list(proposal.evidence_refs),
        status="proposed",
    )
    db.add(hypothesis)
    db.flush()
    record_audit_event_once(
        db,
        merchant_id=opportunity.merchant_id,
        event_type=AI_DIAGNOSIS_CREATED,
        entity_type=ENTITY_HYPOTHESIS,
        entity_id=hypothesis.id,
        data={"ai_model": ai_model},
        actor=ACTOR_AI,
    )
    record_audit_event_once(
        db,
        merchant_id=opportunity.merchant_id,
        event_type=HYPOTHESIS_PROPOSED,
        entity_type=ENTITY_HYPOTHESIS,
        entity_id=hypothesis.id,
        data={
            "intervention_type": hypothesis.intervention_type,
            "confidence": hypothesis.confidence,
        },
        actor=ACTOR_AI,
    )
    return hypothesis


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _resolve_allowed_interventions(
    policy: MerchantPolicy | None,
) -> set[str]:
    """Resolve the allowed intervention set with fail-closed semantics."""
    if policy is None:
        return set(INTERVENTION_TYPE_SET)
    listed = policy.allowed_interventions
    if not isinstance(listed, (list, tuple, set, frozenset)):
        return set()
    return {item for item in listed if item in INTERVENTION_TYPE_SET}


def diagnose_opportunity(
    db: Session,
    opportunity_id: str,
    *,
    client: OpenAI | None = None,
) -> Hypothesis:
    """Diagnose one Opportunity and persist a validated Hypothesis.

    Never commits - the caller controls the transaction. Never persists
    anything when validation fails or when no interventions are enabled.
    """
    opportunity = db.get(Opportunity, opportunity_id)
    if opportunity is None:
        raise DiagnosisError(f"Opportunity not found: {opportunity_id!r}")

    existing = (
        db.query(Hypothesis)
        .filter(Hypothesis.opportunity_id == opportunity.id)
        .filter(Hypothesis.status == ACTIVE_HYPOTHESIS_STATUS)
        .order_by(Hypothesis.created_at.desc())
        .first()
    )
    if existing is not None:
        return existing

    settings = get_settings()

    policy = (
        db.query(MerchantPolicy)
        .filter(MerchantPolicy.merchant_id == opportunity.merchant_id)
        .first()
    )
    allowed_interventions = _resolve_allowed_interventions(policy)
    if not allowed_interventions:
        raise DiagnosisConfigurationError(
            "No interventions are enabled for this merchant."
        )

    evidence_catalog = build_evidence_catalog(opportunity)
    memory_trials = build_diagnosis_memory(
        db,
        opportunity,
        evidence_catalog,
        evidence_catalog_builder=build_evidence_catalog,
    )
    memory_payload = prompt_memory_payload(memory_trials)

    if client is None:
        if not settings.OPENAI_API_KEY:
            raise DiagnosisConfigurationError(
                "OPENAI_API_KEY is not configured; cannot run AI diagnosis "
                "without an injected client"
            )
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

    messages = _build_prompt_messages(
        opportunity,
        evidence_catalog,
        allowed_interventions,
        memory_payload,
    )
    accepted_proposal: HypothesisProposal | None = None
    last_repeat_reason: str | None = None
    for attempt in range(MEMORY_PROPOSAL_MAX_ATTEMPTS):
        proposal = _request_proposal(client, settings.OPENAI_MODEL, messages)
        proposal = validate_proposal(
            proposal,
            evidence_catalog,
            allowed_interventions,
        )
        repeat_reason = stale_repeat_reason(
            memory_trials,
            intervention_type=proposal.intervention_type,
            intervention_params=proposal.intervention_params,
        )
        if repeat_reason is None:
            accepted_proposal = proposal
            break

        last_repeat_reason = repeat_reason
        if attempt + 1 >= MEMORY_PROPOSAL_MAX_ATTEMPTS:
            break
        messages = [
            *messages,
            {
                "role": "user",
                "content": (
                    "Deterministic memory validation rejected the proposed exact "
                    f"repeat: {repeat_reason}. Propose a materially different "
                    "semantic configuration or a different allowed intervention. "
                    "Continue to use only evidence_catalog and do not invent metrics."
                ),
            },
        ]

    if accepted_proposal is None:
        raise DiagnosisOutputInvalidError(
            "AI proposal repeats blocked prior experiment: "
            f"{last_repeat_reason or 'stale experiment history'}"
        )

    return persist_hypothesis(
        db,
        opportunity=opportunity,
        proposal=accepted_proposal,
        ai_model=settings.OPENAI_MODEL,
    )
