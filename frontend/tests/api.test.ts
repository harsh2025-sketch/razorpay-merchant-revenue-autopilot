import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, advanceAutopilot, getOverview } from "@/lib/api";
import { describeApiError } from "@/lib/errors";

const overviewPayload = {
  merchant: { merchant_id: "merchant_techbazaar", name: "TechBazaar Electronics" },
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  vi.resetModules();
});

function stubFetchOnce(payload: unknown, init?: { status?: number; ok?: boolean }) {
  const response = new Response(JSON.stringify(payload), {
    status: init?.status ?? 200,
    headers: { "Content-Type": "application/json" },
  });
  const fetchMock = vi.fn().mockResolvedValue(response);
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("api base URL resolution", () => {
  it("uses the public production API origin for browser-side fetches", async () => {
    vi.resetModules();
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "https://render-backend.example.com");
    vi.stubEnv("API_INTERNAL_BASE_URL", "https://internal-backend.example.com");

    const { API_BASE_URL } = await import("@/lib/constants");

    expect(API_BASE_URL).toBe("https://render-backend.example.com");
  });

  it("uses the internal API origin for server-side relative public paths", async () => {
    vi.resetModules();
    vi.stubGlobal("window", undefined);
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "/backend-api");
    vi.stubEnv("API_INTERNAL_BASE_URL", "https://render-backend.example.com");

    const { API_BASE_URL } = await import("@/lib/constants");

    expect(API_BASE_URL).toBe("https://render-backend.example.com");
  });

  it("does not require a localhost rewrite in the production config", () => {
    const nextConfig = readFileSync(join(__dirname, "..", "next.config.mjs"), "utf8");

    expect(nextConfig).not.toMatch(/rewrites\s*\(/);
    expect(nextConfig).not.toContain("127.0.0.1:8000");
  });

  it("does not read backend secret variables in browser-facing constants", () => {
    const constants = readFileSync(join(__dirname, "..", "lib", "constants.ts"), "utf8");

    expect(constants).not.toMatch(/OPENAI_API_KEY|RAZORPAY_KEY_SECRET|DATABASE_URL/);
  });
});

describe("api layer", () => {
  it("fetches the overview from the centralized base URL", async () => {
    const fetchMock = stubFetchOnce(overviewPayload);
    const overview = await getOverview("merchant_techbazaar");
    expect(overview.merchant.name).toBe("TechBazaar Electronics");
    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/v1/merchants/merchant_techbazaar/overview");
  });

  it("posts an empty body to the autopilot step endpoint", async () => {
    const fetchMock = stubFetchOnce({
      merchant_id: "merchant_techbazaar",
      step: "OPPORTUNITY_DETECTED",
      entity_type: "opportunity",
      entity_id: "opp-1",
      message: "Detected 1 opportunity",
      status: "HYPOTHESIS_PENDING",
      next_action: "DIAGNOSE_OPPORTUNITY",
    });
    await advanceAutopilot("merchant_techbazaar");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
  });

  it("parses the deterministic error envelope without dumping objects", async () => {
    stubFetchOnce(
      { detail: { code: "EXPERIMENT_NOT_READY", message: "not ready" } },
      { status: 409 },
    );
    await expect(getOverview("merchant_techbazaar")).rejects.toMatchObject({
      code: "EXPERIMENT_NOT_READY",
    });
  });

  it("maps network failures to the controlled connection message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("boom")));
    await expect(getOverview("merchant_techbazaar")).rejects.toMatchObject({
      code: "NETWORK_ERROR",
    });
  });
});

describe("error copy", () => {
  it("uses fixed merchant-safe sentences for known codes", () => {
    const described = describeApiError(
      new ApiError("OPENAI_NOT_CONFIGURED", "internal detail", 503),
    );
    expect(described.title).toBe(
      "AI diagnosis is unavailable because OpenAI is not configured.",
    );
    expect(described.detail).toBeNull();
  });

  it("marks ambiguous execution states amber and retry-free", () => {
    const described = describeApiError(
      new ApiError(
        "EXECUTION_STATE_CONFLICT",
        "previous external operation unresolved",
        409,
      ),
    );
    expect(described.tone).toBe("amber");
  });

  it("falls back to the sanitized backend message for unknown codes", () => {
    const described = describeApiError(
      new ApiError("RAZORPAY_API_FAILURE", "Upstream request failed.", 502),
    );
    expect(described.title).toBe("Upstream request failed.");
    expect(described.tone).toBe("red");
  });

  it("never renders a raw non-API error object", () => {
    const described = describeApiError(new Error("TypeError: cannot read x"));
    expect(described.title).toBe("The request could not be completed.");
  });
});
