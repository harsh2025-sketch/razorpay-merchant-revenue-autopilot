# Submission Copy

## Project name

Merchant Revenue Autopilot

## One-line pitch

An AI revenue agent for Razorpay merchants where the model can propose an experiment, but deterministic policy, controlled execution, and fixed-horizon statistics decide what is actually allowed and whether it worked.

## Short description

Merchant Revenue Autopilot detects payment-conversion leakage, grounds an LLM diagnosis in observable evidence, converts the proposal into a bounded experiment, applies deterministic merchant policy, executes through a Razorpay boundary, measures the result at a fixed sample horizon, and records every transition in a tamper-evident audit chain.

The key design choice is separation of authority. The LLM cannot call Razorpay, cannot bypass merchant limits, cannot choose its own sample horizon, and cannot decide whether its treatment succeeded. The hosted demo uses an explicitly labelled simulated Razorpay execution adapter because Test Mode API credentials were unavailable without merchant onboarding/KYC; the real Test Mode client remains implemented in the repository.

## Problem

Merchants can lose conversion because payment behavior differs across customer segments, devices, order values, and payment methods. A naive AI agent can detect patterns, but giving that same probabilistic component direct authority over discounts, payment configuration, external writes, and success criteria introduces financial and operational risk.

## Solution

Revenue Autopilot separates the loop into observable evidence, probabilistic hypothesis generation, deterministic planning, deterministic policy authorization, controlled execution, fixed-horizon measurement, statistical decision-making, and tamper-evident audit.

## What is novel here

The project treats an AI growth agent as a controlled decision system rather than a chat interface or unrestricted tool-calling agent.

The important boundaries are:

- evidence references are validated against a deterministic catalog
- the LLM only proposes semantic interventions
- experiment structure is deterministic
- merchant policy owns authorization
- application-level idempotency protects external writes
- ambiguous writes are not auto-retried
- experiment success is decided statistically, not by the LLM
- benchmark selection is separated from the sealed causal scoring world
- lifecycle events are SHA-256 hash-chained per merchant

## Razorpay usage

The repository implements Razorpay Test Mode client methods for Payment Links and Orders, including payment-method configuration, partial payment, expiry configuration, existing Offer association, fetch, and cancellation paths.

Razorpay Offers are not fabricated or created through a nonexistent API path. Offer-based deployment fails closed until a verified pre-created Test Mode Offer ID is mapped.

The public hosted demo runs `RAZORPAY_EXECUTION_MODE=simulated` because merchant API credentials require onboarding/KYC unavailable to this submission account. Simulated resources are visibly marked and use `demo_...` IDs. The UI explicitly states that no Razorpay API request was made.

## AI usage

The diagnosis boundary uses the OpenAI Python SDK with an OpenAI-compatible endpoint. The hosted demo is configured through OpenRouter. The model receives only merchant-visible opportunity evidence and returns a structured hypothesis.

Model output is re-validated by deterministic code before persistence.

## Evaluation

A frozen synthetic benchmark compares:

- no optimization
- random intervention
- rule-based intervention
- Autopilot

The canonical run uses five fixed seeds, five segments, and 5,000 paired contexts per segment per seed.

Overall mean conversion:

| Strategy | Mean conversion | Mean delta vs control |
| --- | ---: | ---: |
| Autopilot | 59.39% | +1.22 pp |
| Random intervention | 59.20% | +1.02 pp |
| No optimization | 58.18% | 0.00 pp |
| Rule based | 57.65% | -0.52 pp |

Autopilot recorded 5 policy rejections instead of forcing every proposal into deployment.

These are synthetic deterministic results in the frozen evaluation world, not production revenue evidence.

## Live links

Frontend:
https://merchant-revenue-autopilot-psi.vercel.app

Backend health:
https://merchant-revenue-autopilot-api.onrender.com/health

Evaluation summary:
`docs/evaluation/summary.md`

## Stack

Frontend: Next.js App Router, TypeScript, Tailwind CSS, Recharts, Lucide

Backend: Python 3.12, FastAPI, SQLAlchemy 2, Pydantic v2, httpx, OpenAI-compatible SDK

Database: Supabase PostgreSQL in the hosted deployment, SQLite supported locally

Hosting: Vercel frontend, Render backend

Testing: pytest, Vitest, Testing Library, TypeScript typecheck, ESLint, Next.js production build

## Demo merchant

TechBazaar Electronics is a deterministic synthetic consumer-electronics merchant profile. The canonical baseline contains 6,112 payment attempts across android_budget, android_mid, ios_premium, repeat_buyer, and web_general segments.

## Safety and failure behavior

The system fails closed when:

- the model output is malformed
- evidence references are invalid
- an intervention is not allowed
- exposure or discount limits are exceeded
- an Offer is not safely mapped
- external write state is ambiguous
- the experiment has not reached its sample horizon

No unsafe proposal is silently rewritten into an approved one.

## Current demonstrated cycle

The public demo contains a completed `android_budget` cycle. The detector found a segment conversion gap, the hosted LLM proposed a payment-method configuration experiment, merchant policy approved the bounded plan, the hosted simulated execution boundary created a clearly labelled demo resource, and the fixed-horizon statistical engine returned `INCONCLUSIVE` with p = 0.1939.

That outcome is intentionally preserved because an honest inconclusive result is stronger evidence of the architecture than forcing the demo to produce a positive treatment result.

## Limitations

The hosted payment resource is simulated, not a real Razorpay dashboard object.

The benchmark is synthetic and deterministic, not production traffic.

The benchmark diagnosis adapter is deterministic for reproducibility and is not a live OpenRouter call.

The audit chain provides application-level tamper evidence, not distributed consensus.

The current product demonstrates one canonical merchant rather than complete multi-tenant merchant onboarding.
