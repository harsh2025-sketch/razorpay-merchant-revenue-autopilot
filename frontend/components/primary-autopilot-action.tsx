"use client";

import { AlertTriangle } from "lucide-react";
import {
  ACTION_DISABLED,
  actionLabel,
  actionLoadingLabel,
} from "@/lib/labels";
import type { AutopilotNextAction } from "@/lib/types";
import { LoadingButton } from "./loading-button";

/**
 * The single context-aware lifecycle action. Active states render a mutation
 * button; terminal DONE is presented as status, not as an actionable control.
 */
export function PrimaryAutopilotAction({
  action,
  loading = false,
  onAction,
  size = "default",
}: {
  action: AutopilotNextAction | null;
  loading?: boolean;
  onAction?: () => void;
  size?: "default" | "compact";
}) {
  if (!action || action === "STOP") return null;

  const disabled = Boolean(ACTION_DISABLED[action]);
  const label = actionLabel(action);
  if (!label) return null;

  if (action === "DONE") {
    return (
      <button
        type="button"
        disabled
        aria-label={label}
        title="No further Autopilot action is available."
        className={`inline-flex cursor-default items-center justify-center rounded-md bg-gray-100 px-3.5 py-2 text-[13px] font-medium text-gray-700 opacity-100 ${
          size === "compact" ? "px-3 py-1.5 text-[12.5px]" : ""
        }`}
      >
        {label}
      </button>
    );
  }

  if (disabled) {
    return (
      <LoadingButton
        disabled
        variant="outline"
        title={
          action === "CONFIGURE_OFFER_MAPPING"
            ? "Deployment is blocked until the intervention is mapped to a verified Razorpay resource."
            : "No further Autopilot action is available."
        }
        className={size === "compact" ? "px-3 py-1.5 text-[12.5px]" : ""}
      >
        <span className="inline-flex items-center gap-1.5">
          {action === "CONFIGURE_OFFER_MAPPING" && (
            <AlertTriangle size={13} aria-hidden className="text-amber-500" />
          )}
          {label}
        </span>
      </LoadingButton>
    );
  }

  return (
    <LoadingButton
      loading={loading}
      loadingLabel={actionLoadingLabel(action) ?? undefined}
      onClick={onAction}
      variant={action === "ROLLBACK_TREATMENT" ? "danger" : "primary"}
      className={size === "compact" ? "px-3 py-1.5 text-[12.5px]" : ""}
    >
      {label}
    </LoadingButton>
  );
}
