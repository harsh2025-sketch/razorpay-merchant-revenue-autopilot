import { Check, X } from "lucide-react";
import {
  formatDurationHours,
  formatInt,
  formatPercent,
  humanizeToken,
  paymentMethodLabel,
} from "@/lib/format";
import { interventionLabel } from "@/lib/labels";
import type { Experiment } from "@/lib/types";

interface ConfigLine {
  text: string;
  tone?: "default" | "enabled" | "disabled" | "muted";
}

/**
 * Canonical semantic configs rendered as merchant-readable lines - for the
 * four known intervention shapes and a generic key-value fallback.
 */
function configLines(
  interventionType: string,
  config: Record<string, unknown>,
  side: "control" | "treatment",
): ConfigLine[] {
  switch (interventionType) {
    case "payment_method_config": {
      if (side === "control") {
        return [{ text: "Merchant default payment methods", tone: "muted" }];
      }
      const entries = Object.entries(config.payment_methods ?? {});
      if (!entries.length) return [{ text: "Merchant default payment methods", tone: "muted" }];
      return entries.map(([method, value]) => ({
        text: `${paymentMethodLabel(method)} ${value === true ? "enabled" : "disabled"}`,
        tone: value === true ? "enabled" : "disabled",
      }));
    }
    case "offer_discount":
      return side === "control"
        ? [{ text: "No offer", tone: "muted" }]
        : [
            {
              text:
                typeof config.discount_pct === "number"
                  ? `${formatPercent(config.discount_pct, 0)} discount`
                  : "Discount applied",
              tone: "enabled",
            },
          ];
    case "partial_payment":
      return side === "control"
        ? [{ text: "Partial payment disabled", tone: "muted" }]
        : [
            {
              text:
                config.accept_partial === true
                  ? "Partial payment enabled"
                  : "Partial payment disabled",
              tone: config.accept_partial === true ? "enabled" : "disabled",
            },
            ...(typeof config.first_min_partial_amount_pct === "number"
              ? [
                  {
                    text: `Minimum first payment: ${formatPercent(config.first_min_partial_amount_pct, 0)}`,
                    tone: "default" as const,
                  },
                ]
              : []),
          ];
    case "expiry_config":
      return side === "control"
        ? [{ text: "Merchant default expiry", tone: "muted" }]
        : [
            {
              text:
                typeof config.expiry_hours === "number"
                  ? `Expires after ${formatDurationHours(config.expiry_hours)}`
                  : "Custom expiry",
              tone: "enabled",
            },
          ];
    default: {
      const entries = Object.entries(config);
      if (!entries.length) return [{ text: "-", tone: "muted" }];
      return entries.map(([key, value]) => ({
        text: `${humanizeToken(key)}: ${typeof value === "object" ? JSON.stringify(value) : String(value)}`,
        tone: "default" as const,
      }));
    }
  }
}

function ConfigColumn({
  title,
  badge,
  lines,
}: {
  title: string;
  badge: React.ReactNode;
  lines: ConfigLine[];
}) {
  return (
    <div className="min-w-0">
      <div className="flex items-center gap-2">
        <h3 className="text-[14px] font-semibold text-gray-900">{title}</h3>
        {badge}
      </div>
      <ul className="mt-2.5 space-y-1.5">
        {lines.map((line, index) => (
          <li key={index} className="flex items-center gap-2 text-[13.5px]">
            {line.tone === "enabled" ? (
              <Check size={13} aria-hidden className="shrink-0 text-indigo-600" />
            ) : line.tone === "disabled" ? (
              <X size={13} aria-hidden className="shrink-0 text-gray-400" />
            ) : null}
            <span
              className={
                line.tone === "muted"
                  ? "text-gray-500"
                  : line.tone === "disabled"
                    ? "text-gray-500"
                    : "text-gray-800"
              }
            >
              {line.text}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * SECTION 3 - the deterministic plan only. No policy judgment here.
 */
export function ExperimentPlan({ experiment }: { experiment: Experiment }) {
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-500">
            Experiment Plan
          </p>
          <h2 className="mt-0.5 text-[16px] font-semibold text-gray-900">
            {experiment.name}
          </h2>
        </div>
        <p className="text-[12px] text-gray-500">
          {interventionLabel(experiment.intervention_type)}
        </p>
      </div>

      <div className="mt-4 grid gap-6 rounded-md border border-gray-100 p-4 md:grid-cols-2 md:gap-0 md:divide-x md:divide-gray-100">
        <div className="md:pr-6">
          <ConfigColumn
            title="Control"
            badge={
              <span className="rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wider text-gray-500">
                Baseline
              </span>
            }
            lines={configLines(
              experiment.intervention_type,
              experiment.control_config,
              "control",
            )}
          />
        </div>
        <div className="md:pl-6">
          <ConfigColumn
            title="Treatment"
            badge={
              <span className="rounded border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wider text-indigo-700">
                Test
              </span>
            }
            lines={configLines(
              experiment.intervention_type,
              experiment.treatment_config,
              "treatment",
            )}
          />
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-gray-100 bg-gray-100 md:grid-cols-4">
        <div className="bg-white">
          <Metric
            label="Treatment exposure"
            value={formatPercent(experiment.traffic_split_treatment_pct, 0)}
            sub="of segment traffic"
          />
        </div>
        <div className="bg-white">
          <Metric label="Primary metric" value={experiment.primary_metric} />
        </div>
        <div className="bg-white">
          <Metric
            label="Min sample / variant"
            value={formatInt(experiment.min_sample_per_variant)}
            sub="attempts"
          />
        </div>
        <div className="bg-white">
          <Metric
            label="Max duration"
            value={formatDurationHours(experiment.max_duration_hours)}
          />
        </div>
      </div>

      {experiment.guardrail_metrics.length > 0 && (
        <p className="mt-3 text-[12.5px] text-gray-500">
          Guardrails:{" "}
          <span className="text-gray-700">
            {experiment.guardrail_metrics.join(", ")}
          </span>
        </p>
      )}

      <details className="mt-3">
        <summary className="cursor-pointer text-[12.5px] font-medium text-indigo-600 hover:text-indigo-700">
          View configuration
        </summary>
        <pre className="mt-2 max-h-64 overflow-auto rounded-md border border-gray-100 bg-slate-50 p-3 font-mono text-[12px] leading-relaxed text-gray-700">
          {JSON.stringify(
            {
              control: experiment.control_config,
              treatment: experiment.treatment_config,
            },
            null,
            2,
          )}
        </pre>
      </details>
    </section>
  );
}

function Metric({
  label,
  value,
  sub,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
}) {
  return (
    <div className="px-4 py-3">
      <p className="text-[11.5px] font-medium text-gray-500">{label}</p>
      <p className="mt-0.5 text-[16px] font-semibold text-gray-900 tabular-nums">
        {value}
      </p>
      {sub && <p className="text-[11.5px] text-gray-400">{sub}</p>}
    </div>
  );
}
