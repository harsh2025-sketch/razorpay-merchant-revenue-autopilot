# Controlled Razorpay Test Mode proof

Revenue Autopilot now has **application-side evidence of a real Razorpay Test Mode resource** in the hosted lifecycle, but the strongest independent proof is still the credential-gated create→fetch→rollback→cancel verifier described below.

## Current hosted evidence

Earlier hosted cycles used the explicit simulated adapter and generated `demo_plink_*` IDs.

A later hosted cycle currently records:

```text
opportunity: 2956c570-9504-40b6-9557-372fe7455ccc
experiment:  83b571f0-3520-4435-b279-7fad1f4e0efb
policy:      APPROVE
resource:    plink_TW3blQWQpXXHRL
resource UI: Razorpay Test Mode Payment Link
status:      active
result:      INCONCLUSIVE
p-value:     ~0.9917
```

The Audit Log also contains `RAZORPAY_RESOURCE_CREATED` for the same `plink_TW3blQWQpXXHRL`.

In this repository's simulated execution mode, Payment Link IDs are generated with a `demo_plink_*` prefix. A plain `plink_*` therefore constitutes **application-side evidence that the real Razorpay Test Mode client path returned a resource** for this hosted cycle.

### What this does not prove by itself

Our own database and UI are not an independent source. The hosted `plink_*` should still be confirmed in the Razorpay Test Mode dashboard/API before saying that resource has been externally verified.

The accurate statement before that external check is:

> The live application records a real Razorpay Test Mode `plink_*` from an approved executor path; independent external confirmation is pending.

## Strongest controlled proof

The repository includes:

```text
scripts/verify_razorpay_autopilot.py
```

This is a MANUAL verifier because it performs real external writes against Razorpay Test Mode.

It proves:

```text
persisted verification experiment
  -> deterministic merchant policy APPROVE
  -> Task 13 executor create
  -> real Razorpay Test Mode Payment Link
  -> persisted RazorpayResource + OperationExecution
  -> independent Razorpay fetch
  -> existing fixed-horizon runtime
  -> existing statistical ROLLBACK
  -> Task 13 rollback executor
  -> independent Razorpay cancellation fetch
  -> valid hash-chained audit trail
```

The controlled fixture uses the canonical harmful `ios_premium` short-expiry challenger. It raises only the fixed sample horizon for statistical power; it does not alter the causal effect, treatment parameters, alpha, practical-lift threshold, policy rules, executor rules, or statistical decision logic.

## Safety properties

- `RAZORPAY_EXECUTION_MODE` must be `real`.
- `RAZORPAY_KEY_ID` must begin with `rzp_test_`.
- Live-mode credentials are refused.
- Credentials are read only from the local environment / local `.env`.
- The proof database is temporary and deleted after the run.
- The proof creates one Test Mode treatment Payment Link.
- Normal cleanup uses the existing rollback executor.
- A final cleanup block attempts direct cancellation if the proof fails after resource creation.
- Secrets are never intentionally printed or persisted in the repository.

## Offline CI coverage

`backend/tests/test_razorpay_autopilot_verifier.py` runs the same proof orchestration with a small stateful fake Razorpay client while keeping the real:

- planner;
- merchant policy;
- executor persistence/idempotency;
- experiment runtime;
- statistical engine;
- rollback executor;
- audit verification.

That regression is useful because it proves the harness itself and the expected deterministic `ROLLBACK` fixture.

It does **not** prove an external network call to Razorpay.

## Run the controlled proof locally

Do not paste credentials into chat, commits, issues, screenshots, or source files.

PowerShell:

```powershell
$env:RAZORPAY_EXECUTION_MODE="real"
$env:RAZORPAY_KEY_ID="rzp_test_..."
$env:RAZORPAY_KEY_SECRET="<local secret>"
python scripts/verify_razorpay_autopilot.py
```

Bash:

```bash
export RAZORPAY_EXECUTION_MODE=real
export RAZORPAY_KEY_ID='rzp_test_...'
export RAZORPAY_KEY_SECRET='<local secret>'
python scripts/verify_razorpay_autopilot.py
```

## Required PASS evidence

A successful controlled run prints fields equivalent to:

```text
RAZORPAY AUTOPILOT TEST MODE PROOF: PASS
policy: APPROVE
razorpay_resource: plink_...
after_deploy_fetch_status: created
statistics: decision=ROLLBACK ...
rollback_operation: succeeded
after_rollback_fetch_status: cancelled
audit_chain_valid: True
```

The exact lift and p-value must come from the unchanged statistical engine; do not hand-edit them for a nicer demo.

## Evidence to capture for judging

Capture only non-secret evidence:

1. the application's persisted `plink_*` / Audit Log lifecycle;
2. independent Razorpay Test Mode dashboard/API evidence for the same resource when available;
3. for the controlled verifier, terminal `PASS`, the `plink_*`, policy approval, ROLLBACK, cancellation status, and audit-chain result.

Never expose the key secret. Avoid exposing a complete key ID if it is not needed.

## Claim ladder

Use these statements precisely:

**Already supported:**
> The repository contains a real Razorpay Test Mode client and deterministic executor.

**Already supported by the live application:**
> A later hosted approved cycle records `plink_TW3blQWQpXXHRL` as a Razorpay Test Mode Payment Link; simulated cycles use `demo_plink_*`.

**Only after external dashboard/API confirmation:**
> The hosted `plink_TW3blQWQpXXHRL` was independently confirmed in Razorpay Test Mode.

**Only after the verifier returns PASS:**
> The complete executor create→fetch→statistical ROLLBACK→cancel proof was verified against Razorpay Test Mode.
