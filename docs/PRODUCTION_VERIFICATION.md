# Production Verification Record

This document records the final hosted release verification for Merchant Revenue Autopilot.

It is deliberately separate from the synthetic benchmark. The benchmark evaluates strategy behavior in a frozen causal world; this verification checks that the deployed product lifecycle actually behaves as designed across Vercel, Render, Supabase, the hosted LLM boundary, the simulated Razorpay execution adapter, runtime, statistics, and audit history.

## Release surfaces

- Frontend: https://merchant-revenue-autopilot-psi.vercel.app
- Backend health: https://merchant-revenue-autopilot-api.onrender.com/health
- Merchant: `merchant_techbazaar`
- Verified opportunity: `a1761032-0637-40f3-8a44-38e02242683f`
- Verified experiment: `a4cc6504-493c-4d97-a27e-e61a7145290f`
- Hosted execution mode: simulated
- Verified demo resource: `demo_plink_8b6f752dd2126b8a`

## Why there are two verification runs

The first guarded production verification was intentionally allowed to mutate exactly one new optimization cycle. It successfully preserved the original completed cycle and created opportunity `a1761032-0637-40f3-8a44-38e02242683f`.

Its first Autopilot step then exposed a production-only issue at the OpenAI-compatible structured-output boundary: provider output parsing could raise a local Pydantic validation exception that was not yet mapped into the diagnosis error model. The request returned a sanitized HTTP 500.

The failure happened before any hypothesis, experiment, policy decision, or payment resource for the new cycle was persisted. The live state remained at `HYPOTHESIS_PENDING`, with the earlier cycle preserved and the audit chain valid.

The diagnosis boundary was repaired and regression-tested so that provider-side structured parse failures fail closed as AI-output errors. One bounded retry is allowed only at this pre-persistence diagnosis boundary. No financial/execution operation gained retry behavior.

The production verifier was then made interruption-safe so it could resume the exact persisted opportunity rather than create a third cycle.

## Successful resume verification

Trigger commit:

`cd931dab6b811afc7b18e29fc54613d9e2de911b`

Trigger marker:

`[production-verify-resume:a1761032-0637-40f3-8a44-38e02242683f]`

Production Journey Verification run:

https://github.com/harsh2025-sketch/razorpay-merchant-revenue-autopilot/actions/runs/33303163100

The exact-ID resume completed successfully.

### Safety check before continuation

Attempting to start another cycle while `a1761032...` was active returned:

`HTTP 409 INVALID_TRANSITION`

This proves the interrupted cycle could not be skipped by rollover.

### Verified lifecycle

The public one-step orchestrator advanced one legal transition per request:

```text
HYPOTHESIS_PROPOSED
  -> EXPERIMENT_PLANNED
  -> POLICY_APPROVED
  -> RESOURCE_DEPLOYED
  -> EXPERIMENT_BATCH_RUN
  -> EXPERIMENT_BATCH_RUN
  -> EXPERIMENT_BATCH_RUN
  -> EXPERIMENT_BATCH_RUN
  -> EXPERIMENT_EVALUATED
```

After resource deployment, rollover protection was tested again and returned the same `409 INVALID_TRANSITION` refusal while the experiment was active.

## Final persisted result

The live LLM proposed:

- intervention: `partial_payment`
- `accept_partial: true`
- minimum first payment: 25%
- confidence: medium

The deterministic planner created:

- treatment exposure: 10%
- primary metric: conversion rate
- minimum sample: 200 per variant
- maximum duration: 72 hours
- guardrails: captured GMV, failure rate, abandonment rate

Merchant policy returned `APPROVE`.

The hosted simulated execution boundary created:

`demo_plink_8b6f752dd2126b8a`

The UI explicitly identifies this as a simulated Payment Link, states that no Razorpay API request was made, and states that the resource does not exist in the Razorpay dashboard.

Fixed-horizon statistics produced:

| Metric | Result |
| --- | ---: |
| Control samples | 1,795 |
| Treatment samples | 200 |
| Control conversions | 842 |
| Treatment conversions | 92 |
| Control conversion | 46.9% |
| Treatment conversion | 46.0% |
| Absolute lift | -0.9 pp |
| Relative lift | -1.9% |
| p-value | 0.8071 |
| 95% CI | -8.2 pp to +6.4 pp |
| Significant | No |
| Decision | `INCONCLUSIVE` |

The LLM did not participate in that decision.

## History and audit checks

The verification confirmed:

- the previous opportunity `870d7535-5ad0-461a-b7f3-554887251ff7` remained persisted
- the previous statistical result and policy decision were not changed
- opportunity count did not increase during resume
- the new experiment completed rather than remaining stuck
- only a `demo_...` hosted payment resource was created
- the merchant audit chain remained valid
- both completed cycles are visible in the Autopilot history
- both cycle histories are visible in the Audit Log

## Frontend release verification

After the successful production journey, the live Vercel pages were fetched independently:

- `/overview` returned HTTP 200 and displayed `Cycle complete`, `INCONCLUSIVE`, `Demo Mode`, and audit integrity `Verified`
- `/autopilot` returned HTTP 200 and displayed both preserved cycles, with `a1761032...` labelled `Latest cycle`
- `/autopilot/a1761032-0637-40f3-8a44-38e02242683f` returned HTTP 200 and displayed the observed evidence, LLM proposal, deterministic plan, policy approval, simulated resource, statistical result, and verified audit state
- `/audit` returned HTTP 200 and displayed the hash-chained history for both cycles with integrity `Verified`

The Vercel production deployment for the final verification trigger was `READY`, and Vercel reported no frontend runtime errors in the checked one-hour release window.

## What this verification does not claim

- It does not claim that `demo_plink_...` is a real Razorpay resource.
- It does not claim production merchant uplift.
- It does not convert the synthetic benchmark into a revenue claim.
- It does not prove the audit chain is a blockchain or an external immutable ledger.
- It does not replace real merchant onboarding or Razorpay Test Mode verification once suitable merchant credentials are available.

The purpose of this record is narrower: prove that the hosted decision pipeline is repeatable, fail-closed, recoverable after an interrupted non-financial boundary, history-preserving, and truthful about its simulated execution mode.
