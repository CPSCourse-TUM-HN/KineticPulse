import type { MonitoringWirePayload } from "../lib/monitoring/model";

export function normalMonitoringPayload(): MonitoringWirePayload {
  const now = Date.now();
  return {
    subject_id: "resident-001",
    location: "Living room",
    system: { connection: "connected" },
    sensor: { connection: "connected", ppg_source: "hardware" },
    snapshot: {
      decision: { tier: "none", scenario: "monitoring", reason: "No fall signatures detected." },
      pose: "upright",
      accel: "quiet",
      hr: "resting",
      latest_hr_bpm: 72,
      latest_accel_g: 1.01,
      detector_class: "stand",
      detector_conf: 0.96,
      action_class: "stand",
      action_conf: 0.93,
      timestamp_ms: now
    },
    voice: { status: "not_required" },
    alert_dispatch: { status: "idle" },
    simulation: {
      drill: false,
      sensor_source: "hardware",
      ppg_source: "hardware",
      scenario: null
    },
    runtime: {
      health: "ok",
      accelerator: "cuda",
      accelerator_device: "Orin",
      vision_fps: 16.4,
      detector_backend: "tensorrt",
      pose_backend: "tensorrt"
    },
    events: [
      { id: "runtime-2", timestamp_ms: now - 1_000, severity: "info", category: "vision", title: "Standing posture", detail: "Vision confidence 96%; no fall signature." },
      { id: "runtime-1", timestamp_ms: now - 2_000, severity: "info", category: "sensor", title: "Sensor sample received", detail: "Heart rate and motion are within normal bounds." }
    ]
  };
}

export function disconnectedMonitoringPayload(): MonitoringWirePayload {
  const payload = normalMonitoringPayload();
  payload.system.connection = "degraded";
  payload.sensor.connection = "disconnected";
  payload.snapshot.accel = "unknown";
  payload.snapshot.hr = "unknown";
  payload.snapshot.latest_hr_bpm = null;
  payload.snapshot.latest_accel_g = null;
  payload.events = [];
  return payload;
}

export function pulseLostMonitoringPayload(): MonitoringWirePayload {
  const payload = normalMonitoringPayload();
  payload.snapshot.decision = {
    tier: "tier_2_cardiac",
    scenario: "C",
    reason: "Pulse signal lost; suspected cardiac arrest."
  };
  payload.snapshot.pose = "prone";
  payload.snapshot.hr = "pulse_lost";
  payload.snapshot.latest_hr_bpm = null;
  payload.snapshot.detector_class = "fallen";
  payload.snapshot.detector_conf = 0.91;
  payload.voice.status = "not_required";
  payload.alert_dispatch.status = "sent";
  payload.events = [];
  return payload;
}

/**
 * Bench run with a dead MAX30102: the transport is up and the BPM looks
 * healthy, but it comes from the synthetic waveform in
 * kineticpulse/sensors/ppg_sim.py.
 */
export function simulatedHeartRatePayload(): MonitoringWirePayload {
  const payload = normalMonitoringPayload();
  payload.sensor.ppg_source = "simulated";
  payload.snapshot.hr_simulated = true;
  return payload;
}

/**
 * A control-panel drill: scripted telemetry is driving the pipeline, so
 * nothing on the dashboard is a measurement.
 */
export function drillMonitoringPayload(): MonitoringWirePayload {
  const payload = normalMonitoringPayload();
  payload.simulation = {
    drill: true,
    sensor_source: "mock",
    ppg_source: "hardware",
    scenario: "demo_trip_fall"
  };
  return payload;
}

/**
 * `--mock-ble` at the resting baseline: telemetry is generated but no drill
 * scenario is running, so `drill` is false. The dashboard still has to say
 * these are not the person's vitals.
 */
export function syntheticSensorPayload(): MonitoringWirePayload {
  const payload = normalMonitoringPayload();
  payload.simulation = {
    drill: false,
    sensor_source: "mock",
    ppg_source: "hardware",
    scenario: "resting"
  };
  return payload;
}

/** The CPU fallback: same pipeline, ~0.65 FPS, steps over falls. */
export function cpuFallbackPayload(): MonitoringWirePayload {
  const payload = normalMonitoringPayload();
  payload.runtime = {
    health: "critical",
    accelerator: "cpu",
    accelerator_device: null,
    vision_fps: 0.7,
    detector_backend: "pytorch",
    pose_backend: "pytorch"
  };
  return payload;
}

/** Running on the GPU but below the useful frame rate. */
export function degradedRuntimePayload(): MonitoringWirePayload {
  const payload = normalMonitoringPayload();
  payload.runtime = {
    health: "degraded",
    accelerator: "cuda",
    accelerator_device: "Orin",
    vision_fps: 7.2,
    detector_backend: "tensorrt",
    pose_backend: "tensorrt"
  };
  return payload;
}
