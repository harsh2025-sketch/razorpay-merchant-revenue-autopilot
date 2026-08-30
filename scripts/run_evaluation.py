#!/usr/bin/env python3
"""Run the offline Task 16 synthetic deterministic evaluation.

The default command runs the frozen 5 x 5 x 4 benchmark with 5,000 paired
contexts per segment.  Unit tests should pass a smaller ``--customers-per-
segment`` value.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

# Make the backend package importable when this script is run from the repo
# root, without importing or configuring the production database.
BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.evaluation.harness import (  # noqa: E402
    CANONICAL_SEGMENTS,
    DEFAULT_SEEDS,
    EVALUATION_CUSTOMERS_PER_SEGMENT,
    run_benchmark,
)
from app.evaluation.report import (  # noqa: E402
    write_json_report,
    write_markdown_summary,
)


ARTIFACT_DIR = Path(__file__).resolve().parents[1] / "artifacts" / "evaluation"


def _parse_seeds(value: str) -> tuple[int, ...]:
    try:
        seeds = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("seeds must be comma-separated integers") from exc
    if not seeds:
        raise argparse.ArgumentTypeError("at least one seed is required")
    return seeds


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--customers-per-segment",
        type=int,
        default=EVALUATION_CUSTOMERS_PER_SEGMENT,
        help="paired contexts per segment (default: %(default)s)",
    )
    parser.add_argument(
        "--seeds",
        type=_parse_seeds,
        default=DEFAULT_SEEDS,
        help="optional comma-separated deterministic seeds",
    )
    args = parser.parse_args()

    report = run_benchmark(
        seeds=args.seeds,
        segments=CANONICAL_SEGMENTS,
        customers_per_segment=args.customers_per_segment,
    )
    json_path = write_json_report(report, ARTIFACT_DIR / "evaluation_report.json")
    markdown_path = write_markdown_summary(
        report, ARTIFACT_DIR / "evaluation_summary.md"
    )

    print("SYNTHETIC DETERMINISTIC EVALUATION")
    print(f"Fingerprint: {report.causal_model_fingerprint}")
    print(
        f"Seeds: {report.seeds} | segments: {len(CANONICAL_SEGMENTS)} "
        f"| cohort per segment: {report.customers_per_segment}"
    )
    print("Strategy                         mean conversion  mean delta  rejected")
    strategies = sorted({run.strategy for run in report.runs})
    for strategy in strategies:
        rows = [run for run in report.runs if run.strategy == strategy]
        mean_conversion = sum(row.conversion_rate for row in rows) / len(rows)
        mean_delta = sum(row.absolute_conversion_delta_vs_control for row in rows) / len(rows)
        rejected = sum(row.policy_decision == "REJECT" for row in rows)
        print(
            f"{strategy:<32} {mean_conversion:>8.2%}       "
            f"{mean_delta:>+8.2%}     {rejected:>3}"
        )
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
