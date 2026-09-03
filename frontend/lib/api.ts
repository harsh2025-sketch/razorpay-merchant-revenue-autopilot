import { API_BASE_URL, API_PATHS } from "./constants";
import type {
  DemoMerchantSource,
  MerchantDataStatus,
  OnboardedMerchant,
} from "./onboarding-types";
import type {
  DemoPeriodResult,
  DetectionReadiness,
  IncrementalCsvResult,
} from "./data-types";
import type { OneClickExperimentResult } from "./experiment-run-types";
import type {
  AuditEvent,
  AutopilotStep,
  AutopilotCycle,
  ExperimentRollback,
  MerchantOverview,
  MerchantIntelligence,
  MerchantSummary,
  Opportunity,
} from "./types";

/**
 * Centralized API access. Every backend call in the dashboard goes through
 * here so error parsing, the base URL and the envelope shape stay in one
 * place. Native fetch only - no axios, no react-query.
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

export function getMerchant(merchantId: string): Promise<MerchantSummary> {
  return request<MerchantSummary>(API_PATHS.merchant(merchantId));
}

export function getOverview(merchantId: string): Promise<MerchantOverview> {
  return request<MerchantOverview>(API_PATHS.overview(merchantId));
}

export function getMerchantIntelligence(
  merchantId: string,
): Promise<MerchantIntelligence> {
  return request<MerchantIntelligence>(API_PATHS.intelligence(merchantId));
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

export function getDemoMerchantSource(): Promise<DemoMerchantSource> {
  return request<DemoMerchantSource>(API_PATHS.onboardingDemo);
}

export function getOnboardingDataStatus(
  merchantId: string,
): Promise<MerchantDataStatus> {
  return request<MerchantDataStatus>(API_PATHS.onboardingDataStatus(merchantId));
}

export function getDetectionReadiness(
  merchantId: string,
): Promise<DetectionReadiness> {
  return request<DetectionReadiness>(API_PATHS.detectionReadiness(merchantId));
}

// ---------------------------------------------------------------------------
// Merchant registration / data mutations
// ---------------------------------------------------------------------------

export function onboardMerchantWithCsv(input: {
  name: string;
  category?: string;
  monthlyGmvPaise?: number;
  file: File;
}): Promise<OnboardedMerchant> {
  const body = new FormData();
  body.set("name", input.name.trim());
  if (input.category?.trim()) body.set("category", input.category.trim());
  if (input.monthlyGmvPaise != null) {
    body.set("monthly_gmv_paise", String(input.monthlyGmvPaise));
  }
  body.set("file", input.file);

  // Do not set Content-Type manually: the browser must add the multipart
  // boundary to the FormData request.
  return request<OnboardedMerchant>(API_PATHS.onboardMerchantWithCsv, {
    method: "POST",
    body,
  });
}

export function appendMerchantCsv(
  merchantId: string,
  file: File,
): Promise<IncrementalCsvResult> {
  const body = new FormData();
  body.set("file", file);
  return request<IncrementalCsvResult>(API_PATHS.appendMerchantCsv(merchantId), {
    method: "POST",
    body,
  });
}

export function appendDemoPeriod(): Promise<DemoPeriodResult> {
  return request<DemoPeriodResult>(API_PATHS.appendDemoPeriod, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

// ---------------------------------------------------------------------------
// Lifecycle mutations
// ---------------------------------------------------------------------------

export function advanceAutopilot(merchantId: string): Promise<AutopilotStep> {
  return request<AutopilotStep>(API_PATHS.autopilotStep(merchantId), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
}

/**
 * Task 21C product action. Runtime and statistical evaluation are deliberately
 * combined only after policy approval + treatment deployment. Earlier safety
 * boundaries and a later Razorpay rollback remain explicit user-visible steps.
 */
export function runExperimentToDecision(
  experimentId: string,
): Promise<OneClickExperimentResult> {
  return request<OneClickExperimentResult>(
    API_PATHS.runExperimentToDecision(experimentId),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    },
  );
}

/**
 * Close a terminal optimization cycle while preserving all historical rows,
 * then return the next persisted opportunity to drive. Task 21B may return
 * null when the prior observation pass is exhausted and no new data has been
 * appended yet.
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
