# Final Submission Copy

## Project name

**Merchant Revenue Autopilot**

## One-line pitch

A controlled AI revenue-optimization system for Razorpay merchants where AI proposes experiments from payment evidence, deterministic policy authorizes them, the Razorpay boundary executes them, fixed-horizon statistics decides the outcome, and persisted memory shapes the next cycle.

## Problem

Merchants can see that payment conversion is weak without knowing which segment is leaking, which payment configuration is worth testing, how to bound the blast radius, or whether the change actually helped.

A naive AI agent makes this more dangerous if the same probabilistic component can choose, authorize, execute, repeat, and judge commercial changes.

## Solution

Revenue Autopilot deliberately separates those responsibilities:

1. deterministic metric computation and opportunity detection;
2. deterministic opportunity portfolio ranking;
3. structured terminal experiment memory;
4. evidence-grounded LLM hypothesis generation;
5. deterministic schema/evidence/semantic/stale-repeat validation;
6. champion-aware deterministic planning;
7. deterministic merchant-policy authorization;
8. duplicate-safe Razorpay execution;
9. fixed-horizon experiment measurement and statistics;
10. KEEP-derived champion state;
11. history-preserving repeat cycles;
12. hash-chained merchant audit history.

> **AI proposes. Deterministic policy authorizes. The execution boundary acts. Statistics decides. Persisted outcomes shape the next cycle.**

## Adaptive learning

Terminal learning is reconstructed from persisted experiments, policy decisions, statistical results, and execution resources rather than stored as opaque chat history.

The system uses that history to:

- down-rank repeatedly tested opportunities;
- block exact stale failed/inconclusive proposals unless observable evidence materially changes;
- keep exact policy-rejected configurations blocked;
- derive Champion vN from real `KEEP` results;
- use the current champion as the future control.

No hosted treatment has earned KEEP, so Champion v1 correctly remains the merchant baseline.

## Merchant data path

A merchant can be created from canonical CSV payment history and can append later transaction revisions with deterministic deduplication.

Uploaded merchant evidence may feed metrics, detection, ranking, diagnosis, planning, and merchant policy.

### Real-merchant measurement boundary

Uploaded merchants are **not** evaluated with TechBazaar's hidden causal simulator. At experiment measurement time the product enters **Awaiting live outcomes** and returns `LIVE_EXPERIMENT_TRAFFIC_REQUIRED` rather than fabricating treatment lift or a p-value.

A production checkout/payment-event integration must provide authoritative assigned control/treatment outcomes before the existing statistical engine evaluates that merchant's experiment.

## Razorpay integration

The repository contains a real Razorpay Test Mode HTTP client and deterministic executor for Payment Links and Orders, including:

- payment-method configuration;
- partial payment;
- expiry configuration;
- verified existing Offer association where supported;
- resource fetch;
- cancellation/rollback;
- application-level idempotency;
- fail-closed ambiguous-write handling.

The executor accepts only a persisted merchant-policy `APPROVE`; the LLM cannot call it directly.

### Hosted execution evidence

The hosted history contains both simulated and real-path evidence.

Earlier cycles deliberately used the simulated adapter and generated `demo_plink_*` IDs, including `demo_plink_0a6348797891d7c8`.

A later hosted cycle, opportunity `2956c570-9504-40b6-9557-372fe7455ccc`, persisted and currently renders:

```text
experiment: 83b571f0-3520-4435-b279-7fad1f4e0efb
resource:   plink_TW3blQWQpXXHRL
policy:     APPROVE
resource:   Razorpay Test Mode Payment Link
```

In this codebase simulated execution creates `demo_plink_*`. The persisted plain `plink_*` is therefore **application-side evidence that the real Razorpay Test Mode client path returned a resource** for that approved hosted cycle.

The same cycle reached its fixed horizon and returned `INCONCLUSIVE` with p ≈ 0.9917. Experimental customer traffic remained simulated separately.

### What is still pending

The application record is not independent external verification. Before claiming the external Razorpay proof is fully closed, the same `plink_TW3blQWQpXXHRL` should be confirmed in the Razorpay Test Mode dashboard/API.

The repository also includes a stronger credential-gated proof harness:

```text
python scripts/verify_razorpay_autopilot.py
```

