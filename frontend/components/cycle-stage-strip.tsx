import { AlertTriangle, Check, ChevronRight, X } from "lucide-react";
import type { AutopilotCycle } from "@/lib/types";

export type StageStatus =
  | "completed"
  | "current"
  | "pending"
  | "rejected"
  | "blocked";

export interface CycleStage {
  id: string;
  label: string;
  status: StageStatus;
}

/**
 * Item-specific stage derivation from persisted cycle data only.
 * Every stage that has not happened yet stays `pending`; a rejected policy
 * decision marks the Policy stage `rejected`; a blocked deployment marks the
 * Razorpay stage `blocked`. The first non-terminal stage is `current`.
 */
export function deriveCycleStages(
  cycle: AutopilotCycle,
  deploymentBlocked = false,
): CycleStage[] {
  const decision = cycle.policy_decision;
  const experiment = cycle.experiment;

  const experimentStatus: StageStatus = !experiment
    ? "pending"
    : cycle.result
      ? "completed"
      : experiment.status === "running"
        ? "current"
        : ["completed", "rolled_back", "cancelled"].includes(experiment.status)
          ? "completed"
          : "pending";

  const stages: CycleStage[] = [
    { id: "evidence", label: "Evidence", status: "completed" },
    { id: "ai", label: "AI", status: cycle.hypothesis ? "completed" : "pending" },
    { id: "plan", label: "Plan", status: experiment ? "completed" : "pending" },
    {
      id: "policy",
      label: "Policy",
      status: !decision
        ? "pending"
        : decision.decision === "APPROVE"
          ? "completed"
          : "rejected",
    },
    {
      id: "razorpay",
      label: "Razorpay",
      status: cycle.razorpay_resource
        ? "completed"
        : deploymentBlocked
          ? "blocked"
          : "pending",
    },
    { id: "experiment", label: "Experiment", status: experimentStatus },
    {
      id: "decision",
      label: "Decision",
      status: cycle.result ? "completed" : "pending",
    },
  ];

  const current = stages.find((stage) => stage.status === "pending");
  // A rejected cycle is terminal - nothing is "in progress" afterwards.
  const rejected = stages.some((stage) => stage.status === "rejected");
  if (current && !rejected) current.status = "current";
  return stages;
}

/**
 * Compact horizontal stage indicator for one cycle (~36px tall).
 */
export function CycleStageStrip({
  stages,
  className = "",
}: {
  stages: CycleStage[];
  className?: string;
}) {
  return (
    <ol
      aria-label="Cycle stages"
      className={`flex flex-wrap items-center gap-x-1 gap-y-1.5 text-[12px] ${className}`}
    >
      {stages.map((stage, index) => {
        const done = stage.status === "completed";
        const current = stage.status === "current";
        return (
          <li key={stage.id} className="flex items-center gap-1">
            <span
              className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 ${
                stage.status === "rejected"
                  ? "border-red-200 bg-red-50 text-red-600"
                  : stage.status === "blocked"
                    ? "border-amber-200 bg-amber-50 text-amber-700"
                    : done
                      ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                      : current
                        ? "border-indigo-200 bg-indigo-50 font-medium text-indigo-700"
                        : "border-gray-200 bg-white text-gray-400"
              }`}
            >
              {stage.status === "rejected" ? (
                <X size={10} aria-hidden />
              ) : stage.status === "blocked" ? (
                <AlertTriangle size={10} aria-hidden />
              ) : done ? (
                <Check size={10} aria-hidden />
              ) : (
                <span
                  aria-hidden
                  className={`h-1.5 w-1.5 rounded-full ${
                    current ? "bg-indigo-500" : "bg-gray-300"
                  }`}
                />
              )}
              {stage.label}
            </span>
            {index < stages.length - 1 && (
              <ChevronRight size={12} aria-hidden className="text-gray-300" />
            )}
          </li>
        );
      })}
    </ol>
  );
}
