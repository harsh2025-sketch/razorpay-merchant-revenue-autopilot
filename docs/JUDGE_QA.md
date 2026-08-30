# Judge Q and A

## What is the actual AI contribution?

The LLM performs diagnosis and hypothesis generation from deterministic merchant evidence plus compact prior experiment knowledge. It decides what intervention might explain or improve a detected conversion problem. It does not own opportunity ranking, experiment structure, merchant policy, execution, or statistical success criteria.

## Is this just an A/B testing dashboard?

No. The UI is only the visible surface. The backend contains opportunity detection, deterministic opportunity ranking, structured terminal experiment memory, evidence-grounded LLM diagnosis, stale-repeat validation, champion-aware planning, merchant policy, idempotent execution, sticky assignment, fixed-horizon statistics, repeatable cycle rollover, champion promotion semantics, and hash-chained audit.

## What changed in the adaptive version?

Four major additions:

1. terminal experiment history is reconstructed into structured merchant memory;
2. untouched opportunities are ranked deterministically rather than by the LLM;
3. diagnosis is constrained by prior experiment outcomes so stale exact failed/inconclusive proposals cannot simply repeat without material evidence change;
4. statistical KEEP results become the merchant's future champion control.

The `/intelligence` page exposes this state directly.

## Is the “memory” just putting old text into the prompt?

No. The source of truth is persisted structured experiment data: treatment config, policy decision, statistical outcome, lift, p-value, resource state, and terminal reason. The LLM receives a compact projection of that data, but deterministic code independently enforces stale-repeat rules after the model responds.

There is no vector database or opaque chat history in the learning loop.

## How do you stop the model from repeating a failed idea forever?

The diagnosis memory layer compares the semantic proposal with prior terminal trials for the same merchant segment.

- an exact policy-rejected proposal stays blocked;
- an exact ROLLBACK/INCONCLUSIVE proposal is blocked when observable evidence is materially unchanged;
- reconsideration is allowed only after explicit material change, such as a >=2 percentage-point rate movement or sufficiently large new segment observations.

A stale proposal can receive one bounded diagnosis-only corrective attempt. Nothing is persisted until a proposal passes the memory check.

## Why did Task 20 use payment-method configuration again if it had already been inconclusive?

Because the system does not ban an intervention forever. The previous payment-method trial was inconclusive, but the observable android_budget evidence had materially changed after substantial additional experimental observations. Task 20 verified that the repeat was accepted through the explicit material-change rule rather than bypassing memory.

The live LLM reasoning also explicitly referenced previous partial-payment and payment-method experiments.

## How does opportunity ranking work?

Only untouched detected opportunities are ranked. Inputs are observable conversion gap, segment volume, captured average order value, merchant-policy feasibility, and prior terminal-trial count.

The history adjustment is transparent:

`1 / (1 + prior_terminal_trials)`

The GMV value is an opportunity-sizing proxy, not a forecast or causal estimate.

A partially started cycle always resumes before the system ranks a new candidate.

## Did Task 20 prove the portfolio chose the best of multiple live opportunities?

No, and the submission does not claim that. Before the Task 20 rollover there was no untouched active opportunity, so the portfolio correctly had no next candidate. Rollover then ran fresh deterministic detection and created the third opportunity. The portfolio read model and its invariants were verified, but Task 20 did not manufacture extra candidates to make the dashboard look busier.

## What is Champion v1?

Champion v1 is the merchant's baseline configuration. Champion state is derived from historical statistical KEEP results.

A treatment becomes a new champion only if the fixed-horizon statistical engine returns KEEP. Future experiments of the same intervention type then inherit that promoted configuration as control.

ROLLBACK and INCONCLUSIVE keep the previous champion unchanged.

## Why is the hosted merchant still Champion v1 after three trials?

All three hosted statistical results are INCONCLUSIVE. None has earned the deterministic KEEP threshold, so promoting any of them would be dishonest. Task 20 explicitly verified that champion version stayed v1 -> v1 after the third trial.

## What happens if the proposed challenger is identical to the champion?

The planner rejects the experiment as meaningless instead of allocating merchant traffic to compare identical configurations.

## Why not let the LLM call Razorpay directly?

Because the component that generates a hypothesis should not also authorize or execute a merchant-impacting change. The model produces a proposal; deterministic policy and the execution boundary own the next decisions.

## What happens when the model gives malformed output?

The proposal must match the strict schema, use supported parameters, and reference only evidence keys present in the deterministic catalog. Invalid output is rejected before hypothesis persistence.

The hosted OpenAI-compatible boundary also maps provider-side structured-output parse failures into the diagnosis error model and permits one bounded pre-persistence retry. That retry cannot authorize or execute anything.

## What prevents unsafe discount or exposure levels?

The deterministic policy engine evaluates the complete planned experiment against merchant limits. It does not silently clamp an unsafe proposal into a different approved proposal.

## Why fixed-horizon statistics?

Repeatedly looking at interim p-values and stopping when a treatment looks good inflates false positives. The experiment waits for the predefined sample horizon and evaluates once using the fixed statistical rule.

## What was the final Task 20 live result?

Opportunity:
`0e500ccd-6c3d-4ade-a06c-afc3d2cd24e6`

Experiment:
`5277a2df-c1a5-4009-9320-c97c3576ff38`

Intervention: payment-method configuration enabling card, UPI, netbanking, and wallet for `android_budget`.

The fixed-horizon result was:

