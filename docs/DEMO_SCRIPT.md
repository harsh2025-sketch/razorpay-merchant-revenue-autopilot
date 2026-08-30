# Judge Demo Script

Target length: 2 to 3 minutes.

## Opening, 15 seconds

"Merchant Revenue Autopilot is an AI growth agent for Razorpay merchants, but the model is deliberately not trusted with authority. AI can propose an experiment. Deterministic policy authorizes it. The execution layer acts. Fixed-horizon statistics decides whether the treatment should be kept, rolled back, or marked inconclusive."

Open the live Overview page:

https://merchant-revenue-autopilot-psi.vercel.app/overview

## Step 1: show the merchant problem, 25 seconds

Point to:

- baseline conversion
- captured GMV
- weakest segment
- payment-method performance
- Autopilot state

Say:

"The system starts from payment data, not a chat prompt. Metrics and opportunity detection are deterministic. The detector compares a segment with the rest of the merchant cohort and creates evidence the model can reference."

## Step 2: open the completed cycle, 45 seconds

Open the `android_budget` cycle from Autopilot.

Point to Observed Evidence first.

Say:

"This section is observed evidence. The model cannot invent evidence keys because every reference in its response is validated against this catalog."

Move to the visible `AI ANALYSIS` trust divider.

Say:

"Only below this boundary does the LLM participate. In this run it proposed disabling UPI for the segment. That is a hypothesis, not an authorized action."

## Step 3: show deterministic control, 35 seconds

Point to Experiment Plan and Policy Authorization.

Say:

"The planner turns the hypothesis into a canonical test. Traffic exposure, sample size, metric, duration, and guardrails come from deterministic code. Then merchant policy authorizes or rejects the complete experiment. There is no override button and no silent parameter clamping."

If asked about an unsafe proposal, explain that policy returns explicit violation codes and stops the cycle.

## Step 4: disclose hosted execution honestly, 20 seconds

Point to the Simulated Payment Resource panel.

Say:

"The repository contains a real Razorpay Test Mode client and executor. The public hosted demo uses an explicit simulated execution adapter because this account could not obtain Test Mode API credentials without merchant onboarding and KYC. The product labels this clearly, uses `demo_...` IDs, and states that no Razorpay API request was made."

Do not claim the `demo_...` resource exists in the Razorpay dashboard.

## Step 5: show the statistical decision, 30 seconds

Point to the final Statistical Result.

Say:

"The experiment does not stop on an interim trend. It waits for the fixed sample horizon. This completed run produced 48.3% control conversion and 43.5% treatment conversion, but the p-value was 0.1939 and the confidence interval crossed zero. So the correct decision was INCONCLUSIVE, not rollback. The LLM does not participate in this decision."

This is a useful demo result because the system does not force a positive outcome to make the product look better.

## Step 6: show audit integrity, 20 seconds

Open Audit Log.

Say:

"Every lifecycle transition is merchant-visible and hash-chained: opportunity, AI diagnosis, plan, policy, execution, runtime, and statistics. The dashboard verifies the chain and exposes the event hashes."

## Step 7: finish with benchmark evidence, 25 seconds

Open `docs/evaluation/summary.md` in GitHub.

Say:

"The live cycle is one run, so I also built a frozen paired benchmark. Across five seeds, five merchant segments, and 5,000 contexts per segment, Autopilot averaged 59.39% conversion versus 58.18% with no optimization, a +1.22 percentage-point mean delta. The rule baseline was -0.52 points overall. These are synthetic deterministic results, not production revenue claims."

Close with:

"The contribution is not another AI recommendation dashboard. It is the control architecture around an AI revenue agent: evidence grounding, deterministic authorization, duplicate-safe execution, statistical decision-making, and tamper-evident audit."

## If the live backend is waking from Render free-tier sleep

Do not repeatedly click lifecycle actions. Refresh the Overview page once and wait for the backend to respond. Read-only pages are safe to retry. Mutation buttons should be clicked once per intended transition.

## Things not to claim

- Do not call hosted `demo_...` resources real Razorpay resources.
- Do not call synthetic benchmark GMV production revenue.
- Do not say the LLM decides experiment success.
- Do not say the audit chain is a blockchain.
- Do not imply the benchmark uses live OpenRouter responses. Its diagnosis adapter is deterministic for reproducibility.
