# Production Verification Record

This document records hosted release verification for Merchant Revenue Autopilot. It is deliberately separate from the frozen synthetic benchmark.

The benchmark evaluates strategy behavior in a controlled causal world. Production verification checks whether the deployed product actually preserves its trust boundaries across Vercel, Render, Supabase, the hosted LLM boundary, the simulated Razorpay execution adapter, experiment runtime, statistics, adaptive memory, champion state, and audit history.

## Release surfaces

- Frontend: https://merchant-revenue-autopilot-psi.vercel.app
- Merchant Intelligence: https://merchant-revenue-autopilot-psi.vercel.app/intelligence
- Backend health: https://merchant-revenue-autopilot-api.onrender.com/health
- Merchant: `merchant_techbazaar`
- Hosted execution mode: simulated

---

# Final adaptive verification — Task 20

## Trigger

Implementation merge:

`0c25ad618253ca1b2ac397a99b4c2317fb072936`

Production verification trigger:

`30a5f5f0028f802f151bfa72a7629366a7f2effa`

Trigger marker:

`[production-verify-v2]`

Production Journey Verification run:

https://github.com/harsh2025-sketch/razorpay-merchant-revenue-autopilot/actions/runs/33308795917

Result:

`TASK 20: PASS`

## Preflight state

Before the write-capable Task 20 run:

- terminal experiment memory contained 2 trials
- both previous statistical outcomes were `INCONCLUSIVE`
- Champion was v1 / merchant baseline
- no treatment had earned KEEP promotion
- audit chain was valid
- no untouched active opportunity was available for portfolio ranking

Because the portfolio had no eligible untouched candidate, rollover correctly proceeded to fresh deterministic detection instead of pretending to rank nonexistent choices.

## New verified cycle

Opportunity:

`0e500ccd-6c3d-4ade-a06c-afc3d2cd24e6`

Experiment:

`5277a2df-c1a5-4009-9320-c97c3576ff38`

Segment:

`android_budget`

Observed evidence at detection:

| Metric | Value |
| --- | ---: |
| Segment attempts | 6,207 |
| Segment captured | 2,936 |
| Segment conversion | 47.3% |
| Comparison attempts | 3,956 |
| Comparison captured | 2,318 |
| Comparison conversion | 58.6% |
| Absolute conversion gap | -11.3 pp |

## Memory-aware diagnosis

The live OpenRouter-backed diagnosis received prior experiment history for the merchant segment.

It explicitly reasoned over earlier `partial_payment` and `payment_method_config` experiments and proposed:

- intervention: `payment_method_config`
- card: enabled
- UPI: enabled
- netbanking: enabled
- wallet: enabled
- confidence: high

A payment-method configuration had previously reached an INCONCLUSIVE terminal result. The memory layer did not simply forget that history. Task 20 verified that reconsideration passed the deterministic material-evidence-change rule before hypothesis persistence.

Verifier assertion:

`memory_aware_hypothesis_verified: true`

## Champion–challenger verification

The merchant entered Task 20 at Champion v1 because no prior treatment had earned KEEP.

The deterministic planner therefore used the merchant baseline as control and the new proposal as challenger.

Control:

```json
{"payment_methods":"merchant_default"}
```

Treatment:

```json
{"payment_methods":{"card":true,"upi":true,"netbanking":true,"wallet":true}}
```

Verifier assertion:

`champion_control_verified: true`

## Deterministic plan and authorization

The planner created:

- treatment exposure: 10%
- primary metric: conversion rate
- minimum sample: 200 per variant
- maximum duration: 72 hours
- guardrails: captured GMV, failure rate, abandonment rate

Merchant policy returned:

`APPROVE`

## Hosted execution

The explicitly simulated execution boundary created:

`demo_plink_0a6348797891d7c8`

The UI labels it **Simulated Payment Link** and states that no Razorpay API request was made and that the resource does not exist in the Razorpay dashboard.

Task 20 also attempted cycle rollover while the experiment was active/deployed. The request was rejected with:

`HTTP 409 INVALID_TRANSITION`

Verifier assertion:

`skip_guard_verified: true`

## Verified lifecycle

The public one-step orchestrator advanced exactly one legal transition per request:

```text
HYPOTHESIS_PROPOSED
  -> EXPERIMENT_PLANNED
  -> POLICY_APPROVED
  -> RESOURCE_DEPLOYED
  -> EXPERIMENT_BATCH_RUN
  -> EXPERIMENT_BATCH_RUN
  -> EXPERIMENT_BATCH_RUN
  -> EXPERIMENT_BATCH_RUN
  -> EXPERIMENT_BATCH_RUN
  -> EXPERIMENT_EVALUATED
```

## Fixed-horizon result

