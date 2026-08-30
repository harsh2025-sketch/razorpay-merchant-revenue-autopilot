# Razorpay Merchant Revenue Autopilot

**A controlled AI revenue-optimization system where AI proposes experiments from payment evidence, deterministic policy authorizes them, the execution boundary acts, fixed-horizon statistics decides the outcome, and persisted memory shapes the next cycle.**

**Live demo:** https://merchant-revenue-autopilot-psi.vercel.app  
**Merchant Intelligence:** https://merchant-revenue-autopilot-psi.vercel.app/intelligence  
**Backend health:** https://merchant-revenue-autopilot-api.onrender.com/health  
**Final demo script:** [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md)  
**Judge Q&A:** [`docs/JUDGE_QA.md`](docs/JUDGE_QA.md)  
**Submission copy:** [`docs/SUBMISSION.md`](docs/SUBMISSION.md)

---

## The product in 30 seconds

Razorpay already processes a merchant's payments. Revenue Autopilot sits one layer above that payment evidence and asks:

> Where is conversion leaking, what bounded configuration change is worth testing, is that test safe for this merchant, and did it actually work?

The answer moves through explicit trust boundaries:

```text
payment evidence
    ↓
deterministic metrics + opportunity detection
    ↓
deterministic opportunity portfolio
    ↓
structured prior experiment memory
    ↓
evidence-grounded LLM diagnosis
    ↓
deterministic stale-repeat validation
    ↓
champion-aware experiment planner
    ↓
deterministic merchant policy
    ↓
Razorpay execution boundary
    ↓
fixed-horizon measurement
    ↓
KEEP / ROLLBACK / INCONCLUSIVE
    ↓
champion + learned memory + hash-chained audit
    ↓
next optimization cycle
```

> **AI proposes. Deterministic policy authorizes. The execution boundary acts. Statistics decides. Persisted outcomes shape the next cycle.**

The LLM never decides whether an experiment is safe, never calls Razorpay directly, never chooses its own statistical success rule, and never promotes its own treatment.

---

## Why this is not "LLM + dashboard"

| Failure mode in a naive AI growth agent | Revenue Autopilot boundary |
| --- | --- |
| Model intuition decides what matters | Deterministic opportunity portfolio |
| Hallucinated reasoning becomes action | Structured output + evidence validation |
| Agent repeats failed ideas forever | Persisted experiment memory + stale-repeat checks |
| Model chooses unsafe financial parameters | Deterministic merchant policy |
| Retried external writes create duplicates | Operation ledger + request hashing + ambiguous-write protection |
| Agent declares its own idea successful | Fixed-horizon statistics alone decides the result |
| A past winner is forgotten | KEEP-derived champion becomes future control |
| Synthetic evaluation is presented as real merchant evidence | Explicit real-vs-synthetic measurement boundary |
| Demo history is difficult to trust | Append-only SHA-256 hash-chained audit |

---

## What is implemented

### Adaptive optimization

- deterministic conversion/GMV/segment/payment-method metrics
- deterministic conversion-divergence opportunity detection
- deterministic opportunity portfolio / next-best opportunity
- structured terminal experiment memory
- evidence-grounded LLM diagnosis
- schema + evidence + semantic validation
- deterministic stale-repeat prevention
- champion-aware experiment planning
- deterministic merchant policy
- application-level external-write idempotency
- fixed-horizon statistical evaluation
- KEEP-derived champion promotion
- history-preserving repeat cycles
- Merchant Intelligence read model/UI
- tamper-evident audit chain

### Merchant data path

Revenue Autopilot now supports more than the canonical demo merchant:

- create a merchant from canonical CSV payment history;
- append later transaction revisions;
- deterministically deduplicate repeated external rows;
- preserve previous observations rather than replaying them as new evidence;
- run metrics, detection, ranking, diagnosis, planning, and policy on uploaded merchant evidence.

### One-click canonical experiment interaction

For TechBazaar's sealed evaluation environment, the merchant UI exposes one bounded **Run Experiment** operation instead of repeated batch clicks.

