import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { mapControlPayload } from "../lib/control/model";
import { controlPayload } from "./controlFixture";
import { mapBackendMonitoringPayload } from "../lib/monitoring/backendMonitoringAdapter";
import { normalMonitoringPayload as monitoringFixture } from "./monitoringFixture";

const selectScenario = vi.fn();
const fetchControlState = vi.fn();
const fetchMonitoringSnapshot = vi.fn();

vi.mock("../lib/control/clientControl", () => ({
  fetchControlState: (...args: unknown[]) => fetchControlState(...args),
  selectScenario: (...args: unknown[]) => selectScenario(...args),
  restartScenario: vi.fn(),
  resetScenario: vi.fn()
}));

vi.mock("../lib/monitoring/clientDataSource", () => ({
  fetchMonitoringSnapshot: (...args: unknown[]) => fetchMonitoringSnapshot(...args)
}));

// Imported after the mocks so the component picks them up.
const { default: ScenarioControlPanel } = await import("../app/components/ScenarioControlPanel");

beforeEach(() => {
  vi.clearAllMocks();
  fetchControlState.mockResolvedValue(mapControlPayload(controlPayload()));
  selectScenario.mockImplementation(async (alias: string) => {
    const payload = controlPayload();
    payload.scenario = alias === "trip-fall" ? "demo_trip_fall" : alias;
    payload.drill = alias !== "resting";
    return mapControlPayload(payload);
  });
  fetchMonitoringSnapshot.mockRejectedValue(new Error("not needed for these tests"));
});

describe("ScenarioControlPanel", () => {
  it("renders a button per scenario, grouped, with the expected tier", async () => {
    render(<ScenarioControlPanel />);

    expect(await screen.findByRole("button", { name: /Trip fall/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Resting/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Syncope/ })).toBeInTheDocument();
    expect(screen.getByText("PRD scenarios")).toBeInTheDocument();
    expect(screen.getByText("Scripted playbooks")).toBeInTheDocument();
    expect(screen.getByText("Tier 1 — verify")).toBeInTheDocument();
  });

  it("warns that the buttons drive the real emergency path", async () => {
    render(<ScenarioControlPanel />);
    expect(
      await screen.findByText(/drive the real emergency path/i)
    ).toBeInTheDocument();
  });

  it("activates a Tier-1 scenario on a single press", async () => {
    render(<ScenarioControlPanel />);

    fireEvent.click(await screen.findByRole("button", { name: /Trip fall/ }));

    await waitFor(() => expect(selectScenario).toHaveBeenCalledWith("trip-fall"));
  });

  // Tier 2 bypasses voice verification and dispatches at once, so a stray
  // click must not be enough to page a caregiver.
  it("requires a second press before firing a Tier-2 scenario", async () => {
    render(<ScenarioControlPanel />);

    const button = await screen.findByRole("button", { name: /Syncope/ });
    fireEvent.click(button);

    expect(selectScenario).not.toHaveBeenCalled();
    expect(await screen.findByText(/Press again to confirm/i)).toBeInTheDocument();

    fireEvent.click(button);
    await waitFor(() => expect(selectScenario).toHaveBeenCalledWith("fall-c-syncope"));
  });

  it("marks the running scenario as pressed", async () => {
    fetchControlState.mockResolvedValue(
      mapControlPayload({ ...controlPayload(), scenario: "demo_trip_fall" })
    );
    render(<ScenarioControlPanel />);

    const button = await screen.findByRole("button", { name: /Trip fall/ });
    expect(button).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Running")).toBeInTheDocument();
  });

  it("disables every button and explains why when control is switched off", async () => {
    fetchControlState.mockResolvedValue(
      mapControlPayload({ ...controlPayload(), enabled: false })
    );
    render(<ScenarioControlPanel />);

    expect(
      await screen.findByText(/monitoring.control_enabled: true/)
    ).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Trip fall/ })).toBeDisabled();
  });

  it("shows the backend's reason when there is no scenario to drive", async () => {
    fetchControlState.mockResolvedValue(
      mapControlPayload({
        ...controlPayload(),
        available: false,
        reason: "Scenario control needs the synthetic sensor client."
      })
    );
    render(<ScenarioControlPanel />);

    expect(
      await screen.findByText(/needs the synthetic sensor client/)
    ).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Syncope/ })).toBeDisabled();
  });

  it("surfaces a drill flag while a scenario is running", async () => {
    fetchControlState.mockResolvedValue(
      mapControlPayload({ ...controlPayload(), drill: true, scenario: "demo_trip_fall" })
    );
    render(<ScenarioControlPanel />);
    expect(await screen.findByText(/Drill in progress/i)).toBeInTheDocument();
  });

  it("reports a control backend outage without blanking the panel", async () => {
    fetchControlState.mockRejectedValue(new Error("Control backend unavailable"));
    render(<ScenarioControlPanel />);
    expect(await screen.findByText("Control backend unavailable")).toBeInTheDocument();
  });
});

describe("live fusion readout", () => {
  it("humanises every fusion tier, including tier_0_dismiss", async () => {
    // tier_0_dismiss is what a quiet baseline actually reports, so leaving it
    // out of the label map put a raw enum on screen.
    fetchMonitoringSnapshot.mockResolvedValue({
      ...mapBackendMonitoringPayload(monitoringFixture()),
      emergency: { level: "tier_0_dismiss", scenario: "D", reason: "quiet" }
    });
    render(<ScenarioControlPanel />);

    expect(await screen.findByText("Tier 0 — dismissed")).toBeInTheDocument();
    expect(screen.queryByText("tier_0_dismiss")).not.toBeInTheDocument();
  });
});