| Metric | Result |
| --- | ---: |
| Control samples | 1,895 |
| Treatment samples | 200 |
| Control conversions | 895 |
| Treatment conversions | 91 |
| Control conversion | 47.2% |
| Treatment conversion | 45.5% |
| Absolute lift | -1.7 pp |
| Relative lift | -3.7% |
| p-value | 0.6412 |
| 95% CI | -9.0 pp to +5.5 pp |
| Significant | No |
| Decision | `INCONCLUSIVE` |

The LLM did not participate in this decision.

## Learning and champion assertions

Task 20 verified after the terminal result:

```json
{
  "previous_trial_count": 2,
  "new_trial_count": 3,
  "previous_champion_version": 1,
  "new_champion_version": 1,
  "memory_aware_hypothesis_verified": true,
  "champion_control_verified": true,
  "skip_guard_verified": true,
  "terminal_outcome": "INCONCLUSIVE",
  "audit_chain_valid": true,
  "learning_persisted": true
}
```

The terminal trial was added to merchant memory exactly once.

Because the result was INCONCLUSIVE rather than KEEP, Champion correctly remained v1. No promotion event was fabricated.

## Final Intelligence state

After Task 20 the live `/intelligence` page independently returned HTTP 200 and showed:

- Current Champion: v1
- Promoted treatments: 0
- Terminal trials: 3
- Statistical results: 3
- KEEP: 0
- ROLLBACK: 0
- INCONCLUSIVE: 3
- Policy rejections: 0 in hosted history

Learned history:

- `android_budget / partial_payment`: 1 trial, latest lift about -0.9 pp, INCONCLUSIVE
- `android_budget / payment_method_config`: 2 trials, latest lift about -1.7 pp, both INCONCLUSIVE

No untouched active opportunity remained after the completed cycle.

## Final Overview state

The live `/overview` page independently returned HTTP 200 and showed:

- 12,258 accumulated payment attempts
- completed cycle state
- latest decision INCONCLUSIVE
- audit integrity Verified
- active experiments: 0

The 12,258 live attempts include preserved experimental traffic. The canonical frozen baseline remains 6,112 attempts.

---

# Earlier release verification and recovery evidence

Before the adaptive Task 20 layer, the first guarded hosted verification successfully preserved the original cycle and created a second opportunity:

`a1761032-0637-40f3-8a44-38e02242683f`

Its first Autopilot step exposed a production-only structured-output parsing issue at the OpenAI-compatible boundary. Provider output parsing could raise a local Pydantic validation exception that was not yet mapped into the diagnosis error model, producing a sanitized HTTP 500.

The failure occurred before a hypothesis, experiment, policy decision, or payment resource for the new cycle was persisted. The live state remained safely at `HYPOTHESIS_PENDING`; the prior cycle and audit chain remained intact.

The diagnosis boundary was repaired and regression-tested so provider-side structured parse failures fail closed as AI-output errors. One bounded retry is allowed only at this pre-persistence diagnosis boundary.

The verifier was then made interruption-safe and resumed the exact persisted opportunity rather than creating another cycle.

Successful resume trigger:

`cd931dab6b811afc7b18e29fc54613d9e2de911b`

Successful resume run:

https://github.com/harsh2025-sketch/razorpay-merchant-revenue-autopilot/actions/runs/33303163100

That second cycle completed with:

- intervention: partial payment
- 25% minimum first payment
- policy: APPROVE
- simulated resource: `demo_plink_8b6f752dd2126b8a`
- control conversion: 46.9%
- treatment conversion: 46.0%
- absolute lift: -0.9 pp
- p-value: 0.8071
- decision: INCONCLUSIVE
- audit chain: valid

This failure/recovery history is intentionally retained. The release record is stronger when it shows that a production-only defect was detected before any external action, repaired, regression-tested, and safely resumed.

## What the verification proves

The hosted system has been exercised beyond unit tests and preview builds. The release evidence confirms:

- repeatable, history-preserving cycles
- deterministic opportunity/portfolio semantics
- structured terminal experiment memory
- memory-aware LLM diagnosis
- deterministic stale-repeat constraints
- champion-aware planning
- merchant-policy authorization
- simulated execution disclosure
- active-cycle skip protection
- fixed-horizon statistical decisions
- learning persistence after terminal results
- KEEP-only champion progression semantics
- hash-chain integrity
- recoverability after an interrupted pre-persistence AI boundary

## What this verification does not claim

- It does not claim a `demo_...` resource is a real Razorpay dashboard object.
- It does not claim production merchant uplift.
- It does not convert the synthetic benchmark into a revenue claim.
- It does not claim the hosted merchant advanced its champion; it correctly remains Champion v1.
- It does not claim the portfolio selected among multiple candidates in Task 20; none existed before fresh detection.
- It does not claim the audit chain is a blockchain or external immutable ledger.
- It does not replace real merchant onboarding or Razorpay Test Mode verification once suitable merchant credentials are available.