It performs:

```text
persisted plan
  -> deterministic policy APPROVE
  -> real executor create
  -> independent Razorpay fetch
  -> unchanged fixed-horizon harmful fixture
  -> genuine ROLLBACK
  -> executor cancellation
  -> independent cancellation fetch
  -> audit verification
```

It refuses live-mode keys, uses a temporary local database, and attempts cleanup if a resource remains active after failure. Its domain chain is covered by offline CI, but offline CI is not external Test Mode proof.

## One-click experiment interaction

For the canonical TechBazaar evaluation merchant, runtime/evaluation is exposed as one bounded `Run Experiment` action rather than repeated batch clicks.

This does not weaken safety boundaries: persisted policy APPROVE and a deployed treatment are still required; Task 11 remains the only synthetic runtime; Task 12 remains the only statistical authority; explicit external rollback remains separate; completed calls are idempotent.

Uploaded merchants do not receive this synthetic outcome path.

## Frozen evaluation

A deterministic benchmark compares no optimization, random intervention, a rule baseline, and Autopilot on identical paired synthetic contexts.

| Strategy | Mean conversion | Mean delta vs control |
| --- | ---: | ---: |
| **Autopilot** | **59.39%** | **+1.22 pp** |
| Random intervention | 59.20% | +1.02 pp |
| No optimization | 58.18% | 0.00 pp |
| Rule based | 57.65% | -0.52 pp |

These are reproducible **synthetic evaluation results**, not production revenue claims.

## Current read-only hosted observation

The final production smoke returned HTTP 200 across the judge-facing product routes and observed:

- 14,115 accumulated payment attempts;
- Champion v1;
- 5 terminal trials / 4 statistical results;
- 0 promoted treatments;
- 4 INCONCLUSIVE statistical results;
- audit integrity Verified;
- latest experiment `83b571f0-3520-4435-b279-7fad1f4e0efb`;
- latest Test Mode resource `plink_TW3blQWQpXXHRL`;
- latest fixed-horizon result approximately 48.46% control vs 48.5% treatment, p ≈ 0.9917, `INCONCLUSIVE`.

These are point-in-time live observations, not frozen benchmark constants.

## Why this is not "LLM + dashboard"

The contribution is the control and learning architecture around the probabilistic component:

- deterministic evidence and prioritization;
- structured experiment memory;
- stale-repeat prevention;
- champion–challenger progression;
- deterministic merchant authorization;
- duplicate-safe external execution;
- fixed-horizon statistical decisions;
- explicit real-vs-synthetic measurement separation;
- history-preserving cycles;
- tamper-evident audit.

## Current limitations

- the live application contains application-side evidence of a real Test Mode `plink_*`, but independent dashboard/API confirmation of that exact resource has not been recorded in the repository;
- the full credential-gated create→fetch→ROLLBACK→cancel proof still requires a real `rzp_test_*` run;
- uploaded merchants do not yet have authoritative checkout assignment and Razorpay payment-event/webhook ingestion for real experimental outcomes;
- authentication, tenant authorization, and merchant credential isolation are not production-complete;
- scheduled/background experiment progression is not production-complete;
- synthetic evaluation does not establish external validity across Razorpay merchants;
- the audit chain is application-level tamper evidence, not a blockchain.

## What comes next

1. independently confirm the hosted `plink_*` and complete the controlled Test Mode cancellation proof;
2. authoritative control/treatment assignment at checkout;
3. Razorpay payment-event/webhook ingestion bound to persisted assignments;
4. authentication + tenant/credential isolation;
5. scheduled experiment progression and external-write reconciliation;
6. consented real-merchant evaluation before production uplift claims.

## Live links

Frontend: https://merchant-revenue-autopilot-psi.vercel.app  
Merchant Intelligence: https://merchant-revenue-autopilot-psi.vercel.app/intelligence  
Backend health: https://merchant-revenue-autopilot-api.onrender.com/health

## Documentation

- `docs/DEMO_SCRIPT.md`
- `docs/JUDGE_QA.md`
- `docs/RAZORPAY_TEST_MODE_PROOF.md`
- `docs/PRODUCTION_VERIFICATION.md`
- `docs/task-22-e2e-boundaries.md`
- `docs/evaluation/summary.md`
