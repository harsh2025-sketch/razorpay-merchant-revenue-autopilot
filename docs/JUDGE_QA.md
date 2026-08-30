# Final Judge Q&A

## What is Merchant Revenue Autopilot?

Revenue Autopilot is a controlled learning system for payment-conversion optimization.

It observes merchant payment evidence, detects conversion leakage, lets an LLM propose a structured causal hypothesis, converts that proposal into a bounded experiment, applies deterministic merchant policy, executes through an explicit Razorpay boundary, evaluates at a fixed statistical horizon, and persists the outcome so later cycles learn from prior trials.

The core rule is:

> **AI proposes. Deterministic policy authorizes. The execution boundary acts. Statistics decides. Persisted outcomes shape the next cycle.**

## What is the actual AI contribution?

The LLM performs evidence-grounded diagnosis and hypothesis generation. It maps heterogeneous merchant evidence plus compact prior experiment history into a structured candidate intervention.

The LLM does **not**:

- rank opportunities;
- set experiment traffic allocation;
- choose the statistical threshold;
- authorize merchant-impacting changes;
- call Razorpay directly;
- decide whether its treatment won;
- promote itself to champion.

If the hypothesis space eventually becomes completely enumerable, some diagnosis work could become deterministic. The architecture does not pretend an LLM is necessary for tasks deterministic code can do better.

## Is this just an A/B testing dashboard?

No. The dashboard is only a read/control surface over a backend lifecycle:

```text
payment evidence
  -> deterministic metrics + detection
  -> deterministic opportunity portfolio
  -> structured experiment memory
  -> LLM diagnosis
  -> deterministic stale-repeat validation
  -> champion-aware experiment planner
  -> deterministic merchant policy
  -> duplicate-safe execution boundary
  -> fixed-horizon measurement
  -> KEEP / ROLLBACK / INCONCLUSIVE
  -> champion + learned memory + audit
```

## How does the system learn?

Learning is reconstructed from persisted structured records rather than free-form chat history.

Per terminal experiment the system can recover:

- segment and intervention;
- treatment configuration;
- policy outcome;
- statistical decision;
- lift and p-value;
- resource state;
- prior trial count.

Active experiments are excluded until they reach a safe terminal boundary.

## How do you stop the model from repeating failed ideas forever?

After the LLM responds, deterministic memory validation compares the semantic proposal with prior merchant trials.

- exact policy-rejected configurations remain blocked;
- exact prior ROLLBACK/INCONCLUSIVE proposals are blocked when observable evidence is materially unchanged;
- reconsideration is allowed only after explicit material evidence change;
- one bounded corrective diagnosis attempt is allowed before failing closed.

Nothing is persisted until the proposal passes schema, evidence, semantic, and memory checks.

## How are opportunities prioritized?

Only eligible untouched opportunities are ranked. The deterministic portfolio uses observable conversion gap, affected volume, captured average order value, merchant-policy feasibility, and prior terminal-trial history.

The history adjustment is explicit:

```text
history_factor = 1 / (1 + prior_terminal_trials)
```

Any displayed GMV figure is an **opportunity-sizing proxy**. It is not realized revenue, forecast revenue, or a causal uplift claim.

## What is Champion v1?

Champion v1 is the merchant baseline configuration.

Only a fixed-horizon statistical `KEEP` can promote a treatment. Future experiments of the same intervention type inherit the promoted configuration as control. `ROLLBACK` and `INCONCLUSIVE` leave the champion unchanged.

The preserved Task 20 hosted snapshot has three terminal INCONCLUSIVE trials, so Champion v1 correctly remains the merchant baseline.

## Why is an INCONCLUSIVE result useful in a demo?

Because the architecture is supposed to prevent the agent from declaring success without sufficient evidence.

The preserved hosted result was approximately:

- control conversion: 47.2%
- treatment conversion: 45.5%
- absolute lift: -1.7 percentage points
- p-value: 0.6412
- decision: `INCONCLUSIVE`

The system did not promote the treatment simply because the AI proposed it.

## Why fixed-horizon statistics?

Repeatedly peeking at interim p-values and stopping whenever results look favorable inflates false-positive risk.

Revenue Autopilot evaluates only after both variants reach the predefined horizon. A deterministic two-proportion test plus practical-lift threshold returns `KEEP`, `ROLLBACK`, or `INCONCLUSIVE`. The LLM does not participate.

## What prevents unsafe financial changes?

A deterministic merchant policy checks the complete planned experiment, including intervention allow-list, treatment exposure, minimum sample size, duration, discount limits, financial exposure, and concurrent-experiment constraints.

Unsafe proposals are rejected. The system does not silently clamp an unsafe AI proposal into a different approved proposal.

## Why not let the LLM call Razorpay directly?

The component that generates a hypothesis should not also authorize and execute it.

The LLM proposes a semantic intervention. Deterministic policy authorizes or rejects it. The executor converts only an already-approved treatment into a supported Razorpay operation.

## Is Razorpay actually integrated?

The repository contains a real Razorpay Test Mode HTTP client and a deterministic executor for Payment Links and Orders, including:

- payment-method configuration;
- partial payments;
- expiry configuration;
- verified existing Offer association where supported;
- resource fetch;
- cancellation/rollback;
- application-level idempotency;
- ambiguous-write fail-closed behavior.

The **public hosted demo remains explicitly simulated** and uses `demo_...` resource IDs. Those are not represented as real Razorpay dashboard objects.

A separate credential-gated verifier, `scripts/verify_razorpay_autopilot.py`, exercises the exact executor chain against Razorpay Test Mode. It refuses live-mode keys and cleans up its Test Mode Payment Link.

