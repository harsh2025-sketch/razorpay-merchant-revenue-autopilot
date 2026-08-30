# Judge Q and A

## What is the actual AI contribution?

The LLM performs diagnosis and hypothesis generation from a deterministic evidence catalog. It decides what intervention might explain or improve a detected conversion problem. It does not own planning, policy, execution, or statistical success criteria.

## Why not let the LLM call Razorpay directly?

Because the same model that generates a hypothesis should not also be trusted to authorize a financial or checkout change. The system keeps authority in deterministic policy and execution layers that can be tested independently.

## Is this just an A/B testing dashboard?

No. The experiment UI is only the visible surface. The backend contains a full decision pipeline: opportunity detection, evidence grounding, structured LLM diagnosis, deterministic planning, policy authorization, idempotent execution, sticky assignment, fixed-horizon statistics, rollback semantics, repeatable cycle rollover, and hash-chained audit.

## What happens when the model gives a bad answer?

The proposal must match the strict schema, use a supported intervention, contain valid parameters, and reference only evidence keys present in the catalog. Invalid output is rejected before a hypothesis is persisted.

The hosted OpenAI-compatible boundary also handles provider-side structured-output parse failures fail-closed. It permits one bounded diagnosis-only retry because no hypothesis or payment action exists yet; repeated malformed output becomes an AI-output error rather than a generic server failure.

A schema-valid but commercially unsafe proposal can still be rejected later by merchant policy.

## What prevents a dangerous discount or exposure level?

The policy engine checks the complete planned experiment against merchant limits. The LLM cannot change those limits. The system does not silently clamp an unsafe proposal into a different approved proposal.

## Why fixed-horizon statistics?

Repeatedly looking at interim p-values and stopping when a treatment looks good inflates false positives. The demo waits until the predefined sample target is reached and then runs the fixed statistical decision once.

## Why did the latest live experiment end INCONCLUSIVE?

The latest production-verified cycle measured 46.9% control conversion and 46.0% treatment conversion. The absolute lift was about -0.9 percentage points, p = 0.8071, and the 95% confidence interval ran from roughly -8.2 to +6.4 points. The evidence was nowhere near the deterministic rollback threshold, so `INCONCLUSIVE` is the correct result.

The earlier preserved cycle also ended `INCONCLUSIVE`, with a different intervention and result. The system does not rewrite history when a new cycle starts.

## Why is an inconclusive demo result useful?

Because the architecture is supposed to prevent the agent from declaring victory or failure without evidence. A demo that honestly preserves an inconclusive result is stronger evidence of that control boundary than forcing a positive outcome for presentation.

## Does the benchmark prove production revenue lift?

No. It is a synthetic deterministic benchmark over a frozen causal world. It measures how the strategies behave under controlled paired conditions. The repository explicitly avoids calling those results production revenue, ROI, or profit.

## What did the benchmark show?

Across five seeds and five canonical segments with 5,000 paired contexts per segment, Autopilot averaged 59.39% conversion versus 58.18% for no optimization, a +1.22 percentage-point mean delta. Random intervention averaged +1.02 points and the rule baseline averaged -0.52 points.

Autopilot also recorded five policy rejections instead of deploying every proposal.

## Why does random intervention sometimes do well?

The hidden world contains real positive interventions, so a random strategy can occasionally hit them. That is intentional. The benchmark is more credible when baselines can succeed by chance rather than being engineered to always lose.

The value of Autopilot is that it combines evidence-based selection with policy constraints and an auditable execution path.

## Why is android_budget zero lift in the canonical benchmark when the live detector focuses on it?

Detection means there is an observable conversion gap, not that every proposed intervention is guaranteed to be safe or causally beneficial. In the canonical benchmark, Autopilot does not deploy a treatment for that segment after policy gating, so its scored delta remains zero. This demonstrates that the system can identify a problem without forcing an action.

The hosted live cycle is separate from the frozen benchmark and may generate a different evidence-grounded hypothesis through the live LLM boundary.

## Is Razorpay actually integrated?

