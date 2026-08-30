import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OverviewView } from "@/components/overview-view";
import { makeAuditEvent, makeOverview } from "./fixtures";

/**
 * Integration-style test for the Overview mutation loop: one click → exactly
 * one POST to the autopilot step → overview + recent audit refetched →
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
const audit = [makeAuditEvent()];

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
      if (url.includes("/overview")) {
        return Promise.resolve(
          jsonResponse(
            fetchMock.mock.calls.filter(([u]) => String(u).includes("/overview"))
              .length === 1
              ? overviewB
              : overviewA,
          ),
        );
      }
      if (url.includes("/audit")) return Promise.resolve(jsonResponse(audit));
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
        initialAudit={audit}
      />,
    );

    expect(screen.getByText("Baseline Conversion")).toBeInTheDocument();
    expect(screen.getByText("Weakest Segment")).toBeInTheDocument();
    expect(screen.getByText("Captured GMV")).toBeInTheDocument();

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
    expect(stepCalls).toBe(stepCallsBefore + 1); // exactly one transition

    // The status sentence updated from the refetched overview.
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
      if (url.includes("/overview")) {
        return Promise.resolve(jsonResponse(overviewA));
      }
      if (url.includes("/audit")) return Promise.resolve(jsonResponse(audit));
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
        initialAudit={audit}
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
    // Raw backend objects and exception details are never printed.
    expect(screen.queryByText(/detail/i)).not.toBeInTheDocument();
  });
});
