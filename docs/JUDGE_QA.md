# Final Judge Q&A

## What is Merchant Revenue Autopilot?

Revenue Autopilot is a controlled learning system for payment-conversion optimization.

It observes merchant payment evidence, detects conversion leakage, lets an LLM propose a structured causal hypothesis, converts the proposal into a bounded experiment, applies deterministic merchant policy, executes through an explicit Razorpay boundary, evaluates at a fixed statistical horizon, and persists the outcome so later cycles learn from prior trials.

> **AI proposes. Deterministic policy authorizes. The execution boundary acts. Statistics decides. Persisted outcomes shape the next cycle.**

## What is the AI actually responsible for?

The LLM performs evidence-grounded diagnosis and hypothesis generation from merchant evidence plus compact prior experiment history.

It does **not**:

- rank opportunities;
- authorize merchant-impacting changes;
- call Razorpay directly;
- set statistical significance rules;
- decide whether its treatment won;
- promote itself to champion.

If a future hypothesis space becomes completely enumerable, some diagnosis could be made deterministic. The architecture does not force an LLM into tasks deterministic code can do better.

## Is this just an analytics dashboard with an OpenAI call?

No. The UI is a read/control surface over a backend lifecycle:

```text
payment evidence
  -> metrics + detection
  -> deterministic opportunity portfolio
  -> structured experiment memory
  -> LLM hypothesis
  -> deterministic stale-repeat validation
  -> champion-aware planner
  -> deterministic merchant policy
  -> idempotent execution boundary
  -> fixed-horizon measurement
  -> KEEP / ROLLBACK / INCONCLUSIVE
  -> champion + learned memory + audit
```

The interesting engineering is the separation of authority around the probabilistic component.

## How does the system learn?

Learning is reconstructed from persisted domain records rather than free-form chat memory.

Per terminal experiment the system can recover treatment configuration, merchant-policy outcome, statistical decision, lift, p-value, resource state, and prior-trial count. Active work is excluded until it reaches a safe terminal boundary.

## How do you stop repeated failed ideas?

After the LLM responds, deterministic memory validation compares the semantic proposal with prior merchant trials.

- exact policy-rejected configurations remain blocked;
- exact prior ROLLBACK/INCONCLUSIVE proposals are blocked when evidence is materially unchanged;
- reconsideration requires explicit material evidence change;
- one bounded corrective diagnosis attempt is allowed before failing closed.

Nothing is persisted until schema, evidence, semantics, and memory checks pass.

## How are opportunities prioritized?

Untouched opportunities are ranked deterministically using observable conversion gap, affected volume, captured average order value, policy feasibility, and prior terminal trials.

The displayed GMV number is an **opportunity-sizing proxy**. It is not realized revenue, profit, or a causal uplift forecast.

## What is Champion v1?

Champion v1 is the merchant baseline configuration. Only a fixed-horizon statistical `KEEP` can promote a treatment. `ROLLBACK` and `INCONCLUSIVE` leave the champion unchanged.

The hosted history currently has no KEEP promotion, so Champion v1 correctly remains the merchant baseline.

## Why is INCONCLUSIVE acceptable?

Because the system should refuse to promote its own idea without sufficient evidence.

The latest observed hosted cycle had approximately:

- control conversion: 48.46%
- treatment conversion: 48.5%
- absolute lift: approximately 0.04 percentage points
- p-value: approximately 0.9917
- decision: `INCONCLUSIVE`

The LLM did not participate in that decision, and Champion v1 remained unchanged.

## Why fixed-horizon statistics?

Repeatedly peeking and stopping when a p-value looks favorable inflates false-positive risk. Revenue Autopilot evaluates only after both variants reach the predefined horizon. A deterministic two-proportion test plus practical-lift threshold returns `KEEP`, `ROLLBACK`, or `INCONCLUSIVE`.

## What prevents unsafe financial changes?

Deterministic merchant policy checks intervention allow-lists, treatment exposure, minimum sample size, duration, discounts, financial exposure, and concurrent-experiment constraints.

Unsafe proposals are rejected rather than silently clamped into a different approved experiment.

## Is Razorpay actually integrated?

Yes, at the code and application-execution boundary.

The repository contains a real Razorpay Test Mode HTTP client and deterministic executor for Payment Links and Orders, including supported checkout configuration, partial payment, expiry configuration, resource fetch, cancellation, application-level idempotency, and ambiguous-write protection.

The hosted history contains two classes of execution evidence:

1. **Older explicitly simulated cycles**, which created IDs such as `demo_plink_0a6348797891d7c8`.
2. **A later hosted cycle** whose approved executor path persisted and renders `plink_TW3blQWQpXXHRL` as a Razorpay Test Mode Payment Link.

In this codebase simulated execution generates `demo_plink_*`. A plain `plink_*` therefore provides **application-side evidence that the real Test Mode client path returned a Razorpay resource** for that cycle.

That still does not equal independent external verification. The matching `plink_*` should be confirmed in the Razorpay Test Mode dashboard/API before saying the external proof is fully closed.

## What exactly remains to prove about Razorpay?

Two levels should be kept separate:

- **Application-side execution evidence:** already present for `plink_TW3blQWQpXXHRL`.
- **Independent external confirmation:** confirm the same resource in Razorpay Test Mode outside our own database/UI.