The one-click service still requires:

- persisted policy `APPROVE`;
- an active deployed treatment resource;
- the existing Task 11 runtime;
- the existing Task 12 statistical engine.

It does not call the LLM, rerun policy, or silently perform Razorpay rollback. Repeated requests after a result are idempotent.

---

## Real merchant vs synthetic evaluation boundary

This distinction is deliberate and important.

### TechBazaar

TechBazaar is the canonical deterministic evaluation merchant. Its hidden causal model exists so the system can be tested against known but concealed intervention effects.

For this merchant only:

```text
approved treatment
  -> sealed deterministic experiment runtime
  -> fixed horizon
  -> statistics
```

### Uploaded merchant

An uploaded merchant's historical observations may feed detection and reasoning, but Revenue Autopilot **will not manufacture experimental outcomes using TechBazaar's hidden causal world**.

At measurement time the product enters:

> **Awaiting live outcomes**

and the backend returns:

```text
LIVE_EXPERIMENT_TRAFFIC_REQUIRED
```

Zero synthetic experiment attempts and zero invented `ExperimentResult` rows are generated for that merchant.

A production checkout/payment-event integration must provide authoritative assigned control/treatment outcomes before the existing statistics engine can evaluate the experiment.

See [`docs/task-22-e2e-boundaries.md`](docs/task-22-e2e-boundaries.md).

---

## Razorpay integration

The repository contains a real Razorpay Test Mode HTTP client and deterministic executor for Payment Links and Orders.

Supported execution behavior includes:

- payment-method configuration;
- partial payments;
- expiry configuration;
- verified existing Offer association where supported;
- independent resource fetch;
- cancellation/rollback;
- application-level idempotency;
- fail-closed ambiguous external writes.

The executor only accepts a persisted merchant-policy `APPROVE`. It does not rerun the model or policy and does not use the synthetic causal model.

### Hosted demo disclosure

The public deployment currently uses:

```text
RAZORPAY_EXECUTION_MODE=simulated
```

Hosted resources therefore use `demo_...` IDs and the product explicitly states that those resources do not exist in the Razorpay dashboard.

Do not present a `demo_...` ID as a real Razorpay resource.

### Controlled real Test Mode proof

The repository includes:

```text
scripts/verify_razorpay_autopilot.py
```

The credential-gated verifier proves the exact executor chain using one temporary Test Mode Payment Link:

```text
persisted experiment
  -> deterministic policy APPROVE
  -> real executor deploy
  -> real plink_...
  -> persisted resource + operation ledger
  -> independent Razorpay fetch
  -> unchanged fixed-horizon runtime/statistics
  -> genuine ROLLBACK on controlled harmful-expiry fixture
  -> real executor cancellation
  -> independent cancellation fetch
  -> valid audit chain
```

It:

- requires `RAZORPAY_EXECUTION_MODE=real`;
- accepts only `rzp_test_...` key IDs;
- refuses live-mode keys;
- uses a temporary local database;
- attempts cleanup if the normal proof path fails after resource creation.

The proof harness itself is covered by an offline CI regression with a stateful fake Razorpay client while keeping the real planner, policy, executor persistence/idempotency, runtime, statistics, rollback, and audit logic.

**Important:** offline CI proves the harness/domain chain, not an external Test Mode request. Claim external Razorpay Test Mode verification only after the credential-gated script actually returns `PASS` and the matching `plink_...` has been independently observed.

See [`docs/RAZORPAY_TEST_MODE_PROOF.md`](docs/RAZORPAY_TEST_MODE_PROOF.md).

---

## Adaptive memory and champion–challenger

Terminal experiment state is reconstructed from persisted domain truth rather than a second opaque "AI memory" database.

Memory contains, per segment/intervention:

- treatment configuration/fingerprint;
- merchant-policy outcome;
- KEEP / ROLLBACK / INCONCLUSIVE;
- absolute/relative lift;
- p-value and confidence interval;
- resource state;
- prior terminal-trial count.

