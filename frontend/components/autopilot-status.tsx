"use client";

import Link from "next/link";
import { ArrowRight, Database, RotateCcw } from "lucide-react";
import { ACTION_ACTORS, autopilotStatusSentence } from "@/lib/labels";
import type {
  AutopilotNextAction,
  AutopilotState,
  StatisticalDecision,
} from "@/lib/types";
import { AutopilotActorStrip } from "./autopilot-actor-strip";
import { LoadingButton } from "./loading-button";
import { PrimaryAutopilotAction } from "./primary-autopilot-action";

/** Overview Autopilot status surface. */
export function AutopilotStatus({
  state,
  nextAction,
  latestDecision,
  waitingForData = false,
  waitingForLiveOutcomes = false,
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
  waitingForData?: boolean;
  waitingForLiveOutcomes?: boolean;
  loading?: boolean;
  restartLoading?: boolean;
  restartAvailable?: boolean;
  stepMessage?: string | null;
  viewCycleHref?: string | null;
  onAction?: () => void;
  onStartNewCycle?: () => void;
}) {
  const sentence = waitingForData
    ? "The current evidence revision is exhausted. Add new payment data before another optimization scan."
    : waitingForLiveOutcomes
      ? "Treatment is deployed. This uploaded merchant is awaiting assigned real experiment outcomes."
      : autopilotStatusSentence(state, latestDecision);
  const actor = nextAction ? ACTION_ACTORS[nextAction] : null;
  const terminalCycle = nextAction === "DONE" || nextAction === "STOP";
  const undeployedCycle =
    restartAvailable ||
    nextAction === "CONFIGURE_OFFER_MAPPING" ||
    nextAction === "DEPLOY_TREATMENT";
  const canStartNewCycle =
    !waitingForData && !waitingForLiveOutcomes && (terminalCycle || undeployedCycle);

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
            {waitingForData ? (
              "Historical transactions are preserved, but they will not be replayed as new evidence."
            ) : waitingForLiveOutcomes ? (
              "No TechBazaar synthetic customer traffic will be generated for this merchant. A production payment-event integration must supply control and treatment outcomes before statistics can run."
            ) : actor ? (
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
          {waitingForData ? (
            <Link
              href="/data"
              className="inline-flex items-center gap-1.5 rounded-md border border-indigo-600 bg-indigo-600 px-3.5 py-2 text-[13px] font-medium text-white hover:bg-indigo-700"
            >
              <Database size={13} aria-hidden />
              Add New Data
            </Link>
          ) : waitingForLiveOutcomes ? (
            <span
              role="status"
              className="inline-flex items-center rounded-md border border-amber-200 bg-amber-50 px-3.5 py-2 text-[13px] font-medium text-amber-800"
            >
              Awaiting live outcomes
            </span>
          ) : (
            <PrimaryAutopilotAction
              action={nextAction}
              loading={loading}
              onAction={onAction}
            />
          )}
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
