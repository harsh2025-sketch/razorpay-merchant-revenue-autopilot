import { API_BASE_URL, API_PATHS } from "./constants";
import type {
  AuditEvent,
  AutopilotStep,
  AutopilotCycle,
  ExperimentRollback,
  MerchantOverview,
  Opportunity,
} from "./types";

/**
 * Centralized API access. Every backend call in the dashboard goes through
 * here so error parsing, the base URL and the envelope shape stay in one
 * place. Native fetch only - no axios, no react-query.
 *
 * The API maps domain failures onto a deterministic envelope:
 *   { "detail": { "code": "...", "message": "..." } }
 * Anything else (network failure, non-JSON body) becomes a safe ApiError -
 * raw objects, stack traces and exception class names are never surfaced.
 */

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, message: string, status = 0) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

const GENERIC_MESSAGE = "The request could not be completed.";
const NETWORK_MESSAGE = "Unable to connect to Revenue Autopilot.";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      cache: "no-store",
      headers: { Accept: "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError("NETWORK_ERROR", NETWORK_MESSAGE);
  }

  if (!response.ok) {
    throw await toApiError(response);
  }
  return (await response.json()) as T;
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = "REQUEST_FAILED";
  let message = GENERIC_MESSAGE;
  try {
    const body: unknown = await response.json();
    const detail = (body as { detail?: unknown })?.detail;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail && typeof detail === "object") {
      const record = detail as Record<string, unknown>;
      if (typeof record.code === "string") code = record.code;
      if (typeof record.message === "string" && record.message.trim()) {
        message = record.message;
      }
    }
  } catch {
    // Non-JSON error body: keep the safe defaults above.
  }
  return new ApiError(code, message, response.status);
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export function getOverview(merchantId: string): Promise<MerchantOverview> {
  return request<MerchantOverview>(API_PATHS.overview(merchantId));
}

export function getOpportunities(merchantId: string): Promise<Opportunity[]> {
  return request<Opportunity[]>(API_PATHS.opportunities(merchantId));
}

export function getCycle(opportunityId: string): Promise<AutopilotCycle> {
  return request<AutopilotCycle>(API_PATHS.cycle(opportunityId));
}

export function getMerchantAudit(
  merchantId: string,
  limit: number,
): Promise<AuditEvent[]> {
  return request<AuditEvent[]>(API_PATHS.merchantAudit(merchantId, limit));
}

// ---------------------------------------------------------------------------
// Mutations - one user action, exactly one backend transition
// ---------------------------------------------------------------------------

export function advanceAutopilot(merchantId: string): Promise<AutopilotStep> {
  return request<AutopilotStep>(API_PATHS.autopilotStep(merchantId), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

/**
 * Close a terminal optimization cycle while preserving all historical rows,
 * then return the next persisted opportunity to drive (or null if the current
 * merchant data has no opportunity above the detector threshold).
 */
export function startNewAutopilotCycle(
  merchantId: string,
): Promise<Opportunity | null> {
  return request<Opportunity | null>(API_PATHS.newAutopilotCycle(merchantId), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

export function rollbackExperiment(
  experimentId: string,
): Promise<ExperimentRollback> {
  return request<ExperimentRollback>(
    API_PATHS.rollbackExperiment(experimentId),
    { method: "POST", headers: { "Content-Type": "application/json" } },
  );
}
