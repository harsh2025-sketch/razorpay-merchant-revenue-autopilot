import { Check, ShieldCheck, X } from "lucide-react";
import { formatDurationHours, formatInt, formatPercent } from "@/lib/format";
import { violationLabel } from "@/lib/labels";
import type {
  Experiment,
  MerchantPolicyPublic,
  PolicyDecision as PolicyDecisionModel,
} from "@/lib/types";
import { StatusBadge } from "./badges";

interface LimitRow {
  label: string;
  proposed: string;
  limit: string | null;
  limitPrefix: string;
}

function buildLimitRows(
  experiment: Experiment,
  policy: MerchantPolicyPublic | null,
): LimitRow[] {
  const rows: LimitRow[] = [
    {
      label: "Treatment exposure",
      proposed: formatPercent(experiment.traffic_split_treatment_pct, 0),
      limit: policy ? formatPercent(policy.max_experiment_exposure_pct, 0) : null,
      limitPrefix: "Allowed max",
    },
    {
      label: "Minimum sample",
      proposed: `${formatInt(experiment.min_sample_per_variant)} / variant`,
      limit: policy ? `${formatInt(policy.min_sample_size)} / variant` : null,
      limitPrefix: "Merchant min",
    },
    {
      label: "Duration",
      proposed: formatDurationHours(experiment.max_duration_hours),
      limit: policy
        ? formatDurationHours(policy.max_experiment_duration_hours)
        : null,
      limitPrefix: "Allowed max",
    },
  ];
  const discount = experiment.treatment_config.discount_pct;
  if (typeof discount === "number") {
    rows.push({
      label: "Discount",
      proposed: formatPercent(discount, 0),
      limit: policy ? formatPercent(policy.max_discount_pct, 0) : null,
      limitPrefix: "Merchant max",
    });
  }
  return rows;
}

function LimitTable({ rows }: { rows: LimitRow[] }) {
  return (
    <div className="mt-4 max-w-xl overflow-hidden rounded-md border border-gray-100">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-gray-100 bg-slate-50 text-left text-[11px] uppercase tracking-wider text-gray-400">
            <th className="px-3.5 py-2 font-medium">Constraint</th>
            <th className="px-2 py-2 text-right font-medium">Proposed</th>
            <th className="px-3.5 py-2 text-right font-medium">Policy limit</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-50">
          {rows.map((row) => (
            <tr key={row.label}>
              <td className="px-3.5 py-2 text-gray-600">{row.label}</td>
              <td className="px-2 py-2 text-right font-medium text-gray-900 tabular-nums">
                {row.proposed}
              </td>
              <td className="px-3.5 py-2 text-right text-gray-500 tabular-nums">
                {row.limit ? `${row.limitPrefix} ${row.limit}` : "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * SECTION 4 - deterministic merchant authorization. Values are shown exactly
 * as persisted; the decision itself is never recomputed client-side, and no
 * override exists anywhere in the product.
 */
export function PolicyDecision({
  decision,
  experiment,
  policy,
}: {
  decision: PolicyDecisionModel;
  experiment: Experiment | null;
  policy: MerchantPolicyPublic | null;
}) {
  const approved = decision.decision === "APPROVE";
  const rows = experiment ? buildLimitRows(experiment, policy) : [];

  return (
    <section
      aria-label="Policy authorization"
      className={`rounded-lg border border-gray-200 bg-white p-5 ${
        approved ? "" : ""
      }`}
      style={{
        borderLeft: `3px solid ${approved ? "#10b981" : "#ef4444"}`,
      }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-500">
          Policy Authorization
        </p>
        {approved ? (
          <StatusBadge tone="green">
            <span className="inline-flex items-center gap-1">
              <Check size={10} aria-hidden />
              Approved
            </span>
          </StatusBadge>
        ) : (
          <StatusBadge tone="red">
            <span className="inline-flex items-center gap-1">
              <X size={10} aria-hidden />
              Rejected
            </span>
          </StatusBadge>
        )}
      </div>

      {approved ? (
        <p className="mt-3 inline-flex items-center gap-1.5 text-[14px] font-medium text-gray-900">
          <ShieldCheck size={15} aria-hidden className="text-emerald-600" />
          Authorized by deterministic merchant policy.
        </p>
      ) : (
        <>
          <p className="mt-3 text-[14px] font-medium text-gray-900">
            The AI proposal exceeded merchant-defined constraints.
          </p>
          {decision.violations.length > 0 && (
            <ul className="mt-2.5 space-y-1.5">
              {decision.violations.map((code) => (
                <li
                  key={code}
                  className="flex items-center gap-2 text-[13px] text-gray-700"
                >
                  <X size={13} aria-hidden className="shrink-0 text-red-500" />
                  {violationLabel(code)}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {rows.length > 0 && <LimitTable rows={rows} />}
    </section>
  );
}
