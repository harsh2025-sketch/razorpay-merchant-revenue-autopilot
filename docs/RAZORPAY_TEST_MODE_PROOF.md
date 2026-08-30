# Controlled Razorpay Test Mode proof

The public hosted demo intentionally uses `RAZORPAY_EXECUTION_MODE=simulated`. A separate credential-gated verifier exists to prove the **real executor path** without changing the hosted demo database.

The verifier is:

```text
scripts/verify_razorpay_autopilot.py
```

It uses a temporary local SQLite database and one Razorpay **Test Mode** Payment Link. It refuses live-mode keys.

## What this proves

The script exercises the actual Revenue Autopilot boundaries:

```text
deterministic experiment plan
  -> persisted merchant policy APPROVE
  -> Task 13 executor
  -> real Razorpay Test Mode Payment Link
  -> persisted RazorpayResource + operation ledger
  -> independent Razorpay fetch
  -> existing fixed-horizon experiment runtime
  -> existing statistical ROLLBACK
  -> Task 13 rollback executor
  -> independent Razorpay cancellation fetch
  -> valid hash-chained audit trail
```

The verification fixture uses the canonical `ios_premium` short-expiry challenger because the sealed evaluation world already defines that intervention as harmful. The verifier increases only the fixed sample horizon to obtain adequate statistical power. It does **not** modify the causal effect, treatment parameters, alpha, practical-lift threshold, policy rules, executor rules, or statistical decision logic.

## Safety properties

- `RAZORPAY_EXECUTION_MODE` must be `real`.
- `RAZORPAY_KEY_ID` must begin with `rzp_test_`.
- Live-mode credentials are refused.
- Credentials are read from environment / local `.env`; they are never written into the repository.
- The verifier creates only one Test Mode treatment Payment Link.
- The normal proof path cancels the Payment Link through the existing rollback executor.
- A final cleanup block attempts direct cancellation if the normal proof fails after resource creation.
- The proof database is temporary and deleted after the run.

## Run locally

From the repository root, configure **Test Mode** credentials in your local environment. Do not paste them into source files, issues, commits, screenshots, or the submission form.

PowerShell example:

```powershell
$env:RAZORPAY_EXECUTION_MODE="real"
$env:RAZORPAY_KEY_ID="rzp_test_..."
$env:RAZORPAY_KEY_SECRET="<local secret>"
python scripts/verify_razorpay_autopilot.py
```

Bash example:

```bash
export RAZORPAY_EXECUTION_MODE=real
export RAZORPAY_KEY_ID='rzp_test_...'
export RAZORPAY_KEY_SECRET='<local secret>'
python scripts/verify_razorpay_autopilot.py
```

## Required PASS evidence

A successful run prints fields equivalent to:

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

The exact lift and p-value come from the deterministic runtime/statistics engine and should not be manually edited in documentation.

## Evidence to capture for judging

After a PASS run, capture only non-secret evidence:

1. terminal output showing `PASS`, the `plink_...` ID, policy approval, statistical decision, cancellation status, and audit-chain result;
2. Razorpay Test Mode dashboard/API evidence for the same `plink_...` ID;
3. confirmation that the resource is cancelled after rollback.

Never include the Razorpay key secret. Avoid showing the complete key ID if the capture does not need it.

## Truthfulness rule

Do not claim that real Razorpay Test Mode execution has been verified merely because this script exists or because its offline CI regression passes.

Use the phrase **"real Razorpay Test Mode proof verified"** only after this credential-gated script has actually completed with `PASS` and the matching Test Mode resource has been independently observed.

Until then, the accurate statement is:

> The repository contains a real Razorpay Test Mode executor and a controlled credential-gated proof harness; the public hosted demo remains explicitly simulated.