Do not call the Test Mode path externally verified until that script has actually completed with `PASS` using real `rzp_test_...` credentials and the matching resource has been independently observed. See `docs/RAZORPAY_TEST_MODE_PROOF.md`.

## Why is application-level idempotency important?

The executor records a unique operation key and canonical request hash before an external write.

- a confirmed repeated deployment returns the existing resource;
- a conflicting request is rejected;
- an ambiguous timeout/network/5xx outcome is not blindly retried;
- rollback uses the same explicit operation-ledger boundary.

This prevents a transient HTTP failure from silently creating duplicate merchant resources.

## Why is Offer creation not automated?

The executor does not invent a nonexistent or unverified Offer creation path. Offer-based deployment requires a verified pre-created Offer mapping. Without it, the system fails closed.

## Can real merchants onboard data now?

Yes. The production-facing path supports creating a merchant from canonical CSV payment history and later appending additional transaction revisions with deterministic deduplication.

Historical observations are preserved rather than replayed as new evidence.

This is separate from authentication and full multi-tenant account isolation, which remain production-hardening work.

## Do uploaded merchants use the TechBazaar simulator?

No. This is an explicit Task 22 boundary.

Uploaded merchant history can feed metrics, detection, ranking, diagnosis, planning, and policy. But once a treatment reaches measurement, the system **refuses** to generate experimental outcomes from TechBazaar's hidden causal simulator.

The product enters an **Awaiting live outcomes** state and returns `LIVE_EXPERIMENT_TRAFFIC_REQUIRED` rather than manufacturing lift or a p-value.

A production integration must provide authoritative assigned control/treatment payment outcomes before the existing statistics engine can evaluate that merchant's experiment.

## Why not generalize the simulator to every uploaded merchant?

Because that would turn an evaluation model into fabricated production evidence.

The sealed TechBazaar causal model exists only to evaluate the system under known hidden truth. It is not evidence about an arbitrary merchant.

## What does one-click experiment execution mean?

For the canonical TechBazaar evaluation merchant, Task 21C combines repeated runtime-batch clicks into one bounded `Run Experiment` operation.

It does **not** weaken the boundaries:

- persisted policy APPROVE is still required;
- an active deployed treatment resource is still required;
- Task 11 remains the only synthetic runtime;
- Task 12 remains the only statistical authority;
- explicit Razorpay rollback remains separate;
- repeated calls after a result are idempotent.

Uploaded merchants do not receive that synthetic one-click runtime; they wait for real outcomes.

## What is the benchmark and what does it prove?

A separate frozen deterministic benchmark compares:

- no optimization;
- random intervention;
- rule-based intervention;
- Autopilot.

The canonical evaluation averages approximately 59.39% conversion for Autopilot versus 58.18% for no optimization across the frozen paired world.

That demonstrates reproducible behavior inside the synthetic evaluation environment. It does **not** prove production revenue lift across Razorpay's merchant population.

## Why can random intervention perform reasonably well?

Because the hidden benchmark world contains genuinely positive interventions. Random selection can hit one by chance. A useful baseline should not be engineered to always lose.

## What does the audit trail guarantee?

Major lifecycle events are appended to a per-merchant SHA-256 hash chain.

It provides application-level tamper evidence and provenance for evidence, diagnosis, planning, policy, execution, statistics, promotion, and rollback.

It is not a blockchain and does not claim protection against an administrator capable of rewriting the entire database and recomputing every hash.

## What happens when the LLM or an external provider fails?

The system fails closed.

Examples include:

- malformed structured diagnosis;
- unknown evidence references;
- unsupported intervention parameters;
- unavailable AI configuration;
- missing Razorpay configuration;
- ambiguous external writes;
- insufficient experiment samples;
- unsafe rollover of an active cycle.

The project preserves failure history instead of hiding it. Earlier production verification exposed a provider-side structured-output issue, which was fixed and regression-tested without inventing a successful experiment.

## Why OpenRouter in the hosted demo?

The diagnosis layer uses an OpenAI-compatible SDK boundary. The hosted deployment points that boundary at OpenRouter. Provider choice does not alter downstream evidence validation, memory rules, policy, execution, or statistics.

The frozen benchmark uses a deterministic diagnosis adapter so repeated evaluation runs do not depend on a changing hosted model.

## What is the strongest production boundary in the architecture?

No single probabilistic component owns the whole commercial decision.

- AI proposes.
- Deterministic code ranks and validates.
- Merchant policy authorizes.
- The executor controls external mutation.
- Statistics determines success.
- Persisted memory controls what happens next.

That separation is the main engineering contribution.

## What would you build next for a production rollout?

The highest-value next steps are:

1. authoritative checkout assignment and Razorpay payment-event/webhook ingestion for real control/treatment outcomes;
2. authentication, tenant authorization, and merchant credential management;
3. scheduled/background experiment progression;
4. external-write reconciliation for ambiguous states;
5. stronger concurrent database transaction controls;
6. consented real-merchant evaluation before making production uplift claims.

Merchant CSV onboarding, incremental append/dedup, and real-vs-synthetic measurement separation are already implemented and should not be listed as future features.

## What should I never claim to a judge?

Do not claim:

- a `demo_...` resource exists in Razorpay;
- Test Mode proof has passed before the credential-gated verifier actually passes;
- opportunity-sized GMV is realized revenue;
- synthetic benchmark lift is production lift;
- the LLM authorizes or statistically judges its own ideas;
- uploaded merchants use the TechBazaar causal model;
- the audit chain is a blockchain;
- a hosted INCONCLUSIVE trial was a successful revenue increase.
