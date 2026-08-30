# Task 22 — End-to-end merchant measurement boundaries

Task 22 verifies the product after Tasks 21A–21C without weakening the separation between real merchant evidence and the sealed TechBazaar evaluation world.

## Two deliberately different paths

### Canonical TechBazaar demo/evaluation merchant

```text
historical TechBazaar observations
  -> deterministic detection
  -> evidence-grounded diagnosis
  -> deterministic planning
  -> merchant policy
  -> Razorpay execution boundary
  -> Run Experiment
  -> sealed deterministic Task 11 runtime
  -> fixed-horizon Task 12 statistics
  -> KEEP / ROLLBACK / INCONCLUSIVE
```

TechBazaar is the only merchant for which the repository's sealed synthetic causal model is valid.

### Merchant created from uploaded payment history

```text
uploaded real historical observations
  -> deterministic detection
  -> evidence-grounded diagnosis
  -> deterministic planning
  -> merchant policy
  -> Razorpay execution boundary
  -> await assigned real experiment outcomes
  -> fixed-horizon statistics only after those outcomes exist
```

An uploaded merchant must **never** be routed into TechBazaar's synthetic causal simulator. Doing so would manufacture experimental evidence and would make the resulting conversion lift or p-value invalid for that merchant.

## Current production boundary

The repository does not yet contain a merchant-checkout routing/webhook integration that assigns real customers to control/treatment and persists their resulting Razorpay payment events as experiment observations.

Therefore, after an uploaded merchant reaches the measurement stage:

- the one-click synthetic runtime is blocked;
- the API returns `LIVE_EXPERIMENT_TRAFFIC_REQUIRED`;
- zero synthetic experiment `PaymentAttempt` rows are generated;
- no `ExperimentResult` is invented;
- the dashboard shows **Awaiting live outcomes** instead of a `Run Experiment` mutation.

This is a deliberate fail-closed state, not a simulated claim of production uplift.

## What remains for genuine live experimentation

A production Razorpay integration would need to supply an authoritative experiment-observation path that:

1. assigns eligible customers deterministically to control/treatment before checkout;
2. routes only treatment-assigned customers through the deployed treatment resource/configuration;
3. ingests authoritative Razorpay payment outcomes (for example from the merchant integration/webhook stream);
4. binds each outcome to the persisted experiment and assigned variant without trusting client-supplied variant labels;
5. deduplicates external payment events;
6. lets the existing fixed-horizon statistics engine evaluate only after both cohorts reach the configured target.

Task 22 does not fake this missing checkout/webhook surface by reusing TechBazaar's hidden causal effects.

## Regression contract

Task 22 must preserve all of the following:

- TechBazaar one-click experiments continue to reach a deterministic fixed-horizon decision.
- Uploaded merchants with APPROVE + deployed treatment generate zero synthetic experiment traffic.
- Uploaded merchants receive the stable `LIVE_EXPERIMENT_TRAFFIC_REQUIRED` boundary.
- Policy, Razorpay execution, statistics, audit, and causal-model code remain unchanged by the real-merchant guard.
- Historical uploaded payment data remains usable for detection and diagnosis; only experimental outcome generation is blocked.
