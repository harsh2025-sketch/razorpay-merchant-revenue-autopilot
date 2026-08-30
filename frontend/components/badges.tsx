import { actorLabel, DECISION_BADGES } from "@/lib/labels";
import type { StatisticalDecision } from "@/lib/types";

/**
 * Small restrained status vocabulary shared across pages.
 * Tones: blue = active/execution, green = approved/keep, red = rejected/rollback,
 * amber = blocked/inconclusive, gray = neutral.
 */

export type BadgeTone = "blue" | "green" | "red" | "amber" | "gray" | "indigo";

const TONE_CLASSES: Record<BadgeTone, string> = {
  blue: "border-blue-200 bg-blue-50 text-blue-700",
  green: "border-emerald-200 bg-emerald-50 text-emerald-700",
  red: "border-red-200 bg-red-50 text-red-700",
  amber: "border-amber-200 bg-amber-50 text-amber-700",
  gray: "border-gray-200 bg-gray-50 text-gray-600",
  indigo: "border-indigo-200 bg-indigo-50 text-indigo-700",
};

export function StatusBadge({
  tone = "gray",
  children,
  uppercase = true,
}: {
  tone?: BadgeTone;
  children: React.ReactNode;
  uppercase?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded border px-1.5 py-0.5 text-[10.5px] font-semibold ${
        uppercase ? "uppercase tracking-wider" : ""
      } ${TONE_CLASSES[tone]}`}
    >
      {children}
    </span>
  );
}

export function ActorBadge({ actor }: { actor: string }) {
  return (
    <span className="inline-flex shrink-0 items-center whitespace-nowrap rounded border border-gray-200 bg-gray-50 px-1.5 py-0.5 text-[11px] font-medium text-gray-600">
      {actorLabel(actor)}
    </span>
  );
}

export function DecisionBadge({
  decision,
  large = false,
}: {
  decision: StatisticalDecision;
  large?: boolean;
}) {
  const badge = DECISION_BADGES[decision];
  return (
    <span
      className={`inline-flex items-center rounded-md border font-semibold tracking-wide ${
        large ? "px-3 py-1.5 text-[15px]" : "px-2 py-1 text-[12px]"
      } ${TONE_CLASSES[badge.tone]}`}
    >
      {badge.label}
    </span>
  );
}

export function IntegrityBadge({ valid }: { valid: boolean }) {
  return valid ? (
    <span className="inline-flex items-center gap-1.5 rounded border border-emerald-200 bg-emerald-50 px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wider text-emerald-700">
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
      Verified
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 rounded border border-red-200 bg-red-50 px-1.5 py-0.5 text-[10.5px] font-semibold uppercase tracking-wider text-red-700">
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-red-500" />
      Verification failed
    </span>
  );
}
