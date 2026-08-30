"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ShieldCheck } from "lucide-react";
import {
  formatInt,
  formatPercent,
  formatPp,
  formatPValue,
  formatSignedPercent,
  formatUtcDateTime,
} from "@/lib/format";
import type { ExperimentResult, RazorpayResource } from "@/lib/types";
import { DecisionBadge, StatusBadge } from "./badges";
import { LoadingButton } from "./loading-button";

/**
 * SECTION 7 - the persisted statistical decision (CHART 2 of 2).
 * Backend values are shown exactly as computed; the LLM never participates
 * in this decision and the UI says so, always.
 */
export function StatisticalResult({
  result,
  resource,
  onRollback,
  rolling = false,
}: {
  result: ExperimentResult;
  resource: RazorpayResource | null;
  onRollback?: () => void;
  rolling?: boolean;
}) {
  const decision = result.decision;
  if (!decision) return null;

  // The rollback affordance exists only for a persisted ROLLBACK decision
  // with a still-active Razorpay resource - never for KEEP/INCONCLUSIVE.
  const showRollback =
    decision === "ROLLBACK" && resource?.status === "active";

  const chartData = [
    { name: "Control", rate: result.control_rate ?? 0 },
    { name: "Treatment", rate: result.treatment_rate ?? 0 },
  ];

  return (
    <section
      aria-label="Statistical result"
      className="rounded-lg border border-gray-200 bg-white p-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-500">
            Statistical Result
          </p>
          <DecisionBadge decision={decision} large />
        </div>
        <div className="flex items-center gap-3">
          {result.decided_at && (
            <p className="text-[12px] text-gray-400">
              {formatUtcDateTime(result.decided_at)}
            </p>
          )}
          {showRollback && (
            <LoadingButton
              variant="danger"
              loading={rolling}
              loadingLabel="Rolling back…"
              onClick={onRollback}
            >
              Roll Back Treatment
            </LoadingButton>
          )}
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-gray-100 bg-gray-100 md:grid-cols-4">
        <ResultCell
          label="Control conversion"
          value={formatPercent(result.control_rate)}
          sub={`${formatInt(result.control_conversions)} of ${formatInt(result.control_count)}`}
        />
        <ResultCell
          label="Treatment conversion"
          value={formatPercent(result.treatment_rate)}
          sub={`${formatInt(result.treatment_conversions)} of ${formatInt(result.treatment_count)}`}
        />
        <ResultCell
          label="Absolute lift"
          value={formatPp(result.absolute_lift)}
        />
        <ResultCell
          label="Relative lift"
          value={formatSignedPercent(result.relative_lift)}
        />
        <ResultCell label="p-value" value={formatPValue(result.p_value)} />
        <ResultCell
          label="95% confidence interval"
          value={`${formatPp(result.confidence_interval_lower)} to ${formatPp(result.confidence_interval_upper)}`}
        />
        <ResultCell
          label="Samples"
          value={`${formatInt(result.control_count)} / ${formatInt(result.treatment_count)}`}
          sub="control / treatment"
        />
        <ResultCell
          label="Significant"
          value={
            result.is_significant == null ? (
              "-"
            ) : result.is_significant ? (
              <span className="text-emerald-700">Yes</span>
            ) : (
              <span className="text-gray-500">No</span>
            )
          }
        />
      </div>

      <div className="mt-4 max-w-md">
        <p className="text-[12px] font-medium text-gray-500">
          Conversion by variant
        </p>
        <div className="mt-2" data-testid="result-chart">
          <ResponsiveContainer width="100%" height={168}>
            <BarChart
              data={chartData}
              margin={{ top: 4, right: 8, left: 0, bottom: 0 }}
            >
              <CartesianGrid vertical={false} stroke="#F1F5F9" />
              <XAxis
                dataKey="name"
                tickLine={false}
                axisLine={{ stroke: "#E5E7EB" }}
                tick={{ fill: "#475569", fontSize: 12 }}
              />
              <YAxis
                domain={[0, (dataMax: number) => Math.min(1, dataMax * 1.25)]}
                tickFormatter={(value: number) => `${Math.round(value * 100)}%`}
                axisLine={false}
                tickLine={false}
                width={44}
                tick={{ fill: "#94A3B8", fontSize: 11 }}
              />
              <Tooltip
                cursor={{ fill: "rgba(99,102,241,0.05)" }}
                formatter={(value: number) => [formatPercent(value), "Conversion"]}
                contentStyle={{
                  borderRadius: 8,
                  border: "1px solid #E5E7EB",
                  fontSize: 12,
                  padding: "6px 10px",
                }}
              />
              <Bar dataKey="rate" barSize={56} radius={[4, 4, 0, 0]} isAnimationActive={false}>
                <Cell fill="#94A3B8" />
                <Cell fill="#4F46E5" />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <p className="mt-4 flex items-start gap-1.5 border-t border-gray-100 pt-3 text-[12.5px] leading-relaxed text-gray-500">
        <ShieldCheck size={14} aria-hidden className="mt-0.5 shrink-0 text-gray-400" />
        Decision generated by fixed-horizon statistical evaluation. The LLM does
        not participate in this decision.
      </p>
    </section>
  );
}

function ResultCell({
  label,
  value,
  sub,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
}) {
  return (
    <div className="bg-white px-4 py-3">
      <p className="text-[11.5px] font-medium text-gray-500">{label}</p>
      <p className="mt-0.5 text-[18px] font-semibold leading-tight text-gray-900 tabular-nums">
        {value}
      </p>
      {sub && <p className="text-[11.5px] text-gray-400">{sub}</p>}
    </div>
  );
}

/** KEEP without an external promote action: a factual retention statement. */
export function TreatmentRetainedNote({
  resource,
}: {
  resource: RazorpayResource | null;
}) {
  if (!resource || resource.status !== "active") return null;
  return (
    <p className="text-[12.5px] text-gray-500">
      Treatment retained
      {resource.status === "active" ? " - Razorpay resource remains active" : ""}.
    </p>
  );
}

/** INCONCLUSIVE: no action exists, stated plainly. */
export function InconclusiveNote() {
  return (
    <p className="text-[13px] text-gray-600">
      Insufficient statistical evidence for a keep or rollback decision.
    </p>
  );
}

export function ResourceCancelledNote({
  resource,
}: {
  resource: RazorpayResource;
}) {
  return (
    <div className="flex items-center gap-2 text-[12.5px] text-gray-500">
      <StatusBadge tone="gray">{resource.status}</StatusBadge>
      Razorpay treatment resource cancelled after the ROLLBACK decision.
    </div>
  );
}
