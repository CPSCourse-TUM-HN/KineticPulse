import { render, screen } from "@testing-library/react";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import React from "react";
import { describe, expect, it } from "vitest";
import MonitoringDashboard from "../app/components/MonitoringDashboard";
import { mapBackendMonitoringPayload } from "../lib/monitoring/backendMonitoringAdapter";
import type { MonitoringWirePayload } from "../lib/monitoring/model";

/**
 * Renders the dashboard from payloads captured off a running Jetson.
 *
 * Fixtures only prove the shapes the fixtures assume. These captures prove the
 * real backend's field names reach the components — the gap that let
 * `simulation.sensor_source` and the whole runtime block sit unused.
 *
 * Assertions are derived from each capture rather than hard-coded, so a
 * re-capture from different hardware or flags stays valid. Refresh with:
 *   curl -s http://127.0.0.1:8790/monitoring > tests/.live-payload.json
 */
const CAPTURES = [
  [".live-payload.json", "GPU run with --mock-ble"],
  [".live-payload-cpu.json", "CPU fallback run"]
] as const;

function load(name: string): MonitoringWirePayload | null {
  const path = join(__dirname, name);
  return existsSync(path)
    ? (JSON.parse(readFileSync(path, "utf8")) as MonitoringWirePayload)
    : null;
}

describe("live Jetson payloads", () => {
  it.each(CAPTURES)("renders from %s (%s)", (file) => {
    const payload = load(file);
    if (payload === null) return;           // capture not present in this checkout

    const model = mapBackendMonitoringPayload(payload);
    render(<MonitoringDashboard model={model} loading={false} error={null} />);

    expect(screen.getByRole("heading", { name: /always aware/i })).toBeInTheDocument();

    // Every field the backend sends must survive into the model, not default.
    expect(model.runtime.accelerator).not.toBe("unknown");
    expect(model.runtime.health).not.toBe("unknown");
    expect(model.runtime.detectorBackend).not.toBeNull();

    // The edge-rate badge always shows the measured value.
    if (model.runtime.visionFps !== null) {
      expect(screen.getByText(`${model.runtime.visionFps} FPS`)).toBeInTheDocument();
    }

    // Degradation must be bannered, and a healthy run must not be.
    if (model.runtime.health === "critical") {
      expect(screen.getByRole("alert")).toHaveTextContent(/may\s+miss events/i);
    } else if (model.runtime.health === "ok") {
      expect(screen.queryByText(/Edge runtime/i)).not.toBeInTheDocument();
    }

    // Synthetic telemetry must be labelled whether or not a drill is running.
    if (model.simulation.drill) {
      expect(screen.getByText(/Drill in progress/i)).toBeInTheDocument();
    } else if (model.simulation.sensorSource === "mock") {
      expect(screen.getByText(/Synthetic sensors/i)).toBeInTheDocument();
    }
  });
});
