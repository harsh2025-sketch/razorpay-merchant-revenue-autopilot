import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { OverviewView } from "@/components/overview-view";
import { makeAuditEvent, makeOpportunity, makeOverview } from "./fixtures";

function jsonResponse(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

const audit = [makeAuditEvent()];
const consumedReadiness = {
  merchant_id: "merchant_techbazaar",
  ready: false,
  reason: "WAITING_FOR_NEW_DATA",
  latest_opportunity_at: "2026-08-30T00:00:00Z",
  latest_data_append_at: null,
};

const readyReadiness = {
  merchant_id: "merchant_techbazaar",
  ready: true,
  reason: "INITIAL_DATA",
  latest_opportunity_at: null,
  latest_data_append_at: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("repeatable optimization cycles", () => {
  it("starts another already-detected opportunity without advancing the old cycle", async () => {
    const completed = makeOverview({
      active_opportunity_count: 1,
      active_experiment_count: 0,
      autopilot_status: {
        ...makeOverview().autopilot_status,
        active_opportunity_count: 1,
        active_experiment_count: 0,
        latest_experiment_status: "completed",
        latest_statistical_decision: "KEEP",
        state: "COMPLETED",
        next_action: "DONE",
      },
    });
    const next = makeOpportunity({
      id: "new-cycle-opportunity",
      segment: "android_mid",
    });
    const fresh = makeOverview({
      active_opportunity_count: 1,
      active_experiment_count: 0,
      autopilot_status: {
        ...makeOverview().autopilot_status,
        latest_opportunity_id: next.id,
        latest_experiment_id: null,
        latest_experiment_status: null,
        latest_statistical_decision: null,
        active_experiment_count: 0,
        state: "HYPOTHESIS_PENDING",
        next_action: "DIAGNOSE_OPPORTUNITY",
      },
    });

    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/autopilot/new-cycle")) {
        return Promise.resolve(jsonResponse(next));
      }
      if (url.includes("/detection-readiness")) {
        // The new focus can legitimately come from the same already-consumed
        // detector pass. Readiness being false must not hide non-detector work.
        return Promise.resolve(jsonResponse(consumedReadiness));
      }
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(fresh));
      if (url.includes("/audit")) return Promise.resolve(jsonResponse(audit));
      return Promise.reject(new Error(`unexpected fetch ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <OverviewView
        initialOverview={completed}
        initialAudit={audit}
        initialDetectionReady={false}
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: /Start New Optimization Cycle/i }),
    );

    await waitFor(() => {
      expect(
        screen.getByText(
          "An actionable conversion opportunity is ready for AI diagnosis.",
        ),
      ).toBeInTheDocument();
    });
    expect(screen.getByRole("link", { name: /View cycle/ })).toHaveAttribute(
      "href",
      `/autopilot/${next.id}`,
    );
    expect(
      fetchMock.mock.calls.filter(([url]) =>
        String(url).includes("/autopilot/new-cycle"),
      ),
    ).toHaveLength(1);
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).includes("/autopilot/step")),
    ).toHaveLength(0);
  });

  it("keeps the new-cycle escape visible after a deployment-blocked response refresh", async () => {
    const approved = makeOverview({
      autopilot_status: {
        ...makeOverview().autopilot_status,
        latest_experiment_status: "approved",
        state: "DEPLOYMENT_PENDING",
        next_action: "DEPLOY_TREATMENT",
      },
    });

    const fetchMock = vi.fn((input: string | URL | Request) => {
      const url = String(input);
      if (url.includes("/autopilot/step")) {
        return Promise.resolve(
          jsonResponse({
            merchant_id: "merchant_techbazaar",
            step: "DEPLOYMENT_BLOCKED",
            entity_type: "experiment",
            entity_id: "exp-1",
            message: "Deployment blocked: no verified offer mapping.",
            status: "DEPLOYMENT_BLOCKED",
            next_action: "CONFIGURE_OFFER_MAPPING",
          }),
        );
      }
      if (url.includes("/detection-readiness")) {
        return Promise.resolve(jsonResponse(readyReadiness));
      }
      if (url.includes("/overview")) return Promise.resolve(jsonResponse(approved));
      if (url.includes("/audit")) return Promise.resolve(jsonResponse(audit));
      return Promise.reject(new Error(`unexpected fetch ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <OverviewView
        initialOverview={approved}
        initialAudit={audit}
        initialDetectionReady
      />,
    );

    await userEvent.click(
      screen.getByRole("button", { name: "Deploy Treatment" }),
    );

    await waitFor(() => {
      expect(
        screen.getByRole("button", { name: /Start New Optimization Cycle/i }),
      ).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Deploy Treatment" })).toBeInTheDocument();
  });
});