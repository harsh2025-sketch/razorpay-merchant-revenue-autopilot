# Submission Copy

## Project name

Merchant Revenue Autopilot

## One-line pitch

A learning AI revenue agent for Razorpay merchants where the model can propose experiments, but deterministic prioritization, merchant policy, controlled execution, fixed-horizon statistics, and persisted experiment memory decide what happens next.

## Short description

Merchant Revenue Autopilot detects payment-conversion leakage, ranks eligible opportunities, gives an evidence-grounded LLM structured knowledge from prior merchant experiments, converts the proposal into a bounded champion–challenger test, applies deterministic merchant policy, executes through a Razorpay boundary, measures the result at a fixed sample horizon, and learns from the terminal outcome without deleting prior history.

The key design choice is separation of authority. The LLM cannot call Razorpay, cannot bypass merchant limits, cannot choose its own sample horizon, cannot repeat an unchanged failed idea without passing deterministic memory checks, and cannot decide whether its treatment succeeded.

## Problem

Merchants can lose conversion because payment behavior differs across customer segments, devices, order values, and payment methods. A naive AI agent can detect patterns, but giving the same probabilistic component authority to choose, authorize, execute, repeat, and judge commercial changes introduces operational risk and makes the learning loop difficult to audit.

## Solution

Revenue Autopilot separates the system into:

1. deterministic metric and opportunity detection
2. deterministic opportunity portfolio ranking
3. structured terminal experiment memory
4. evidence-grounded LLM hypothesis generation
5. deterministic schema, evidence, semantic, and stale-repeat validation
6. champion-aware deterministic experiment planning
7. deterministic merchant-policy authorization
8. duplicate-safe execution boundary
9. fixed-horizon experiment runtime and statistics
10. KEEP-derived champion state
11. hash-chained merchant audit history

## Adaptive layer

### Merchant experiment memory

The system reconstructs learning from persisted experiment, policy, result, and resource records rather than storing opaque chat history. Active work is excluded until it reaches a terminal boundary.

### Opportunity portfolio

Untouched detected opportunities are ranked deterministically using observable conversion gap, affected volume, captured average order value, policy feasibility, and prior terminal-trial history. Opportunity-sized GMV is explicitly a heuristic proxy, not forecast revenue.

### Memory-aware diagnosis

The LLM receives prior experiment outcomes for the affected segment. Deterministic code blocks exact stale failed/inconclusive proposals unless observable evidence has materially changed, and exact policy-rejected configurations remain blocked.

### Champion–challenger

Only a statistical KEEP can promote a treatment. Future experiments of that intervention type use the promoted champion as control. ROLLBACK and INCONCLUSIVE retain the prior champion.

### Merchant Intelligence

The live `/intelligence` page shows the current champion, terminal trial counts, learned segment/intervention history, policy rejections, and the deterministic opportunity portfolio when eligible candidates exist.

## Razorpay usage

The repository implements Razorpay Test Mode client and executor paths for Payment Links and Orders, including payment-method configuration, partial payment, expiry configuration, pre-created Offer association, fetch, and cancellation semantics.

Razorpay Offers are not fabricated or created through a nonexistent API path. Offer-based deployment fails closed until a verified pre-created Test Mode Offer ID is mapped.

The public hosted demo runs `RAZORPAY_EXECUTION_MODE=simulated` because merchant API credentials require onboarding/KYC unavailable to this submission account. Simulated resources are visibly marked with `demo_...` IDs, and the UI explicitly states that no Razorpay API request was made.

## AI usage

The diagnosis boundary uses the OpenAI Python SDK with an OpenAI-compatible endpoint; the hosted demo is routed through OpenRouter.

The model receives observable evidence plus compact prior experiment knowledge and returns a structured hypothesis. Output is schema-validated, evidence-validated, semantic-validated, and memory-validated before persistence.

Provider-side structured-output parsing failures fail closed. A bounded diagnosis-only retry is permitted because no experiment or payment action exists at that point.

## Frozen evaluation

A separate deterministic benchmark compares:

- no optimization
- random intervention
- rule-based intervention
- Autopilot

Canonical run: five fixed seeds, five segments, and 5,000 paired contexts per segment per seed.

| Strategy | Mean conversion | Mean delta vs control |
| --- | ---: | ---: |
| Autopilot | 59.39% | +1.22 pp |
| Random intervention | 59.20% | +1.02 pp |
| No optimization | 58.18% | 0.00 pp |
| Rule based | 57.65% | -0.52 pp |