- control: 895 / 1,895 = 47.2%
- treatment: 91 / 200 = 45.5%
- absolute lift: -1.7 pp
- relative lift: -3.7%
- p = 0.6412
- 95% CI: approximately -9.0 pp to +5.5 pp
- decision: `INCONCLUSIVE`

The LLM did not participate in that decision.

## Why is an inconclusive result useful in a demo?

Because the architecture is supposed to stop the agent from declaring success without evidence. The hosted merchant now has three preserved inconclusive trials and still has Champion v1. That is evidence that the system does not manipulate its own outcome to look successful.

## Does the benchmark prove production revenue lift?

No. It is a synthetic deterministic benchmark over a frozen causal world. The repository explicitly avoids calling those results production revenue, ROI, or profit.

## What did the benchmark show?

Across five seeds, five segments, and 5,000 paired contexts per segment, Autopilot averaged 59.39% conversion versus 58.18% for no optimization, a +1.22 percentage-point mean delta. Random intervention averaged +1.02 points and the rule baseline averaged -0.52 points.

Autopilot recorded five policy rejections instead of deploying every proposal.

## Why can random intervention do well?

The hidden benchmark world contains genuinely positive interventions, so random selection can hit them by chance. That makes the baseline more credible than engineering it to always lose.

## Why can the live detector focus on android_budget while benchmark behavior is different?

Detection means there is observable divergence; it does not mean a particular treatment is guaranteed to help. The live hosted LLM and the frozen deterministic benchmark diagnosis adapter are separate evaluation contexts.

## Is Razorpay actually integrated?

Yes. The repository implements the real Razorpay Test Mode client and executor boundary for Payment Links and Orders, including payment-method configuration, partial payment, expiry configuration, existing Offer association, fetch, and cancellation semantics.

The public hosted demo uses an explicit simulated adapter because the submission account could not obtain Test Mode credentials without merchant onboarding/KYC. The mode is visibly labelled and makes no Razorpay HTTP request.

## Why not fake a real Razorpay resource for the demo?

The project deliberately avoids that. Hosted resources use the `demo_...` namespace and the UI says they do not exist in the Razorpay dashboard.

The latest Task 20 resource is:

`demo_plink_0a6348797891d7c8`

## Why is Offer creation not automated?

For this integration path, Offers are pre-created dashboard resources. The executor associates a verified existing Offer ID. If a semantic discount has no verified mapping, deployment fails closed instead of inventing an Offer ID or a nonexistent create-offer API.

## How do you prevent duplicate external resources?

The executor uses an application-level operation ledger with unique operation keys and canonical request hashes. Repeated confirmed deployment returns the existing recorded resource. Ambiguous outcomes remain unresolved and are not automatically retried.

## Why not use a generic Razorpay idempotency header?

The implementation does not invent an unsupported generic idempotency mechanism for Orders or Payment Links. Idempotency is owned at the application layer.

## Can a judge click “Start New Optimization Cycle” repeatedly and skip an experiment?

No. Rollover is allowed only after a terminal or safely undeployed cycle. Task 20 attempted rollover while the third experiment was active and received HTTP 409 `INVALID_TRANSITION`.

When rollover is legal, previous opportunities, experiments, results, resources, attempts, learned memory, and audit events remain preserved.

## Was Task 20 actually exercised against production?

Yes. The guarded `verify_production_v2.py` workflow ran against the deployed Render API and mutated exactly one new optimization cycle.

The verified path was:

`HYPOTHESIS_PROPOSED -> EXPERIMENT_PLANNED -> POLICY_APPROVED -> RESOURCE_DEPLOYED -> EXPERIMENT_BATCH_RUN x5 -> EXPERIMENT_EVALUATED`

The verifier ended with `TASK 20: PASS` and confirmed:

- trial count 2 -> 3
- Champion v1 -> v1
- memory-aware hypothesis: verified
- champion control: verified
- skip guard: verified
- terminal outcome: INCONCLUSIVE
- learning persistence: verified
- audit chain: valid

## Was there an earlier production failure?

Yes. The earlier pre-adaptive verification exposed a provider-side structured-output parsing exception before any experiment or resource existed. The diagnosis boundary was repaired and regression-tested, and the exact persisted opportunity was resumed instead of creating another cycle.

That failure history is preserved in `docs/PRODUCTION_VERIFICATION.md` because hiding it would make the release record less credible.

## What does the audit chain guarantee?

It provides application-level tamper evidence. Events store the previous hash and a SHA-256 hash over canonical event material. The frontend verifies the chain.

It is not a blockchain and does not claim protection against an administrator who can rewrite the whole database and recompute every hash.

## Why OpenRouter?

The diagnosis client uses an OpenAI-compatible SDK boundary. The hosted demo points that boundary at OpenRouter. Provider choice does not change downstream evidence validation, memory constraints, policy, execution, or statistics.

## Does the benchmark use the hosted LLM?

No. Benchmark diagnosis uses a deterministic evidence-only adapter so repeated benchmark runs remain reproducible and independent of provider availability or model changes.

## Why does the dashboard show 12,258 attempts instead of 6,112?

6,112 is the frozen canonical baseline. Experiment runtime appends simulated traffic, and completed cycles are deliberately preserved. After Task 20, the hosted dashboard contains 12,258 accumulated attempts across the baseline plus experimental traffic.

## What would you build next for production?

Real merchant onboarding, verified Razorpay Test Mode credentials, multi-tenant isolation, scheduled experiment execution, external-write reconciliation, stronger database concurrency controls, and evaluation on consented real merchant traffic.

Those are intentionally not faked in this submission.
