import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { AutopilotCycleList } from "@/components/autopilot-cycle-list";
import { makeOpportunity } from "./fixtures";

describe("autopilot cycle focus badge", () => {
  it("labels an active focus as the current cycle", () => {
    const opportunity = makeOpportunity({ id: "active-cycle" });
    render(
      <AutopilotCycleList
        opportunities={[opportunity]}
        latestOpportunityId={opportunity.id}
        state="RUNNING"
        decision={null}
      />,
    );

    expect(screen.getByText("Current cycle")).toBeInTheDocument();
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("labels a terminal focus as the latest cycle", () => {
    const opportunity = makeOpportunity({ id: "completed-cycle" });
    render(
      <AutopilotCycleList
        opportunities={[opportunity]}
        latestOpportunityId={opportunity.id}
        state="COMPLETED"
        decision="INCONCLUSIVE"
      />,
    );

    expect(screen.getByText("Latest cycle")).toBeInTheDocument();
    expect(screen.queryByText("Current cycle")).toBeNull();
    expect(screen.getByText("Completed · Inconclusive")).toBeInTheDocument();
  });
});