Autopilot recorded 5 policy rejections instead of forcing every proposal into deployment.

These are synthetic deterministic results in a frozen evaluation world, not production revenue evidence.

## Final hosted verification — Task 20

The final guarded production verification started with two preserved terminal trials and Champion v1.

No untouched opportunity existed before rollover, so the next cycle came from fresh deterministic detection. The new android_budget opportunity was:

`0e500ccd-6c3d-4ade-a06c-afc3d2cd24e6`

The live LLM received prior experiment history and explicitly reasoned over earlier payment-method and partial-payment outcomes. It proposed enabling card, UPI, netbanking, and wallet for the segment. Deterministic memory validation accepted reconsideration because observable evidence had materially changed rather than treating it as an unconditional repeat.

The deterministic planner created a 10% treatment experiment with 200 minimum samples per variant and a 72-hour maximum duration. Champion v1 was correctly used as the baseline control because no previous treatment had earned KEEP.

Merchant policy returned `APPROVE`.

Hosted simulated execution created:

`demo_plink_0a6348797891d7c8`

The fixed-horizon result was:

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

Because the result was INCONCLUSIVE, Champion correctly remained v1.

The Task 20 verifier confirmed:

- terminal trial count increased exactly once: 2 -> 3
- champion stayed v1 -> v1
- memory-aware hypothesis validation passed
- champion-control validation passed
- active-cycle rollover was rejected with HTTP 409
- the resource remained in the `demo_` namespace
- audit-chain integrity remained valid
- earlier trial history remained persisted

The live Intelligence page now shows three terminal statistical trials: two payment-method-config trials and one partial-payment trial, all INCONCLUSIVE, with no promoted treatment.

## Live links

Frontend:  
https://merchant-revenue-autopilot-psi.vercel.app

Merchant Intelligence:  
https://merchant-revenue-autopilot-psi.vercel.app/intelligence

Backend health:  
https://merchant-revenue-autopilot-api.onrender.com/health

Evaluation summary:  
`docs/evaluation/summary.md`

Production verification:  
`docs/PRODUCTION_VERIFICATION.md`

## Stack

Frontend: Next.js App Router, TypeScript, Tailwind CSS, Recharts, Lucide

Backend: Python 3.12, FastAPI, SQLAlchemy 2, Pydantic v2, httpx, OpenAI-compatible SDK

Database: Supabase PostgreSQL hosted; SQLite supported locally

Hosting: Vercel frontend, Render backend

Testing: pytest, Vitest, Testing Library, TypeScript typecheck, ESLint, Next.js production build, guarded hosted production verification

## Demo merchant

TechBazaar Electronics is a deterministic synthetic consumer-electronics merchant profile. The frozen canonical baseline contains 6,112 payment attempts. Controlled experiment traffic is appended rather than reset, so the live dashboard currently contains 12,258 attempts across three preserved terminal cycles.

Do not present the accumulated live total as the frozen baseline size.

## Safety and failure behavior

The system fails closed when:

- structured model output is malformed
- evidence references are invalid
- an exact stale failed/inconclusive proposal is repeated without material evidence change
- an exact policy-rejected proposal is repeated
- an intervention is not allowed
- exposure or discount limits are exceeded
- an Offer is not safely mapped
- external write state is ambiguous
- the experiment has not reached its fixed sample horizon
- a caller tries to skip an active/deployed cycle
- a challenger is identical to the current champion

No unsafe proposal is silently rewritten into an approved one.

## What is novel here

The contribution is the control and learning architecture around an AI growth agent:

- observable-evidence grounding
- deterministic opportunity prioritization
- structured merchant experiment memory
- stale-repeat prevention
- champion–challenger progression from statistical KEEP results
- deterministic merchant authorization
- duplicate-safe execution
- fixed-horizon statistical decisions
- repeatable, history-preserving cycles
- hash-chained audit
- a judge-visible Intelligence layer that exposes the system's actual learned state

## Limitations

- The hosted payment resource is simulated, not a real Razorpay dashboard object.
- The benchmark is synthetic and deterministic, not production traffic.
- The benchmark diagnosis adapter is deterministic for reproducibility and does not use live OpenRouter responses.
- The hosted merchant remains Champion v1 because all three live trials were statistically inconclusive; the code path for KEEP promotion is implemented and regression-tested.
- The audit chain provides application-level tamper evidence, not distributed consensus.
- The current product demonstrates one canonical merchant rather than complete multi-tenant merchant onboarding.