Deterministic code then enforces:

- exact policy-rejected configurations stay blocked;
- exact prior ROLLBACK/INCONCLUSIVE proposals are blocked when evidence is materially unchanged;
- reconsideration requires explicit material evidence change;
- one bounded corrective LLM attempt is allowed before failing closed.

Champion state is also derived from persisted statistical truth:

- baseline merchant configuration = Champion v1;
- only `KEEP` promotes a treatment;
- later experiments of that intervention type inherit the champion as control;
- `ROLLBACK` / `INCONCLUSIVE` leave champion unchanged;
- an identical challenger is rejected as meaningless.

---

## Deterministic opportunity portfolio

Untouched opportunities are ranked without an LLM using observable evidence such as conversion gap, segment volume, captured average order value, merchant-policy feasibility, and prior terminal trials.

The transparent history term is:

```text
history_factor = 1 / (1 + prior_terminal_trials)
```

Any displayed GMV value is an **opportunity-sizing proxy**. It is not realized revenue, profit, or a causal forecast.

Partially started cycles resume before a new candidate is ranked.

---

## Fixed-horizon statistics

The experiment does not stop just because an interim result looks favorable.

Task 12 evaluates only after both variants reach the configured sample target and returns:

- `KEEP`
- `ROLLBACK`
- `INCONCLUSIVE`

using a deterministic two-proportion test plus practical absolute-lift threshold.

The LLM does not participate in the statistical decision.

---

## Preserved hosted verification snapshot

Task 20 exercised a third adaptive optimization cycle against the hosted deployment and preserved all earlier history.

Snapshot after that verification:

- terminal trials: **3**
- hosted statistical outcomes: **3 INCONCLUSIVE**
- promoted treatments: **0**
- champion: **v1 / merchant baseline**
- audit chain: **valid**

Final preserved experiment:

```text
opportunity: 0e500ccd-6c3d-4ade-a06c-afc3d2cd24e6
experiment:  5277a2df-c1a5-4009-9320-c97c3576ff38
resource:    demo_plink_0a6348797891d7c8
```

Fixed-horizon result:

| Metric | Result |
| --- | ---: |
| Control samples | 1,895 |
| Treatment samples | 200 |
| Control conversion | 47.2% |
| Treatment conversion | 45.5% |
| Absolute lift | -1.7 pp |
| p-value | 0.6412 |
| Decision | `INCONCLUSIVE` |

Because the experiment did not earn `KEEP`, Champion v1 correctly remained unchanged.

These values are the **Task 20 verification snapshot**, not guaranteed permanent live counters.

---

## Frozen evaluation benchmark

A separate deterministic benchmark compares four strategies on identical paired synthetic contexts:

- `NO_OPTIMIZATION`
- `RANDOM_INTERVENTION`
- `RULE_BASED`
- `AUTOPILOT`

Canonical configuration:

- 5 fixed seeds
- 5 segments
- 5,000 paired contexts per segment per seed

| Strategy | Mean conversion | Mean delta vs control |
| --- | ---: | ---: |
| **AUTOPILOT** | **59.39%** | **+1.22 pp** |
| RANDOM_INTERVENTION | 59.20% | +1.02 pp |
| NO_OPTIMIZATION | 58.18% | 0.00 pp |
| RULE_BASED | 57.65% | -0.52 pp |

These are **synthetic deterministic evaluation results, not production revenue claims**.

See [`docs/evaluation/summary.md`](docs/evaluation/summary.md).

---

## Product surfaces

The Next.js operations console exposes:

- `/overview` — merchant metrics, lifecycle state, segment/payment-method evidence
- `/intelligence` — champion state, learned experiment memory, opportunity portfolio
- `/autopilot` — current and historical optimization cycles
- `/autopilot/[opportunityId]` — evidence → AI → plan → policy → execution → statistics
- `/audit` — expandable hash-chained audit history
- `/onboarding` — merchant creation + initial CSV history
- `/data` — incremental payment-data append

