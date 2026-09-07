import { describe, expect, it } from "vitest";
import { mapBackendMonitoringPayload } from "../lib/monitoring/backendMonitoringAdapter";
import { normalMonitoringPayload, simulatedHeartRatePayload } from "./monitoringFixture";

describe("Jetson monitoring adapter", () => {
  it("maps the real wire contract into the normalized UI model", () => {
    const model = mapBackendMonitoringPayload(normalMonitoringPayload());

    expect(model.subjectId).toBe("resident-001");
    expect(model.system.connection).toBe("connected");
    expect(model.sensor.connection).toBe("connected");
    expect(model.heartRate).toEqual({ bpm: 72, status: "normal", simulated: false });
    expect(model.motion.state).toBe("resting");
    expect(model.vision.state).toBe("stand");
    expect(model.fall).toEqual({ confidence: 0, detected: false });
    expect(model.emergency.level).toBe("none");
    expect(model.recentEvents[0].timestampMs).toBeGreaterThanOrEqual(model.recentEvents[1].timestampMs);
  });
});

describe("simulated heart rate labelling", () => {
  it("flags a synthetic pulse so the UI cannot render it as a measured vital", () => {
    const model = mapBackendMonitoringPayload(simulatedHeartRatePayload());
    expect(model.heartRate.simulated).toBe(true);
    // The BPM itself still flows through: the point is that it is labelled,
    // not hidden. Fusion and the sparkline keep working on the bench.
    expect(model.heartRate.bpm).toBe(72);
    expect(model.heartRate.status).toBe("normal");
  });

  it("treats a payload from either flag alone as simulated", () => {
    const sensorOnly = normalMonitoringPayload();
    sensorOnly.sensor.ppg_source = "simulated";
    expect(mapBackendMonitoringPayload(sensorOnly).heartRate.simulated).toBe(true);

    const snapshotOnly = normalMonitoringPayload();
    snapshotOnly.snapshot.hr_simulated = true;
    expect(mapBackendMonitoringPayload(snapshotOnly).heartRate.simulated).toBe(true);
  });

  it("defaults to hardware when the backend predates the field", () => {
    const legacy = normalMonitoringPayload();
    delete legacy.sensor.ppg_source;
    delete legacy.snapshot.hr_simulated;
    expect(mapBackendMonitoringPayload(legacy).heartRate.simulated).toBe(false);
  });
});

describe("simulation provenance", () => {
  it("maps the drill block from the wire payload", () => {
    const payload = normalMonitoringPayload();
    payload.simulation = {
      drill: true,
      sensor_source: "mock",
      ppg_source: "simulated",
      scenario: "demo_trip_fall"
    };
    const model = mapBackendMonitoringPayload(payload);
    expect(model.simulation).toEqual({
      drill: true,
      sensorSource: "mock",
      ppgSource: "simulated",
      scenario: "demo_trip_fall"
    });
  });

  it("defaults to a non-drill hardware block when the backend omits it", () => {
    const legacy = normalMonitoringPayload();
    delete legacy.simulation;
    expect(mapBackendMonitoringPayload(legacy).simulation).toEqual({
      drill: false,
      sensorSource: "hardware",
      ppgSource: "hardware",
      scenario: null
    });
  });
});
