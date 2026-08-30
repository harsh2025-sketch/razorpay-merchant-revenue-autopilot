"use client";

import { AlertTriangle, Check, ChevronRight, X } from "lucide-react";
import { actorStripStages } from "@/lib/labels";
import type { AutopilotNextAction, AutopilotState } from "@/lib/types";

/** Compact actor strip: Detector → AI → Planner → Policy → Razorpay → Statistics. */
export function AutopilotActorStrip({
  state,
  nextAction,
  className = "",
}: {
  state: AutopilotState;
  nextAction: AutopilotNextAction | null;
  className?: string;
}) {
  if (state === "IDLE" && nextAction == null) {
    return (
      <p className={`text-[12px] text-gray-400 ${className}`}>
        Lifecycle paused · new merchant evidence is required before Detector runs again
      </p>
    );
  }

  const stages = actorStripStages(state, nextAction);

  return (
    <ol
      aria-label="System actors"
      className={`flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12px] ${className}`}
    >
      {stages.map((stage, index) => {
        const completed = stage.status === "completed";
        const current = stage.status === "current";
        const rejected = current && stage.tone === "rejected";
        const blocked = current && stage.tone === "blocked";
        return (
          <li key={stage.id} className="flex items-center gap-1.5">
            <span
              className={`inline-flex items-center gap-1 ${
                rejected
                  ? "font-medium text-red-600"
                  : blocked
                    ? "font-medium text-amber-600"
                    : current
                      ? "font-medium text-indigo-700"
                      : completed
                        ? "text-gray-600"
                        : "text-gray-400"
              }`}
            >
              {completed ? (
                <Check size={11} aria-hidden className="text-indigo-500" />
              ) : rejected ? (
                <X size={11} aria-hidden />
              ) : blocked ? (
                <AlertTriangle size={11} aria-hidden />
              ) : current ? (
                <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-indigo-500" />
              ) : (
                <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-gray-300" />
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
