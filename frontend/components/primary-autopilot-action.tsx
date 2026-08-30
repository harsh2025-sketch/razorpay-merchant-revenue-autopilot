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
 * The single context-aware lifecycle button. One click = exactly one backend
 * transition. Terminal states (STOP / DONE) and the fail-closed mapping state
 * render as disabled buttons - never as a hidden mutation.
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

  if (disabled) {
    return (
      <LoadingButton
        disabled
        variant={action === "DONE" ? "outline" : "outline"}
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
