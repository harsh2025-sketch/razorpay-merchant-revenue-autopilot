# Task 21C — One-click fixed-horizon experiments

Task 21C removes the repeated `Run batch` interaction from the merchant product without weakening the existing safety boundaries.

## Product flow

```text
Policy APPROVE
    -> Razorpay treatment deployed
    -> Run Experiment
    -> deterministic Task 11 runtime advances to the fixed horizon
    -> unchanged Task 12 statistics engine evaluates once
    -> KEEP / ROLLBACK / INCONCLUSIVE
```

The dashboard uses one non-OpenAPI control endpoint:

```text
POST /api/v1/experiments/{experiment_id}/run-to-decision
```

The lower-level batch and evaluate endpoints remain available as engineering primitives; the merchant UI no longer requires repeated batch clicks.

## Safety invariants

The one-click service refuses to run unless:

- the experiment exists;
- a persisted merchant-policy decision is `APPROVE`;
- an active deployed treatment resource exists;
- the experiment is in an approved/running state;
- the configured sample target is positive.

The service does not call the LLM, policy engine, or Razorpay. It delegates simulated traffic only to the existing experiment runtime and delegates the final decision only to the existing statistics engine.

Each internal runtime call remains bounded by Task 11's existing maximum batch size. The whole operation also has a bounded number of internal runtime calls and fails visibly if no progress is made.

Once a statistical result exists, repeating the one-click request is idempotent: it returns the existing result and creates no new traffic.

## Rollback remains explicit

A statistical `ROLLBACK` result does not silently perform another external Razorpay mutation inside the run-to-decision request. The existing explicit rollback boundary remains responsible for cancelling the deployed treatment.

## Deliberate limitation

The current deterministic synthetic experiment runtime models the canonical TechBazaar evaluation merchant. Task 21C changes product interaction, not the causal simulator. Real-merchant runtime support is therefore an explicit Task 22 E2E limitation rather than something hidden inside this task.
