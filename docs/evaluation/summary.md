# Merchant Revenue Autopilot - Evaluation Summary

This is a **synthetic deterministic evaluation**, not production revenue evidence.

- Causal model fingerprint: `05642a18eb14a7d4a0ff3beebf892253f3eed68efeea4f25cd56237c64e0750d`
- Seeds: `20260827, 20260828, 20260829, 20260830, 20260831`
- Cohort size per segment: `5000`
- Cohorts are paired: every strategy receives the same generated amount, payment method, device, source, and customer reference for each seed and segment.

Conversion deltas below are **percentage-point (pp) differences versus the paired no-optimization control**, not relative percent lift.

## Strategy comparison

| Strategy | Mean conversion | Mean delta vs control | Total captured GMV delta (paise) | Mean captured GMV delta (paise) |
| --- | ---: | ---: | ---: | ---: |
| AUTOPILOT | 59.39% | +1.22 pp | 1,364,485,900 | 54,579,436.00 |
| NO_OPTIMIZATION | 58.18% | 0.00 pp | 0 | 0.00 |
| RANDOM_INTERVENTION | 59.20% | +1.02 pp | 447,762,800 | 17,910,512.00 |
| RULE_BASED | 57.65% | -0.52 pp | -996,255,200 | -39,850,208.00 |

## Per-segment results

Positive/negative seed counts refer to signed conversion delta versus control.

| Segment | Strategy | Runs | Mean conversion | Mean delta | Mean GMV delta (paise) | Positive seeds | Negative seeds |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| android_budget | AUTOPILOT | 5 | 48.20% | 0.00 pp | 0.00 | 0 | 0 |
| android_budget | NO_OPTIMIZATION | 5 | 48.20% | 0.00 pp | 0.00 | 0 | 0 |
| android_budget | RANDOM_INTERVENTION | 5 | 48.02% | -0.18 pp | -1,560,240.00 | 1 | 1 |
| android_budget | RULE_BASED | 5 | 48.20% | 0.00 pp | 0.00 | 0 | 0 |
| android_mid | AUTOPILOT | 5 | 52.79% | +0.41 pp | 8,053,600.00 | 4 | 1 |
| android_mid | NO_OPTIMIZATION | 5 | 52.38% | 0.00 pp | 0.00 | 0 | 0 |
| android_mid | RANDOM_INTERVENTION | 5 | 55.42% | +3.05 pp | 35,093,560.00 | 4 | 0 |
| android_mid | RULE_BASED | 5 | 52.79% | +0.41 pp | 8,053,600.00 | 4 | 1 |
| ios_premium | AUTOPILOT | 5 | 73.92% | +0.14 pp | 19,707,040.00 | 4 | 1 |
| ios_premium | NO_OPTIMIZATION | 5 | 73.78% | 0.00 pp | 0.00 | 0 | 0 |
| ios_premium | RANDOM_INTERVENTION | 5 | 72.71% | -1.07 pp | -76,381,180.00 | 1 | 3 |
| ios_premium | RULE_BASED | 5 | 70.96% | -2.82 pp | -212,674,160.00 | 0 | 5 |
| repeat_buyer | AUTOPILOT | 5 | 72.34% | +5.52 pp | 241,775,480.00 | 5 | 0 |
| repeat_buyer | NO_OPTIMIZATION | 5 | 66.82% | 0.00 pp | 0.00 | 0 | 0 |
| repeat_buyer | RANDOM_INTERVENTION | 5 | 69.41% | +2.59 pp | 114,629,640.00 | 3 | 1 |
| repeat_buyer | RULE_BASED | 5 | 66.61% | -0.21 pp | 5,369,520.00 | 1 | 4 |
| web_general | AUTOPILOT | 5 | 49.71% | +0.01 pp | 3,361,060.00 | 2 | 3 |
| web_general | NO_OPTIMIZATION | 5 | 49.70% | 0.00 pp | 0.00 | 0 | 0 |
| web_general | RANDOM_INTERVENTION | 5 | 50.42% | +0.72 pp | 17,770,780.00 | 3 | 1 |
| web_general | RULE_BASED | 5 | 49.70% | 0.00 pp | 0.00 | 0 | 0 |

## Policy rejection counts

- `AUTOPILOT`: `5` rejected proposals
- `NO_OPTIMIZATION`: `0` rejected proposals
- `RANDOM_INTERVENTION`: `7` rejected proposals
- `RULE_BASED`: `10` rejected proposals

## Limitations

- Outcomes come from the sealed synthetic causal model and deterministic pseudo-random draws; they are not observed merchant traffic.
- Captured GMV is reported in paise and is not profit, ROI, net revenue, or a production revenue estimate.
- Offer discount exposure is reported as treatment captured GMV multiplied by the configured discount percentage. Discount cost and margin are not modeled.
- The evaluation diagnosis adapter is a deterministic approximation using only the observable evidence catalog; it is not an OpenAI call.
- Policy rejections are treated as no deployed treatment. No rejected intervention is silently applied or replanned.
