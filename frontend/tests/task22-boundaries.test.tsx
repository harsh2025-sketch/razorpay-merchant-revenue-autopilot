import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AutopilotStatus } from "@/components/autopilot-status";
import { ApiError } from "@/lib/api";
import { describeApiError } from "@/lib/errors";

describe("Task 22 uploaded-merchant experiment boundary", () => {
  it("shows an awaiting-live-outcomes state instead of a synthetic runtime action", () => {
    render(
      <AutopilotStatus
        state="RUNNING"
        nextAction={null}
        latestDecision={null}
        waitingForLiveOutcomes={true}
      />,
    );

    expect(
      screen.getByText(
        "Treatment deployed. Waiting for assigned live experiment outcomes.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Awaiting live outcomes");
    expect(screen.queryByRole("button", { name: "Run Experiment" })).toBeNull();
    expect(
      screen.getByText(
        "Control and treatment outcomes must arrive from real payment events before statistics can run.",
      ),
    ).toBeInTheDocument();
  });

  it("maps the backend live-traffic boundary to an amber merchant-safe message", () => {
    const described = describeApiError(
      new ApiError(
        "LIVE_EXPERIMENT_TRAFFIC_REQUIRED",
        "internal backend detail",
        409,
      ),
    );

    expect(described.tone).toBe("amber");
    expect(described.title).toContain("awaiting assigned real experiment outcomes");
    expect(described.title).toContain("Synthetic TechBazaar traffic is disabled");
  });
});
