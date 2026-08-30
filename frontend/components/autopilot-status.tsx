"use client";

import Link from "next/link";
import { ArrowRight, RotateCcw } from "lucide-react";
import { ACTION_ACTORS, autopilotStatusSentence } from "@/lib/labels";
import type {
  AutopilotNextAction,
  AutopilotState,
  StatisticalDecision,
} from "@/lib/types";
import { AutopilotActorStrip } from "./autopilot-actor-strip";
import { LoadingButton } from "./loading-button";
import { PrimaryAutopilotAction } from "./primary-autopilot-action";

/**
 * The Overview hero: one clean bordered surface with the autopilot state
 * sentence, the next responsible actor, the single primary action and the
 * compact actor strip. Not a marketing hero.
 */
export function AutopilotStatus({
  state,
  nextAction,
  latestDecision,
  loading = false,
  restartLoading = false,
  restartAvailable = false,
  stepMessage,
  viewCycleHref,
  onAction,
  onStartNewCycle,
}: {
  state: AutopilotState;
  nextAction: AutopilotNextAction | null;
  latestDecision: StatisticalDecision | null;
  loading?: boolean;
  restartLoading?: boolean;
  restartAvailable?: boolean;
  stepMessage?: string | null;
  viewCycleHref?: string | null;
  onAction?: () => void;
  onStartNewCycle?: () => void;
}) {
  const sentence = autopilotStatusSentence(state, latestDecision);
  const actor = nextAction ? ACTION_ACTORS[nextAction] : null;
  const terminalCycle = nextAction === "DONE" || nextAction === "STOP";
  const undeployedCycle =
    restartAvailable ||
    nextAction === "CONFIGURE_OFFER_MAPPING" ||
    nextAction === "DEPLOY_TREATMENT";
  const canStartNewCycle = terminalCycle || undeployedCycle;

  return (
    <section
      aria-label="Autopilot status"
      className="rounded-lg border border-gray-200 bg-white shadow-hero"
    >
      <div className="flex flex-col gap-4 p-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-500">
            Autopilot
          </p>
          <p className="mt-1 max-w-3xl text-[19px] font-semibold leading-snug text-gray-900">
            {sentence}
          </p>
          <p className="mt-1.5 text-[13px] text-gray-500">
            {actor ? (
              <>
                Next step · <span className="font-medium text-gray-700">{actor}</span>
                {undeployedCycle && (
                  <span className="text-gray-400">
                    {" "}· or close this undeployed cycle and start another
                  </span>
                )}
              </>
            ) : terminalCycle ? (
              "This cycle is closed. Historical evidence remains available."
            ) : (
              "No pending Autopilot step"
            )}
          </p>
          {stepMessage && (
            <p className="mt-1 max-w-3xl truncate text-[12.5px] text-gray-400" title={stepMessage}>
              Last action · {stepMessage}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-3">
          <PrimaryAutopilotAction
            action={nextAction}
            loading={loading}
            onAction={onAction}
          />
          {canStartNewCycle && onStartNewCycle && (
            <LoadingButton
              loading={restartLoading}
              loadingLabel="Starting new cycle…"
              onClick={onStartNewCycle}
              variant={terminalCycle ? "primary" : "outline"}
            >
              <span className="inline-flex items-center gap-1.5">
                <RotateCcw size={13} aria-hidden />
                Start New Optimization Cycle
              </span>
            </LoadingButton>
          )}
          {viewCycleHref && (
            <Link
              href={viewCycleHref}
              className="inline-flex items-center gap-1 text-[13px] font-medium text-indigo-600 hover:text-indigo-700"
            >
              View cycle
              <ArrowRight size={13} aria-hidden />
            </Link>
          )}
        </div>
      </div>
      <div className="border-t border-gray-100 px-5 py-2.5">
        <AutopilotActorStrip state={state} nextAction={nextAction} />
      </div>
    </section>
  );
}
