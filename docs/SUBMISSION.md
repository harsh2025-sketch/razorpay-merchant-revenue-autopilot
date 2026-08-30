# Final Submission Copy

## Project name

**Merchant Revenue Autopilot**

## One-line pitch

A controlled AI revenue-optimization system for Razorpay merchants where AI proposes experiments from payment evidence, deterministic policy authorizes them, the Razorpay boundary executes them, fixed-horizon statistics decides the outcome, and persisted memory shapes the next cycle.

## Short description

Merchant Revenue Autopilot turns payment-conversion evidence into bounded optimization cycles. It detects conversion leakage, ranks eligible opportunities, gives an evidence-grounded LLM structured prior experiment knowledge, converts the proposal into a champion–challenger experiment, applies deterministic merchant policy, executes through an explicit Razorpay boundary, measures the result at a fixed sample horizon, and learns from the terminal outcome without deleting prior history.

The central design decision is separation of authority. The LLM cannot call Razorpay, cannot bypass merchant limits, cannot choose its own statistical rule, cannot repeat an unchanged failed idea without passing deterministic memory checks, and cannot decide whether its treatment succeeded.

## Problem

Payment behavior differs across customer segments, devices, order values, and payment methods. A merchant may see weak conversion but still has to manually determine where the problem exists, guess what configuration to change, expose customers to the change, and decide whether it helped.

A naive AI agent makes this riskier if the same probabilistic component is allowed to choose, authorize, execute, repeat, and judge commercial changes.

## Solution

Revenue Autopilot separates the loop into explicit trust boundaries:

1. deterministic metric computation and opportunity detection;
2. deterministic opportunity portfolio ranking;
3. structured terminal experiment memory;
4. evidence-grounded LLM hypothesis generation;
5. deterministic schema, evidence, semantic, and stale-repeat validation;
6. champion-aware deterministic experiment planning;
7. deterministic merchant-policy authorization;
8. duplicate-safe Razorpay execution boundary;
9. fixed-horizon experiment measurement and statistics;
10. KEEP-derived champion state;
11. history-preserving repeat cycles;
12. hash-chained merchant audit history.

The operating principle is:

> **AI proposes. Deterministic policy authorizes. The execution boundary acts. Statistics decides. Persisted outcomes shape the next cycle.**

## Adaptive learning layer

### Structured experiment memory

The system reconstructs merchant learning from persisted experiments, policy decisions, statistical results, and execution resources rather than storing opaque chat history.

Terminal memory includes treatment configuration, policy outcome, statistical decision, lift, p-value, execution state, and prior trial count. Active work is excluded until it reaches a safe terminal boundary.

### Deterministic opportunity portfolio

Eligible untouched opportunities are ranked from observable conversion gap, affected volume, captured average order value, policy feasibility, and prior terminal-trial history.

The displayed GMV value is explicitly an **opportunity-sizing proxy**. It is not realized revenue, profit, or a causal forecast.

### Memory-aware diagnosis

The LLM receives compact prior experiment history for the affected segment. Deterministic code blocks exact stale policy-rejected or unchanged failed/inconclusive proposals before persistence. Reconsideration requires explicit material evidence change.

### Champion–challenger progression

Champion v1 is the merchant baseline. Only a fixed-horizon statistical `KEEP` can promote a treatment. Future experiments of the same intervention type inherit the promoted configuration as control. `ROLLBACK` and `INCONCLUSIVE` retain the previous champion.

### Merchant Intelligence

The `/intelligence` surface exposes persisted deterministic truth: champion state, terminal trial counts, learned segment/intervention history, policy rejections, recent outcomes, and the opportunity portfolio when eligible candidates exist.

React does not recompute those decisions.

## Merchant data path

The product now supports a production-facing merchant data entry path in addition to the canonical TechBazaar demo merchant.

A merchant can:

- be created from canonical CSV payment history;
- append later payment-data revisions;
- deduplicate repeated external records deterministically;
- preserve historical observations rather than replaying them as new evidence;
- run metrics, detection, opportunity ranking, diagnosis, planning, and policy on the uploaded evidence.

### Real-merchant experiment boundary

Uploaded merchants are **not** allowed to enter TechBazaar's sealed causal simulator for experimental outcomes.

