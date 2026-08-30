# Razorpay Merchant Revenue Autopilot

**A controlled AI revenue-optimization system where AI proposes experiments from payment evidence, deterministic policy authorizes them, the execution boundary acts, fixed-horizon statistics decides the outcome, and persisted memory shapes the next cycle.**

**Live demo:** https://merchant-revenue-autopilot-psi.vercel.app  
**Merchant Intelligence:** https://merchant-revenue-autopilot-psi.vercel.app/intelligence  
**Backend health:** https://merchant-revenue-autopilot-api.onrender.com/health  
**Demo script:** [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md)  
**Judge Q&A:** [`docs/JUDGE_QA.md`](docs/JUDGE_QA.md)

## Product thesis

Razorpay already processes the merchant's payments. Revenue Autopilot uses that payment evidence to discover where conversion is leaking and safely tests bounded payment configurations.

```text
payment evidence
  -> deterministic metrics + detection
  -> deterministic opportunity portfolio
  -> structured experiment memory
  -> evidence-grounded LLM diagnosis
  -> deterministic stale-repeat validation
  -> champion-aware experiment plan
  -> deterministic merchant policy
  -> Razorpay execution boundary
  -> fixed-horizon measurement
  -> KEEP / ROLLBACK / INCONCLUSIVE
  -> champion + learned memory + hash-chained audit
```

> **AI proposes. Deterministic policy authorizes. The execution boundary acts. Statistics decides. Persisted outcomes shape the next cycle.**

The LLM cannot authorize merchant-impacting changes, call Razorpay directly, choose the statistical rule, or declare its own treatment successful.

## Why this is not "LLM + dashboard"

| Naive agent risk | Revenue Autopilot boundary |
| --- | --- |
| Model intuition chooses what matters | Deterministic opportunity ranking |
| Hallucinated reasoning becomes action | Structured output + evidence validation |
| Failed ideas repeat forever | Persisted memory + stale-repeat checks |
| Unsafe parameters reach payments | Deterministic merchant policy |
| Retried writes create duplicates | Operation ledger + ambiguous-write protection |
| Agent judges its own idea | Fixed-horizon statistics |
| Past winners are forgotten | KEEP-derived champion state |
| Benchmark data is passed off as merchant truth | Explicit real-vs-synthetic measurement boundary |
| Lifecycle history is hard to trust | SHA-256 hash-chained audit |

## Implemented product

- deterministic conversion, GMV, segment, and payment-method metrics
- conversion-divergence opportunity detection
- deterministic opportunity portfolio
- structured terminal experiment memory
- evidence-grounded LLM diagnosis
- deterministic schema/evidence/semantic/stale-repeat validation
- champion-aware experiment planning
- deterministic merchant policy
- Razorpay executor + application idempotency ledger
- fixed-horizon statistical engine
- KEEP-derived champion promotion
- repeatable history-preserving cycles
- Merchant Intelligence read model/UI
- tamper-evident audit history
- merchant CSV onboarding
- incremental payment-data append + deduplication
- one-click canonical fixed-horizon experiment interaction
- explicit real-merchant measurement fail-closed boundary

## Merchant data and measurement boundary

A merchant can be created from canonical CSV payment history and can append later transaction revisions. Repeated external rows are deduplicated rather than replayed as fresh evidence.

Uploaded merchant observations may feed metrics, detection, ranking, diagnosis, planning, and policy. They **do not** enter TechBazaar's hidden causal simulator for experimental outcomes.

At real-merchant measurement time the product enters **Awaiting live outcomes** and the backend returns:

```text
LIVE_EXPERIMENT_TRAFFIC_REQUIRED
```

Zero synthetic experiment attempts and zero invented statistical results are created for that merchant. A production checkout/payment-event integration must provide authoritative assigned control/treatment outcomes first.

See [`docs/task-22-e2e-boundaries.md`](docs/task-22-e2e-boundaries.md).

## Razorpay execution evidence

The repository contains a real Razorpay Test Mode HTTP client and deterministic executor for Payment Links and Orders, including payment-method configuration, partial payment, expiry configuration, resource fetch, cancellation/rollback, idempotency, and fail-closed ambiguous-write handling.

### Hosted history is mixed by design

Earlier hosted verification cycles used the explicitly simulated adapter and therefore produced IDs such as:

```text
demo_plink_0a6348797891d7c8
```

The later hosted cycle detected on 30 Aug 2026 persisted and renders:

```text
plink_TW3blQWQpXXHRL
```

as a **Razorpay Test Mode Payment Link**, with policy `APPROVE`, experiment `83b571f0-3520-4435-b279-7fad1f4e0efb`, and an INCONCLUSIVE fixed-horizon result.

In this codebase the simulated adapter generates `demo_plink_*` IDs. A persisted plain `plink_*` is therefore **application-side evidence that the real Test Mode client path returned a Razorpay resource for that hosted cycle**.

That is not the same as independent external verification. Until the same `plink_*` is confirmed in the Razorpay Test Mode dashboard/API, the accurate claim is:

> **The live application records a real Test Mode execution result; independent external confirmation is still pending.**

Do not describe an older `demo_plink_*` as a real Razorpay object.

### Controlled create → verify → cancel proof

The repository also includes:

```text
python scripts/verify_razorpay_autopilot.py
```

With local `rzp_test_*` credentials it exercises:

```text
persisted plan
  -> deterministic policy APPROVE
  -> real executor deploy
  -> plink_...
  -> persisted resource + operation ledger
  -> independent Razorpay fetch
  -> unchanged fixed-horizon runtime/statistics
  -> genuine ROLLBACK fixture
  -> real executor cancellation
  -> independent cancellation fetch
  -> audit verification
```

The harness refuses live-mode keys, uses a temporary local database, and attempts cleanup on failure. Its domain chain is covered by offline CI with a stateful fake Razorpay client.

**Offline CI is not external Razorpay proof.** Call the complete external proof verified only after the credential-gated script returns `PASS` and the corresponding Test Mode resource is independently observed.

See [`docs/RAZORPAY_TEST_MODE_PROOF.md`](docs/RAZORPAY_TEST_MODE_PROOF.md).

## Champion, memory, and statistics

Champion v1 is the merchant baseline. Only a statistically significant `KEEP` can promote a treatment. `ROLLBACK` and `INCONCLUSIVE` leave the champion unchanged, and a challenger identical to the current champion is rejected.

Terminal experiment memory is reconstructed from persisted experiment, policy, statistical, and resource records. Exact policy-rejected proposals stay blocked; exact previous ROLLBACK/INCONCLUSIVE proposals are blocked when evidence is materially unchanged.

The experiment engine evaluates only after both variants reach the configured fixed horizon and returns `KEEP`, `ROLLBACK`, or `INCONCLUSIVE`. The LLM never participates in that decision.

## Live read-only state checked after final hardening

Read-only production smoke checks returned HTTP 200 for:

- `/overview`
- `/onboarding`
- `/data`
- `/intelligence`
- `/autopilot`
- `/audit`
- latest cycle detail

The current live state observed during the final smoke included:

- 14,115 accumulated payment attempts
- Champion v1
- 5 terminal trials / 4 statistical results
- 0 promoted treatments
- 4 INCONCLUSIVE statistical results
- audit integrity Verified
- latest experiment `83b571f0-3520-4435-b279-7fad1f4e0efb`
- latest Test Mode resource `plink_TW3blQWQpXXHRL`
- latest decision `INCONCLUSIVE`, p ≈ 0.9917

These are a point-in-time production observation, not immutable benchmark numbers.

## Frozen synthetic benchmark

A separate deterministic benchmark compares `NO_OPTIMIZATION`, `RANDOM_INTERVENTION`, `RULE_BASED`, and `AUTOPILOT` on identical paired synthetic contexts.

| Strategy | Mean conversion | Mean delta vs control |
| --- | ---: | ---: |
| **AUTOPILOT** | **59.39%** | **+1.22 pp** |
| RANDOM_INTERVENTION | 59.20% | +1.02 pp |
| NO_OPTIMIZATION | 58.18% | 0.00 pp |
| RULE_BASED | 57.65% | -0.52 pp |

These are reproducible **synthetic evaluation results, not production revenue claims**.

See [`docs/evaluation/summary.md`](docs/evaluation/summary.md).

## Product routes

- `/overview` — merchant evidence + lifecycle state
- `/data` — incremental payment-data append
- `/intelligence` — champion + learned experiment history
- `/autopilot` — optimization cycles
- `/autopilot/[opportunityId]` — evidence → AI → plan → policy → execution → statistics
- `/audit` — hash-chained lifecycle history
- `/onboarding` — merchant creation + initial CSV

## Local verification

Backend:

```bash
cd backend
pytest -q
```

Frontend:

```bash
cd frontend
npm test
npm run lint
npm run typecheck
npm run build
```

Controlled Razorpay proof from repository root, after configuring local **Test Mode** credentials:

```bash
python scripts/verify_razorpay_autopilot.py
```

Never commit credentials or expose backend secrets through `NEXT_PUBLIC_*` variables.

## Current limitations

- the live application now contains application-side evidence of a real Test Mode `plink_*`, but independent dashboard/API confirmation of that same resource has not been recorded in the repository;
- the full credential-gated create→fetch→ROLLBACK→cancel proof still requires a real Test Mode run;
- uploaded merchants do not yet have authoritative checkout assignment + Razorpay payment-event/webhook ingestion for live experiment outcomes;
- authentication, tenant authorization, and merchant credential isolation are not production-complete;
- scheduled/background experiment progression is not production-complete;
- synthetic benchmark results do not establish external validity across Razorpay merchants;
- the audit chain is application-level tamper evidence, not a blockchain.

## Next production work

1. independently confirm the hosted `plink_*` and run the controlled Test Mode cancellation proof;
2. add authoritative control/treatment assignment at checkout;
3. ingest Razorpay payment events/webhooks bound to persisted assignments;
4. add authentication + tenant/credential isolation;
5. add scheduled experiment progression and external-write reconciliation;
6. perform consented real-merchant evaluation before production uplift claims.

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md)
- [`docs/JUDGE_QA.md`](docs/JUDGE_QA.md)
- [`docs/SUBMISSION.md`](docs/SUBMISSION.md)
- [`docs/RAZORPAY_TEST_MODE_PROOF.md`](docs/RAZORPAY_TEST_MODE_PROOF.md)
- [`docs/PRODUCTION_VERIFICATION.md`](docs/PRODUCTION_VERIFICATION.md)
- [`docs/evaluation/summary.md`](docs/evaluation/summary.md)