Yes, the repository implements the real Razorpay Test Mode client and executor boundary for Payment Links and Orders. It supports payment-method configuration, partial payment, expiry configuration, pre-created Offer association, fetch, and cancellation semantics.

The public hosted demo uses an explicit simulated adapter because the account could not obtain Test Mode API credentials without merchant onboarding/KYC. The simulated mode is clearly labelled in the UI and makes no Razorpay HTTP request.

## Why not fake a Razorpay resource for the demo?

The project deliberately does not do that. Simulated resource IDs use the `demo_...` namespace, and the product states that they do not exist in the Razorpay dashboard. The latest verified cycle created `demo_plink_8b6f752dd2126b8a` through the same authorized/idempotent execution path but with the simulated adapter.

## Why is Offer creation not automated?

Razorpay Offers are pre-created dashboard resources for this integration path. The executor only associates a verified existing Offer ID. If a semantic discount has no verified mapping, deployment fails closed instead of guessing an Offer ID or inventing a create-offer API.

## How do you prevent duplicate external resources?

The executor uses a local operation ledger with unique application-level operation keys. A repeated confirmed deployment returns the existing recorded resource. An ambiguous external outcome is kept unresolved and is not automatically retried.

## Why not use a generic Razorpay idempotency header?

The implementation does not invent an unsupported generic idempotency mechanism for Orders or Payment Links. It owns idempotency at the application layer.

## Can a judge keep clicking "Start New Optimization Cycle" and skip an experiment?

No. Rollover is allowed only after a terminal or safely undeployed cycle. The production verification explicitly tested this twice on the latest cycle: before continuing it and again after treatment deployment. Both attempts were rejected with HTTP 409 `INVALID_TRANSITION` while the cycle was active.

When rollover is legitimately allowed, the prior opportunity, experiment, statistical result, resource, payment attempts, and audit events remain preserved.

## Was the hosted flow actually exercised end to end?

Yes. A guarded production verification created a second opportunity and initially exposed a production-only structured-output parsing bug before any experiment or resource was created. After the diagnosis boundary was repaired and regression-tested, the verifier resumed that exact persisted opportunity rather than creating a third cycle.

The successful hosted path was:

`HYPOTHESIS_PROPOSED -> EXPERIMENT_PLANNED -> POLICY_APPROVED -> RESOURCE_DEPLOYED -> EXPERIMENT_BATCH_RUN x4 -> EXPERIMENT_EVALUATED`

It finished `COMPLETED / INCONCLUSIVE`, preserved the earlier cycle, used only a `demo_` resource, and kept the merchant audit chain valid.

## What does the audit chain guarantee?

It provides application-level tamper evidence. Every event stores the previous event hash and a SHA-256 hash over canonical event material. The frontend verifies the chain and exposes integrity state.

It is not a blockchain and does not claim protection against an attacker who can rewrite the database and recompute every hash.

## Why OpenRouter?

The diagnosis client uses the OpenAI-compatible SDK boundary. The hosted demo points that boundary at OpenRouter because the submission account did not have OpenAI API credit. Provider choice does not change the downstream validation, policy, experiment, or statistics layers.

## Is the benchmark using the hosted LLM?

No. Benchmark diagnosis uses a deterministic evidence-only adapter so repeated benchmark runs are reproducible and do not depend on provider availability, model updates, temperature, latency, or API spend.

## Why does the dashboard show more than 6,112 payment attempts?

6,112 is the frozen canonical baseline. The experiment runtime appends simulated experimental traffic and completed cycles are deliberately preserved. The hosted dashboard therefore accumulates additional attempts across verification runs instead of resetting history. The latest verified dashboard contained 10,163 total attempts.

## What would you build next for production?

The next production steps would be real merchant onboarding, verified Razorpay Test Mode credentials, multi-tenant isolation, experiment scheduling instead of button-driven batches, external-write reconciliation tooling, stronger database concurrency guarantees for the audit chain, and evaluation on consented real merchant traffic.

Those are intentionally not faked in this submission.