After an uploaded merchant reaches the measurement stage, the system enters **Awaiting live outcomes** and returns the stable boundary `LIVE_EXPERIMENT_TRAFFIC_REQUIRED` rather than fabricating a treatment lift or p-value.

A production checkout/payment-event integration must provide authoritative assigned control and treatment outcomes before the existing statistics engine evaluates that merchant's experiment.

This separation is deliberate: the TechBazaar hidden causal world is evaluation infrastructure, not evidence about arbitrary merchants.

## One-click experiment interaction

For the canonical TechBazaar evaluation merchant, the merchant UI now exposes one bounded `Run Experiment` action instead of requiring repeated batch clicks.

The one-click orchestration does not weaken the architecture:

- persisted policy `APPROVE` is required;
- an active deployed treatment resource is required;
- the existing Task 11 runtime remains the only synthetic runtime;
- the existing Task 12 statistics engine remains the only decision authority;
- explicit rollback remains a separate external mutation;
- repeated calls after a result are idempotent.

Uploaded merchants do not receive this synthetic runtime path.

## Razorpay integration

The repository implements a real Razorpay Test Mode client and executor for Payment Links and Orders, including:

- payment-method configuration;
- partial payment;
- expiry configuration;
- verified existing Offer association where supported;
- independent resource fetch;
- cancellation/rollback;
- application-level idempotency;
- fail-closed handling of ambiguous external writes.

The executor never reruns policy and never lets the LLM emit raw Razorpay request payloads.

### Hosted demo disclosure

The public hosted demo currently uses:

```text
RAZORPAY_EXECUTION_MODE=simulated
```

Hosted simulated resources use `demo_...` IDs and are explicitly described as simulated. They are not claimed to exist in the Razorpay dashboard.

### Controlled real Test Mode proof

The repository includes:

```text
scripts/verify_razorpay_autopilot.py
```

This credential-gated verifier uses a temporary local database and exercises:

```text
persisted plan
  -> deterministic policy APPROVE
  -> real executor deploy
  -> real Razorpay Test Mode Payment Link
  -> persisted resource + operation ledger
  -> independent Razorpay fetch
  -> unchanged fixed-horizon runtime/statistics
  -> genuine ROLLBACK on the controlled harmful-expiry fixture
  -> real executor cancellation
  -> independent cancellation verification
  -> audit-chain verification
```

It refuses live-mode keys and attempts cleanup of any still-active Test Mode resource.

The proof harness has an offline CI regression using a fake Razorpay client while retaining the real planner, policy, executor persistence/idempotency, runtime, statistics, rollback, and audit code paths.

**Truthfulness rule:** the existence of this harness and its offline CI test is not itself evidence that a real Test Mode call was completed. Claim "real Razorpay Test Mode proof verified" only after running it with actual `rzp_test_...` credentials and independently observing the matching `plink_...` resource. See `docs/RAZORPAY_TEST_MODE_PROOF.md`.

## AI usage

The diagnosis boundary uses an OpenAI-compatible SDK. The hosted deployment is routed through OpenRouter.

The model receives observable evidence plus compact prior experiment knowledge and returns a structured hypothesis. Output is validated against schema, evidence references, semantic intervention constraints, and merchant memory before persistence.

Provider-side structured-output failures fail closed. A bounded diagnosis-only retry is permitted because no experiment or payment mutation exists at that point.

## Frozen evaluation

A separate deterministic benchmark compares:

- no optimization;
- random intervention;
- rule-based intervention;
- Autopilot.

Canonical benchmark: five fixed seeds, five segments, 5,000 paired contexts per segment per seed.

| Strategy | Mean conversion | Mean delta vs control |
| --- | ---: | ---: |
| Autopilot | 59.39% | +1.22 pp |
| Random intervention | 59.20% | +1.02 pp |
| No optimization | 58.18% | 0.00 pp |
| Rule based | 57.65% | -0.52 pp |

Autopilot recorded policy rejections instead of forcing every proposal into deployment.

These are reproducible **synthetic evaluation results**, not evidence of production revenue lift across Razorpay merchants.

## Preserved hosted verification snapshot

Task 20 exercised the adaptive hosted journey with three preserved terminal experiments.

Final preserved cycle:

- opportunity: `0e500ccd-6c3d-4ade-a06c-afc3d2cd24e6`
- experiment: `5277a2df-c1a5-4009-9320-c97c3576ff38`
- intervention: payment-method configuration on `android_budget`
- hosted resource: `demo_plink_0a6348797891d7c8`
- control conversion: 47.2%
- treatment conversion: 45.5%
- absolute lift: -1.7 pp
- p-value: 0.6412
- decision: `INCONCLUSIVE`
- champion after result: v1 / merchant baseline
- audit integrity: valid

This snapshot is useful because the system did **not** manufacture a successful result. Three hosted terminal experiments remained INCONCLUSIVE, so no treatment was promoted.

Treat those values as the **Task 20 verification snapshot**, not permanent live counters.

## Safety and failure behavior

The system fails closed when, among other cases:

- structured model output is malformed;
- evidence references are invalid;
- an unchanged stale failed/inconclusive proposal is repeated;
- an exact policy-rejected configuration is repeated;
- an intervention is not allowed;
- merchant exposure or discount limits are exceeded;
- a verified Offer mapping is unavailable;
- external write state is ambiguous;
- the experiment has not reached its fixed horizon;
- a caller attempts to skip an active/deployed cycle;
- a challenger is identical to the current champion;
- an uploaded merchant attempts to enter the TechBazaar synthetic measurement runtime.

No unsafe proposal is silently rewritten into an approved one.

## Why this is not "LLM + dashboard"

The contribution is the control and learning architecture around the probabilistic component:

- deterministic observable-evidence pipeline;
- opportunity prioritization outside the model;
- structured experiment memory;
- stale-repeat prevention;
- champion–challenger progression;
- deterministic merchant authorization;
- duplicate-safe external execution;
- fixed-horizon statistical decisions;
- real-vs-synthetic measurement separation;
- history-preserving optimization cycles;
- hash-chained audit;
- a judge-visible Intelligence surface exposing actual learned state.

## Current limitations

- the public hosted Razorpay adapter is simulated;
- a real Test Mode proof should only be claimed after the credential-gated verifier actually passes with a matching observed Test Mode resource;
- uploaded merchants do not yet have authoritative checkout assignment + Razorpay payment-event/webhook ingestion for live experiment outcomes;
- authentication, tenant authorization, and merchant credential management are not production-complete;
- scheduled/background experiment progression is not production-complete;
- the synthetic benchmark establishes reproducibility, not external validity on Razorpay's merchant population;
- the hosted snapshot has no promoted treatment because all three preserved trials were INCONCLUSIVE;
- the audit chain is application-level tamper evidence, not distributed consensus.

## What comes next in production

1. authoritative checkout assignment and Razorpay payment-event/webhook ingestion;
2. authentication and tenant/credential isolation;
3. scheduled/background experiment progression;
4. external-write reconciliation;
5. stronger concurrent database controls;
6. consented real-merchant evaluation before production uplift claims.

Merchant CSV onboarding, incremental append/dedup, one-click canonical experiment interaction, and real-vs-synthetic measurement separation are already implemented.

## Live links

Frontend:  
https://merchant-revenue-autopilot-psi.vercel.app

Merchant Intelligence:  
https://merchant-revenue-autopilot-psi.vercel.app/intelligence

Backend health:  
https://merchant-revenue-autopilot-api.onrender.com/health

Evaluation summary:  
`docs/evaluation/summary.md`

Production verification history:  
`docs/PRODUCTION_VERIFICATION.md`

Controlled Razorpay Test Mode proof:  
`docs/RAZORPAY_TEST_MODE_PROOF.md`

Final demo script:  
`docs/DEMO_SCRIPT.md`

Judge Q&A:  
`docs/JUDGE_QA.md`

## Stack

Frontend: Next.js App Router, TypeScript, Tailwind CSS, Recharts, Lucide

Backend: Python 3.12, FastAPI, SQLAlchemy 2, Pydantic v2, httpx, OpenAI-compatible SDK

Database: Supabase PostgreSQL hosted; SQLite supported locally

Hosting: Vercel frontend, Render backend

Testing: pytest, Vitest, Testing Library, ESLint, TypeScript typecheck, Next.js production build, guarded production verification, and an offline executor-proof regression.
