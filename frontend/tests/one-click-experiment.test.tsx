import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OverviewView } from "@/components/overview-view";
import { makeAuditEvent, makeOverview } from "./fixtures";

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const audit = [makeAuditEvent()];
const readiness = {
  merchant_id: "merchant_techbazaar",
  ready: false,
  reason: "WAITING_FOR_NEW_DATA",
  latest_opportunity_at: "2026-08-27T10:00:00Z",
  latest_data_append_at: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Task 21C one-click experiment", () => {
  it("uses one run-to-decision request instead of repeated autopilot batch clicks", async () => {
    const running = makeOverview({
      autopilot_status: {
        ...makeOverview().autopilot_status,
        latest_experiment_id: "exp-1",
        latest_experiment_status: "running",
        latest_resource_status: "active",
        state: "RUNNING",
        next_action: "RUN_EXPERIMENT_BATCH",
        progress: {
          experiment_id: "exp-1",
          control_attempts: 73,
          treatment_attempts: 61,
          sample_target_per_variant: 200,
          control_remaining: 127,
          treatment_remaining: 139,
          sample_target_reached: false,
        },
      },
    });
    const completed = makeOverview({
      active_experiment_count: 0,
      autopilot_status: {
        ...makeOverview().autopilot_status,
        active_experiment_count: 0,
        latest_experiment_id: "exp-1",
        latest_experiment_status: "completed",
        latest_statistical_decision: "INCONCLUSIVE",
        latest_resource_status: "active",
        state: "COMPLETED",
        next_action: "DONE",
        progress: {
          experiment_id: "exp-1",
          control_attempts: 200,
          treatment_attempts: 200,
          sample_target_per_variant: 200,
          control_remaining: 0,
          treatment_remaining: 0,
          sample_target_reached: true,
        },
      },
    });

    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/run-to-decision")) {
        return Promise.resolve(
          jsonResponse({
            experiment_id: "exp-1",
            generated_attempts: 1347,
            runtime_batches: 1,
            control_attempts: 200,
            treatment_attempts: 200,
            sample_target_per_variant: 200,
            decision: "INCONCLUSIVE",
            absolute_lift: 0.001,
            p_value: 0.9917,
          }),
        );
      }
      if (url.includes("/autopilot/step")) {
        return Promise.reject(new Error("generic step must not run runtime batches"));
      }
      if (url.includes("/detection-readiness")) {
        return Promise.resolve(jsonResponse(readiness));
      }
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(completed));
      if (url.includes("/audit")) return Promise.resolve(jsonResponse(audit));
      return Promise.reject(new Error(`unexpected fetch ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <OverviewView
        initialOverview={running}
        initialAudit={audit}
        initialDetectionReady={false}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Run Experiment" }));

    await waitFor(() => {
      expect(
        screen.getByText("Cycle complete · result inconclusive."),
      ).toBeInTheDocument();
    });

    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes("/run-to-decision")),
    ).toHaveLength(1);
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes("/autopilot/step")),
    ).toHaveLength(0);
    expect(
      screen.getByText(/200\/200 control and 200\/200 treatment observations/i),
    ).toBeInTheDocument();
  });
});
