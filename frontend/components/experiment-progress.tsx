import { formatInt } from "@/lib/format";
import type { ExperimentProgress as ProgressModel } from "@/lib/types";
import { StatusBadge } from "./badges";

function ProgressRow({
  label,
  attempts,
  target,
  barClassName,
}: {
  label: string;
  attempts: number;
  target: number;
  barClassName: string;
}) {
  const pct =
    target > 0 ? Math.min(100, Math.max(2, (attempts / target) * 100)) : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 text-[13px]">
        <span className="font-medium text-gray-700">{label}</span>
        <span className="text-gray-500 tabular-nums">
          {formatInt(attempts)} / {formatInt(target)}
        </span>
      </div>
      <div className="mt-1 h-2 overflow-hidden rounded bg-gray-100">
        <div
          className={`h-2 rounded ${barClassName}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

/**
 * SECTION 6 - observable progress toward the fixed sample horizon.
 * Attempts only: no interim conversion, lift, p-value or trend is shown
 * before the statistical result exists.
 */
export function ExperimentProgress({ progress }: { progress: ProgressModel }) {
  const reached = progress.sample_target_reached;
  return (
    <section className="rounded-lg border border-gray-200 bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-500">
            Experiment Progress
          </p>
          <h2 className="mt-0.5 text-[16px] font-semibold text-gray-900">
            Fixed-horizon sampling
          </h2>
        </div>
        <StatusBadge tone={reached ? "indigo" : "blue"}>
          {reached ? "Sample target reached" : "Ready to run"}
        </StatusBadge>
      </div>

      <div className="mt-4 max-w-xl space-y-4" data-testid="experiment-progress">
        <ProgressRow
          label="Control"
          attempts={progress.control_attempts}
          target={progress.sample_target_per_variant}
          barClassName="bg-gray-400"
        />
        <ProgressRow
          label="Treatment"
          attempts={progress.treatment_attempts}
          target={progress.sample_target_per_variant}
          barClassName="bg-indigo-500"
        />
      </div>

      <p className="mt-4 text-[12.5px] leading-relaxed text-gray-500">
        Run Experiment once. The backend advances deterministic simulated traffic
        until both cohorts reach the fixed horizon, then the statistical engine
        records KEEP, ROLLBACK, or INCONCLUSIVE. No interim result is exposed.
      </p>
    </section>
  );
}