The cycle view visibly separates **Observed Evidence** from **AI Analysis** and labels the statistical decision independently from the LLM.

---

## Failure behavior worth testing

Revenue Autopilot fails closed for cases including:

- malformed structured LLM output;
- invalid evidence references;
- unchanged stale failed/inconclusive proposals;
- exact policy-rejected repeats;
- unsupported intervention configuration;
- excessive exposure/discount/financial exposure;
- missing verified Offer mapping;
- ambiguous external write state;
- insufficient fixed-horizon samples;
- attempted rollover of an active/deployed cycle;
- challenger identical to champion;
- uploaded merchant attempting to use synthetic experiment outcome generation.

No unsafe proposal is silently rewritten into an approved proposal.

---

## Local development

### Backend

Requires Python 3.12+.

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload
```

Optional canonical demo bootstrap from repository root:

```bash
python scripts/bootstrap_demo.py
```

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

Default local frontend: `http://localhost:3000`  
Default local backend: `http://localhost:8000`

---

## Configuration

Backend:

```text
APP_ENV=production
DATABASE_URL=<PostgreSQL or SQLite URL>
CORS_ALLOWED_ORIGINS=<comma-separated frontend origins>
OPENAI_API_KEY=<LLM provider key>
OPENAI_MODEL=<model/router name>
RAZORPAY_EXECUTION_MODE=real|simulated
RAZORPAY_KEY_ID=<required in real mode>
RAZORPAY_KEY_SECRET=<required in real mode>
RAZORPAY_TEST_OFFER_ID=<optional verified pre-created Test Mode Offer ID>
```

Frontend:

```text
NEXT_PUBLIC_API_BASE_URL=<backend HTTPS origin>
API_INTERNAL_BASE_URL=<backend HTTPS origin>
```

Never put backend secrets in `NEXT_PUBLIC_` variables or commit credentials.

---

## Verification

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

Controlled Razorpay Test Mode proof, from repository root after configuring **Test Mode** credentials locally:

```bash
python scripts/verify_razorpay_autopilot.py
```

Do not run write-capable hosted production verifiers casually after submission freeze.

---

## Current limitations

- the public hosted Razorpay adapter is simulated;
- external Test Mode execution should only be claimed after the credential-gated proof actually passes;
- uploaded merchants do not yet have authoritative checkout assignment + Razorpay payment-event/webhook ingestion for live experiment outcomes;
- authentication, tenant authorization, and merchant credential management are not production-complete;
- scheduled/background experiment progression is not production-complete;
- synthetic benchmark results do not establish external validity across Razorpay's merchant population;
- the application hash chain is tamper-evident audit infrastructure, not a blockchain.

---

## Next production work

1. authoritative control/treatment assignment at checkout;
2. Razorpay payment-event/webhook ingestion bound to persisted assignments;
3. authentication + tenant/credential isolation;
4. scheduled/background experiment progression;
5. external-write reconciliation and stronger concurrent database controls;
6. consented real-merchant evaluation before production uplift claims.

Merchant CSV onboarding, incremental append/dedup, one-click canonical experiment interaction, and real-vs-synthetic measurement separation are already implemented.

---

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — system/trust architecture
- [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) — final 5-minute judge flow
- [`docs/JUDGE_QA.md`](docs/JUDGE_QA.md) — architecture defense / judge questions
- [`docs/SUBMISSION.md`](docs/SUBMISSION.md) — final submission copy
- [`docs/RAZORPAY_TEST_MODE_PROOF.md`](docs/RAZORPAY_TEST_MODE_PROOF.md) — credential-gated external proof
- [`docs/task-22-e2e-boundaries.md`](docs/task-22-e2e-boundaries.md) — real-vs-synthetic measurement boundary
- [`docs/evaluation/summary.md`](docs/evaluation/summary.md) — frozen evaluation
- [`docs/PRODUCTION_VERIFICATION.md`](docs/PRODUCTION_VERIFICATION.md) — hosted verification history
