import type { ControlWirePayload } from "../lib/control/model";

/** A live bench runtime: control on, synthetic sensors, sitting at baseline. */
export function controlPayload(): ControlWirePayload {
  return {
    enabled: true,
    available: true,
    reason: "",
    scenario: "resting",
    scenario_label: "Resting",
    elapsed_s: 12.5,
    generation: 1,
    sensor_source: "mock",
    drill: false,
    scenarios: [
      {
        id: "resting",
        alias: "resting",
        label: "Resting",
        group: "baseline",
        expected_tier: "none",
        description: "Quiet baseline."
      },
      {
        id: "demo_trip_fall",
        alias: "trip-fall",
        label: "Trip fall",
        group: "playbook",
        expected_tier: "tier_1_verify",
        description: "Gait, then impact at 10.3s."
      },
      {
        id: "fall_c_syncope",
        alias: "fall-c-syncope",
        label: "Syncope",
        group: "scenario",
        expected_tier: "tier_2_cardiac",
        description: "Soft collapse, then the pulse disappears."
      }
    ]
  };
}
