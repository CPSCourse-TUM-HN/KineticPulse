import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";
import MonitoringDashboard from "../app/components/MonitoringDashboard";
import { mapBackendMonitoringPayload } from "../lib/monitoring/backendMonitoringAdapter";
import {
  cpuFallbackPayload,
  degradedRuntimePayload,
  drillMonitoringPayload,
  normalMonitoringPayload,
  syntheticSensorPayload
} from "./monitoringFixture";

describe("MonitoringDashboard", () => {
  it("renders the main dashboard from a normalized model", () => {
    render(
      <MonitoringDashboard
        model={mapBackendMonitoringPayload(normalMonitoringPayload())}
        loading={false}
        error={null}
      />
    );

    expect(screen.getByRole("heading", { name: /always aware/i })).toBeInTheDocument();
    expect(screen.getByText("72")).toBeInTheDocument();
    expect(screen.getAllByText("BPM").length).toBeGreaterThan(0);
    expect(screen.getByRole("img", { name: /heart-rate trend/i })).toBeInTheDocument();
    expect(screen.getByText("ESP32 connected")).toBeInTheDocument();
    expect(screen.getByText("Recent events")).toBeInTheDocument();
  });

  it("banners a control-panel drill so a scripted fall is not read as real", () => {
    render(
      <MonitoringDashboard
        model={mapBackendMonitoringPayload(drillMonitoringPayload())}
        loading={false}
        error={null}
      />
    );

    expect(screen.getByText(/Drill in progress/i)).toBeInTheDocument();
    expect(screen.getByText(/Nothing on this page is a measurement/i)).toBeInTheDocument();
  });

  it("shows no drill banner on a normal run", () => {
    render(
      <MonitoringDashboard
        model={mapBackendMonitoringPayload(normalMonitoringPayload())}
        loading={false}
        error={null}
      />
    );

    expect(screen.queryByText(/Drill in progress/i)).not.toBeInTheDocument();
  });

  it("warns about synthetic sensors even when no drill is running", () => {
    // --mock-ble at the resting baseline sets drill=false, which previously
    // rendered generated vitals with no marker at all.
    render(
      <MonitoringDashboard
        model={mapBackendMonitoringPayload(syntheticSensorPayload())}
        loading={false}
        error={null}
      />
    );

    expect(screen.getByText(/Synthetic sensors/i)).toBeInTheDocument();
    expect(screen.getByText(/not\s+this person/i)).toBeInTheDocument();
    expect(screen.queryByText(/Drill in progress/i)).not.toBeInTheDocument();
  });

  it("banners the CPU fallback as a detection risk, not just a slowdown", () => {
    render(
      <MonitoringDashboard
        model={mapBackendMonitoringPayload(cpuFallbackPayload())}
        loading={false}
        error={null}
      />
    );

    const banner = screen.getByRole("alert");
    expect(banner).toHaveTextContent(/on the CPU/i);
    expect(banner).toHaveTextContent(/may\s+miss events/i);
  });

  it("banners a slow GPU run as degraded rather than critical", () => {
    render(
      <MonitoringDashboard
        model={mapBackendMonitoringPayload(degradedRuntimePayload())}
        loading={false}
        error={null}
      />
    );

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText(/Edge runtime is slow/i)).toBeInTheDocument();
  });

  it("shows the edge frame rate on a healthy run and no runtime banner", () => {
    render(
      <MonitoringDashboard
        model={mapBackendMonitoringPayload(normalMonitoringPayload())}
        loading={false}
        error={null}
      />
    );

    expect(screen.getByText("16.4 FPS")).toBeInTheDocument();
    expect(screen.queryByText(/Edge runtime/i)).not.toBeInTheDocument();
  });
});
