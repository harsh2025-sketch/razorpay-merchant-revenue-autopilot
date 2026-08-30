# Task 21B — Incremental merchant evidence

Task 21B replaces repeated analysis of an unchanged historical dataset with an append-only evidence lifecycle.

## Product contract

- Real merchants onboard once with a canonical CSV and later append additional CSVs.
- Stable external transaction IDs are canonicalized per merchant.
- Exact re-uploads are deduplicated; they create no new data revision.
- Re-uploads that reuse an external ID with different immutable transaction data fail closed.
- TechBazaar advances through deterministic, non-overlapping synthetic historical periods with distinct IDs.
- Opportunity detection reads only historical observations (`experiment_id IS NULL`). Experimental runtime rows never feed back into merchant diagnosis.
- Every historical revision can be analyzed by the opportunity detector at most once. A detector pass is durably marked even when it finds zero opportunities.
- Starting a new optimization cycle can consume another opportunity from the same detector pass, but cannot rescan an exhausted unchanged revision.
- A newly appended historical revision supersedes untouched opportunities from the prior pass; those rows remain persisted as resolved evidence before the updated revision is scanned.

## UI behavior

The dashboard exposes a **Data** page:

- merchant data: upload a new canonical CSV;
- TechBazaar demo: append the next deterministic historical period.

When the current detector revision is exhausted, Overview shows **Awaiting data** and routes the merchant to add new data rather than offering another misleading scan of the same evidence.

## Non-goals

Task 21B does not alter experiment statistics, policy limits, Razorpay execution, AI authority boundaries, or champion/memory semantics. It only makes the merchant evidence lifecycle incremental and non-replaying.
