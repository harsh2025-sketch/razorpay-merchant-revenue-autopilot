import { Check } from "lucide-react";
import {
  formatDurationHours,
  formatPercent,
  humanizeToken,
  paymentMethodLabel,
} from "@/lib/format";
import { interventionLabel } from "@/lib/labels";
import type { Hypothesis } from "@/lib/types";

/**
 * Human-readable intervention description built from the validated
 * intervention params - never raw JSON.
 */
export function describeIntervention(
  type: string,
  params: Record<string, unknown>,
): string {
  switch (type) {
    case "payment_method_config": {
      const parts = Object.entries(params)
        .filter(([key]) => ["upi", "card", "netbanking", "wallet"].includes(key))
        .map(
          ([key, value]) =>
            `${paymentMethodLabel(key)} ${value === true ? "enabled" : value === false ? "disabled" : String(value)}`,
        );
      return parts.length ? parts.join(" · ") : "Payment method change";
    }
    case "offer_discount": {
      const discount = params.discount_pct;
      return typeof discount === "number"
        ? `${formatPercent(discount, 0)} discount on payment`
        : "Checkout discount";
    }
    case "partial_payment": {
      const accept = params.accept_partial === true;
      const firstMin = params.first_min_partial_amount_pct;
      const base = accept
        ? "Partial payment enabled"
        : "Partial payment disabled";
      return typeof firstMin === "number"
        ? `${base} · minimum first payment ${formatPercent(firstMin, 0)}`
        : base;
    }
    case "expiry_config": {
      const hours = params.expiry_hours;
      return typeof hours === "number"
        ? `Payment link expires after ${formatDurationHours(hours)}`
        : "Expiry change";
    }
    default: {
      const entries = Object.entries(params).slice(0, 4);
      if (!entries.length) return humanizeToken(type);
      return entries
        .map(([key, value]) => `${humanizeToken(key)}: ${String(value)}`)
        .join(" · ");
    }
  }
}

function ConfidenceBadge({ confidence }: { confidence: string | null }) {
  // Low/medium/high are shown as words only - never converted into fake
  // percentages or gauges.
  return (
    <span className="inline-flex items-center rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[11px] font-medium text-gray-700">
      {confidence ? humanizeToken(confidence) : "Unknown"}
    </span>
  );
}

/**
 * SECTION 2 - the AI proposal. Visually a step down from deterministic
 * sections: tinted neutral background with a restrained violet left border.
 * No glow, no gradient, no decoration.
 */
export function AIDiagnosis({
  hypothesis,
  modelName,
}: {
  hypothesis: Hypothesis;
  modelName?: string | null;
}) {
  return (
    <section
      aria-label="AI diagnosis"
      className="rounded-lg border border-gray-200 bg-slate-50/60 p-5"
      style={{ borderLeft: "3px solid #a78bfa" }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-500">
          AI Diagnosis
        </p>
        <p className="text-[12px] text-gray-500">
          AI Diagnosis · LLM proposal{modelName ? ` · ${modelName}` : ""}
        </p>
      </div>

      <p className="mt-3 max-w-4xl text-[14px] leading-relaxed text-gray-800">
        {hypothesis.hypothesis_text}
      </p>

      <div className="mt-4 grid max-w-3xl gap-x-10 gap-y-3 sm:grid-cols-2">
        <div>
          <p className="text-[12px] font-medium text-gray-500">Intervention</p>
          <p className="mt-0.5 text-[13.5px] text-gray-800">
            {describeIntervention(
              hypothesis.intervention_type,
              hypothesis.intervention_params,
            )}
          </p>
          <p className="mt-0.5 text-[12px] text-gray-400">
            {interventionLabel(hypothesis.intervention_type)}
          </p>
        </div>
        <div>
          <p className="text-[12px] font-medium text-gray-500">Confidence</p>
          <div className="mt-1">
            <ConfidenceBadge confidence={hypothesis.confidence} />
          </div>
        </div>
      </div>

      {hypothesis.reasoning_summary && (
        <div className="mt-4 max-w-4xl">
          <p className="text-[12px] font-medium text-gray-500">
            Reasoning summary
          </p>
          <p className="mt-0.5 text-[13px] leading-relaxed text-gray-600">
            {hypothesis.reasoning_summary}
          </p>
        </div>
      )}

      {hypothesis.evidence_refs.length > 0 && (
        <div className="mt-4">
          <p className="text-[12px] font-medium text-gray-500">
            Evidence references
          </p>
          <ul className="mt-1.5 flex flex-wrap gap-1.5">
            {hypothesis.evidence_refs.map((ref) => (
              <li
                key={ref}
                className="inline-flex items-center gap-1 rounded border border-gray-200 bg-white px-1.5 py-0.5 font-mono text-[11.5px] text-gray-600"
              >
                <Check size={10} aria-hidden className="shrink-0 text-indigo-500" />
                {ref}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
