import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OverviewView } from "@/components/overview-view";
import { makeOverview } from "./fixtures";

/**
 * Integration-style test for the focused Overview mutation loop: one click →
 * exactly one POST to the autopilot step → overview/readiness refetched →
 * "View cycle" link appears for an opportunity entity.
 */

const overviewA = makeOverview();
const overviewB = makeOverview({
  autopilot_status: {
    ...makeOverview().autopilot_status,
    state: "HYPOTHESIS_PENDING",
    next_action: "DIAGNOSE_OPPORTUNITY",
  },
});
const readiness = {
  merchant_id: "merchant_techbazaar",
  ready: true,
  reason: "INITIAL_DATA",
  latest_opportunity_at: null,
  latest_data_append_at: null,
};

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("OverviewView action loop", () => {
  it("advances one step, refetches data and links to the cycle", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/autopilot/step")) {
        return Promise.resolve(
          jsonResponse({
            merchant_id: "merchant_techbazaar",
            step: "OPPORTUNITY_DETECTED",
            entity_type: "opportunity",
            entity_id: "6f1cad54-0000-4000-8000-00000000e301",
            message: "Detected 1 opportunity; focusing segment 'android_budget'.",
            status: "HYPOTHESIS_PENDING",
            next_action: "DIAGNOSE_OPPORTUNITY",
          }),
        );
      }
      if (url.includes("/detection-readiness")) {
        return Promise.resolve(jsonResponse(readiness));
      }
      if (url.includes("/overview")) {
        return Promise.resolve(jsonResponse(overviewB));
      }
      return Promise.reject(new Error(`unexpected fetch ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <OverviewView
        initialOverview={makeOverview({
          autopilot_status: {
            ...makeOverview().autopilot_status,
            state: "IDLE",
            next_action: "DETECT_OPPORTUNITIES",
            latest_experiment_id: null,
            latest_experiment_status: null,
          },
        })}
        initialDetectionReady
      />,
    );

    expect(screen.getByText("Baseline Conversion")).toBeInTheDocument();
    expect(screen.getByText("Captured GMV")).toBeInTheDocument();
    expect(screen.getByText("Active Cycle")).toBeInTheDocument();
    expect(screen.getByText("Segment Conversion")).toBeInTheDocument();
    expect(screen.queryByText("Weakest Segment")).not.toBeInTheDocument();
    expect(screen.queryByText("Payment Method Performance")).not.toBeInTheDocument();
    expect(screen.queryByText("Recent Activity")).not.toBeInTheDocument();

    const stepCallsBefore = fetchMock.mock.calls.filter(([u]) =>
      String(u).includes("/autopilot/step"),
    ).length;

    await userEvent.click(
      screen.getByRole("button", { name: "Scan for Opportunities" }),
    );

    await waitFor(() => {
      expect(screen.getByRole("link", { name: /View cycle/ })).toBeInTheDocument();
    });

    const stepCalls = fetchMock.mock.calls.filter(([u]) =>
      String(u).includes("/autopilot/step"),
    ).length;
    expect(stepCalls).toBe(stepCallsBefore + 1);

    await waitFor(() => {
      expect(
        screen.getByText(
          "An actionable conversion opportunity is ready for AI diagnosis.",
        ),
      ).toBeInTheDocument();
    });
  });

  it("shows the mapped inline error when the step fails", async () => {
    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/autopilot/step")) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              detail: {
                code: "OPENAI_NOT_CONFIGURED",
                message: "Set OPENAI_API_KEY.",
              },
            }),
            { status: 503, headers: { "Content-Type": "application/json" } },
          ),
        );
      }
      if (url.includes("/detection-readiness")) {
        return Promise.resolve(jsonResponse(readiness));
      }
      if (url.includes("/overview")) {
        return Promise.resolve(jsonResponse(overviewA));
      }
      return Promise.reject(new Error(`unexpected fetch ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <OverviewView
        initialOverview={makeOverview({
          autopilot_status: {
            ...makeOverview().autopilot_status,
            state: "HYPOTHESIS_PENDING",
            next_action: "DIAGNOSE_OPPORTUNITY",
          },
        })}
        initialDetectionReady
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Generate Diagnosis" }),
    );

    await waitFor(() => {
      expect(
        screen.getByText(
          "AI diagnosis is unavailable because OpenAI is not configured.",
        ),
      ).toBeInTheDocument();
    });
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(/detail/i)).not.toBeInTheDocument();
  });
});
