"""Strict report models and deterministic renderers for Task 16."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvaluationRunResult:
    """One strategy result for one paired cohort."""

    seed: int
    segment: str
    strategy: str
    proposed_intervention: dict[str, object] | None
    policy_decision: str
    deployed_intervention: dict[str, object] | None
    attempts: int
    captured: int
    conversion_rate: float
    captured_gmv_paise: int
    control_conversion_rate: float
    absolute_conversion_delta_vs_control: float
    control_captured_gmv_paise: int
    captured_gmv_delta_vs_control_paise: int
    discount_exposure_paise: int = 0
    # The same value is recorded on every strategy row for a pair.  It is a
    # compact, public proof that the context cohort was paired.
    paired_context_fingerprint: str = ""

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvaluationAggregate:
    """Across-seed aggregate for one strategy and canonical segment."""

    strategy: str
    segment: str
    runs: int
    mean_conversion_rate: float
    mean_absolute_delta: float
    mean_gmv_delta_paise: float
    total_gmv_delta_paise: int
    positive_seed_count: int
    negative_seed_count: int

    def model_dump(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkReport:
    """Complete deterministic benchmark output."""

    causal_model_fingerprint: str
    seeds: list[int]
    customers_per_segment: int
    runs: list[EvaluationRunResult]
    aggregates: list[EvaluationAggregate]

    def model_dump(self) -> dict[str, Any]:
        """Pydantic-compatible convenience used by scripts and tests."""
        return asdict(self)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    def model_dump_json(self) -> str:
        return report_to_json(self)


def report_to_json(report: BenchmarkReport) -> str:
    """Serialize a report with stable ordering and formatting."""
    return json.dumps(
        report.model_dump(),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"


def write_json_report(report: BenchmarkReport, path: str | Path) -> Path:
    """Write a valid deterministic JSON report and return its path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report_to_json(report), encoding="utf-8")
    return target


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _overall_rows(report: BenchmarkReport) -> list[dict[str, object]]:
    by_strategy: dict[str, list[EvaluationRunResult]] = {}
    for run in report.runs:
        by_strategy.setdefault(run.strategy, []).append(run)
    rows: list[dict[str, object]] = []
    for strategy in sorted(by_strategy):
        strategy_runs = by_strategy[strategy]
        rows.append(
            {
                "strategy": strategy,
                "mean_conversion_rate": _mean(
                    [r.conversion_rate for r in strategy_runs]
                ),
                "mean_delta": _mean(
                    [r.absolute_conversion_delta_vs_control for r in strategy_runs]
                ),
                "total_gmv_delta_paise": sum(
                    r.captured_gmv_delta_vs_control_paise for r in strategy_runs
                ),
                "mean_gmv_delta_paise": _mean(
                    [
                        float(r.captured_gmv_delta_vs_control_paise)
                        for r in strategy_runs
                    ]
                ),
            }
        )
    return rows


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def render_markdown_summary(report: BenchmarkReport) -> str:
    """Render a concise, non-marketing synthetic-evaluation summary."""
    lines = [
        "# Merchant Revenue Autopilot - Evaluation Summary",
        "",
        "This is a **synthetic deterministic evaluation**, not production revenue evidence.",
        "",
        f"- Causal model fingerprint: `{report.causal_model_fingerprint}`",
        f"- Seeds: `{', '.join(str(seed) for seed in report.seeds)}`",
        f"- Cohort size per segment: `{report.customers_per_segment}`",
        "- Cohorts are paired: every strategy receives the same generated amount, payment method, device, source, and customer reference for each seed and segment.",
        "",
        "## Strategy comparison",
        "",
        "| Strategy | Mean conversion | Mean delta vs control | Total captured GMV delta (paise) | Mean captured GMV delta (paise) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in _overall_rows(report):
        lines.append(
            "| {strategy} | {conversion} | {delta} | {total:,} | {mean:,.2f} |".format(
                strategy=row["strategy"],
                conversion=_pct(float(row["mean_conversion_rate"])),
                delta=_pct(float(row["mean_delta"])),
                total=int(row["total_gmv_delta_paise"]),
                mean=float(row["mean_gmv_delta_paise"]),
            )
        )

    lines.extend(
        [
            "",
            "## Per-segment results",
            "",
            "Positive/negative seed counts refer to signed conversion delta versus control.",
            "",
            "| Segment | Strategy | Runs | Mean conversion | Mean delta | Mean GMV delta (paise) | Positive seeds | Negative seeds |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for aggregate in sorted(report.aggregates, key=lambda item: (item.segment, item.strategy)):
        lines.append(
            "| {segment} | {strategy} | {runs} | {conversion} | {delta} | {gmv:,.2f} | {positive} | {negative} |".format(
                segment=aggregate.segment,
                strategy=aggregate.strategy,
                runs=aggregate.runs,
                conversion=_pct(aggregate.mean_conversion_rate),
                delta=_pct(aggregate.mean_absolute_delta),
                gmv=aggregate.mean_gmv_delta_paise,
                positive=aggregate.positive_seed_count,
                negative=aggregate.negative_seed_count,
            )
        )

    lines.extend(["", "## Policy rejection counts", ""])
    for strategy in sorted({run.strategy for run in report.runs}):
        rejected = sum(
            run.strategy == strategy and run.policy_decision == "REJECT"
            for run in report.runs
        )
        lines.append(f"- `{strategy}`: `{rejected}` rejected proposals")

    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- Outcomes come from the sealed synthetic causal model and deterministic pseudo-random draws; they are not observed merchant traffic.",
            "- Captured GMV is reported in paise and is not profit, ROI, net revenue, or a production revenue estimate.",
            "- Offer discount exposure is reported as treatment captured GMV multiplied by the configured discount percentage. Discount cost and margin are not modeled.",
            "- The evaluation diagnosis adapter is a deterministic approximation using only the observable evidence catalog; it is not an OpenAI call.",
            "- Policy rejections are treated as no deployed treatment. No rejected intervention is silently applied or replanned.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_markdown_summary(report: BenchmarkReport, path: str | Path) -> Path:
    """Write the deterministic Markdown summary and return its path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown_summary(report), encoding="utf-8")
    return target


__all__ = [
    "EvaluationRunResult",
    "EvaluationAggregate",
    "BenchmarkReport",
    "report_to_json",
    "write_json_report",
    "render_markdown_summary",
    "write_markdown_summary",
]