For the strongest proof, the repo includes `scripts/verify_razorpay_autopilot.py`, which performs:

```text
policy APPROVE
  -> executor create
  -> independent Razorpay fetch
  -> fixed-horizon harmful fixture
  -> genuine ROLLBACK
  -> executor cancellation
  -> independent cancellation fetch
  -> audit verification
```

It refuses live-mode keys and uses a temporary local database. Its domain chain passes offline CI, but that offline test is not a substitute for a real credential-gated run.

## Why not let the LLM call Razorpay directly?

The component that invents a hypothesis should not also authorize and execute it. The LLM proposes semantic intent; deterministic policy authorizes; the executor translates only an approved treatment into supported Razorpay operations.

## Why is application-level idempotency important?

A network timeout does not tell you whether an external write happened. The executor records a unique operation key and canonical request hash before the write.

- confirmed repeats return the existing resource;
- conflicting requests fail;
- ambiguous timeout/network/5xx states are not blindly retried;
- rollback uses the same explicit operation ledger.

This protects against duplicate payment resources after transient failures.

## Why not auto-create every Offer?

The executor does not invent an unsupported or unverified API path. Offer-based deployment requires a verified pre-created Offer mapping; otherwise it fails closed.

## Can a real merchant upload data?

Yes. A merchant can be created from canonical CSV payment history and append later transaction revisions with deterministic deduplication.

Historical observations feed metrics, detection, ranking, diagnosis, planning, and policy.

## Do uploaded merchants use the TechBazaar simulator?

No. This is an explicit Task 22 safety boundary.

When an uploaded merchant reaches experiment measurement, Revenue Autopilot refuses to manufacture treatment outcomes from TechBazaar's hidden causal world. The product enters **Awaiting live outcomes** and returns `LIVE_EXPERIMENT_TRAFFIC_REQUIRED`.

A production checkout/payment-event integration must provide authoritative assigned control/treatment outcomes before statistics can run.

## Why not generalize the simulator to uploaded merchants?

Because that would turn an evaluation model into fabricated production evidence. TechBazaar's hidden causal model exists to test the system under known concealed truth; it is not evidence about an arbitrary merchant.

## What does one-click experiment execution mean?

For TechBazaar, the UI can drive the authorized runtime to its fixed horizon with one bounded `Run Experiment` action rather than repeated batch clicks.

It still requires persisted policy APPROVE and an active treatment resource. Task 11 remains the synthetic runtime, Task 12 remains the statistical authority, and explicit external rollback remains separate.

Uploaded merchants do not get the synthetic one-click outcome path.

## What does the benchmark prove?

A frozen deterministic benchmark compares no optimization, random intervention, a rule baseline, and Autopilot over identical paired synthetic contexts.

Autopilot averages approximately 59.39% conversion versus 58.18% for no optimization in that frozen world.

That demonstrates reproducible behavior in the synthetic evaluation environment. It does **not** prove production revenue uplift across Razorpay's merchant population.

## What does the audit trail guarantee?

Major lifecycle events are appended to a per-merchant SHA-256 hash chain. It provides application-level tamper evidence and provenance.

It is not a blockchain and does not protect against an administrator capable of rewriting the entire database and recomputing every hash.

## What happens when the LLM or an external provider fails?

The system fails closed. Examples include malformed structured diagnosis, invalid evidence references, unsupported interventions, unavailable AI configuration, missing Razorpay configuration, ambiguous external writes, insufficient samples, and unsafe lifecycle rollover.

A production-only provider parsing failure was previously detected before any experiment/payment mutation, repaired, regression-tested, and safely resumed. That failure history is intentionally preserved.

## Why OpenRouter in the hosted demo?

The diagnosis boundary uses an OpenAI-compatible SDK and the hosted deployment routes it through OpenRouter. Provider choice does not alter evidence validation, merchant memory, policy, execution, or statistics.

## What is the strongest architecture decision?

No single probabilistic component owns the commercial decision.

- AI proposes.
- Deterministic code validates and ranks.
- Merchant policy authorizes.
- The executor controls external mutation.
- Statistics determines success.
- Persisted memory controls what happens next.

## What would you build next for production?

1. Independently confirm the hosted Test Mode `plink_*` and complete the credential-gated create→verify→cancel proof.
2. Authoritative checkout assignment and Razorpay payment-event/webhook ingestion for real control/treatment outcomes.
3. Authentication, tenant authorization, and merchant credential isolation.
4. Scheduled/background experiment progression.
5. External-write reconciliation and stronger concurrent transaction controls.
6. Consented real-merchant evaluation before production uplift claims.

Merchant CSV onboarding, incremental append/dedup, one-click canonical interaction, and real-vs-synthetic measurement separation are already implemented.

## What should I never claim to a judge?

Do not claim:

- a `demo_plink_*` is real;
- application-side `plink_*` evidence is already independent dashboard/API confirmation unless you actually show that confirmation;
- the full create→verify→cancel proof passed before it does;
- opportunity-sizing GMV is realized revenue;
- synthetic benchmark lift is production lift;
- the LLM authorizes or statistically judges its own ideas;
- uploaded merchants use TechBazaar's causal model;
- Champion advanced when it remains v1;
- the audit chain is a blockchain.
