import { mapBackendMonitoringPayload } from "./backendMonitoringAdapter";
import { demoCatalogue, getDemoRuntime } from "../control/demoRuntime";
import type { MonitoringDataSource } from "./dataSources";
import type { EmergencyLevel, MonitoringWirePayload } from "./model";

function wireForActiveScenario(): MonitoringWirePayload {
  const runtime = getDemoRuntime();
  const info = demoCatalogue().find((s) => s.id === runtime.scenario);
  const now = Date.now();
  const drill = runtime.scenario !== "resting";
  const bpm = drill ? 118 : 72 + Math.round(Math.sin(now / 4000) * 3);
  const tier = info?.expectedTier ?? "none";
  const cardiac = tier.startsWith("tier_2_cardiac");
  const fallen = drill;

  return {
    subject_id: "Resident",
    location: "Home",
    system: { connection: "connected" },
    sensor: { connection: "connected", ppg_source: "simulated" },
    snapshot: {
      decision: {
        tier: (cardiac ? "tier_2_cardiac" : drill ? (info?.expectedTier ?? "none") : "none") as EmergencyLevel,
        scenario: runtime.scenario,
        reason: drill
          ? `${info?.label ?? runtime.scenario} in progress.`
          : "All monitoring signals are steady."
      },
      pose: fallen ? "prone" : "upright",
      accel: cardiac ? "soft_collapse" : drill ? "impact" : "quiet",
      hr: cardiac ? "pulse_lost" : drill ? "elevated" : "resting",
      latest_hr_bpm: cardiac ? null : bpm,
      hr_simulated: true,
      latest_accel_g: drill ? 3.1 : 1.01,
      detector_class: fallen ? "fallen" : "stand",
      detector_conf: 0.9,
      action_class: fallen ? "fallen" : "stand",
      action_conf: 0.88,
      timestamp_ms: now
    },
    voice: { status: cardiac ? "not_required" : drill ? "pending" : "not_required" },
    alert_dispatch: {
      status: cardiac || tier.startsWith("tier_2") ? "sent" : drill ? "pending" : "idle"
    },
    simulation: {
      drill,
      sensor_source: "mock",
      ppg_source: "simulated",
      scenario: runtime.scenario
    },
    runtime: {
      health: "ok",
      accelerator: "cpu",
      accelerator_device: "laptop",
      vision_fps: null,
      detector_backend: "demo",
      pose_backend: "demo"
    },
    events: [
      {
        id: `demo-${runtime.generation}`,
        timestamp_ms: runtime.startedAt,
        severity: tier.startsWith("tier_2") ? "critical" : drill ? "warning" : "info",
        category: drill ? "alert" : "system",
        title: drill ? info?.label ?? runtime.scenario : "Monitoring online",
        detail: drill ? info?.description ?? "" : "Wristband and camera are reporting."
      }
    ]
  };
}

export class DemoMonitoringDataSource implements MonitoringDataSource {
  // sqlite v2 only allows source='jetson'; the payload itself says this is a drill.
  readonly source = "jetson" as const;
  async read() {
    return mapBackendMonitoringPayload(wireForActiveScenario());
  }
}
