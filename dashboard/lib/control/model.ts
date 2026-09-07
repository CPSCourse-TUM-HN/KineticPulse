/**
 * Scenario control panel contract.
 *
 * Mirrors `kineticpulse.control.ControlState.as_json()` and the responses of
 * `POST /control/*` on the Jetson. See `kineticpulse/control.py` for why the
 * surface is disabled by default: activating a Tier-2 scenario runs the real
 * emergency path, webhooks included.
 */

export type ScenarioGroup = "baseline" | "scenario" | "playbook";
export type SensorSource = "hardware" | "mock";

/** One button on the panel. */
export interface ScenarioOption {
  id: string;            // canonical mock id, e.g. "demo_trip_fall"
  alias: string;         // URL-friendly name posted back, e.g. "trip-fall"
  label: string;
  group: ScenarioGroup;
  expectedTier: string;
  description: string;
}

export interface ControlModel {
  /** The HTTP control surface is switched on (monitoring.control_enabled). */
  enabled: boolean;
  /** ...and this runtime actually has a scenario to drive (--mock-ble). */
  available: boolean;
  /** Why not, when `available` is false. Shown to the operator verbatim. */
  reason: string;
  scenario: string;
  scenarioLabel: string;
  elapsedS: number;
  /** Increments on every activation; a cheap "did it actually switch?" check. */
  generation: number;
  sensorSource: SensorSource;
  /** A non-baseline scenario is driving the pipeline right now. */
  drill: boolean;
  scenarios: ScenarioOption[];
}

/** Raw envelope from the Jetson; field names stop at the adapter below. */
export interface ControlWirePayload {
  enabled: boolean;
  available: boolean;
  reason: string;
  scenario: string;
  scenario_label: string;
  elapsed_s: number;
  generation: number;
  sensor_source: SensorSource;
  drill: boolean;
  scenarios: Array<{
    id: string;
    alias: string;
    label: string;
    group: ScenarioGroup;
    expected_tier: string;
    description: string;
  }>;
}

export function mapControlPayload(payload: ControlWirePayload): ControlModel {
  return {
    enabled: payload.enabled,
    available: payload.available,
    reason: payload.reason ?? "",
    scenario: payload.scenario,
    scenarioLabel: payload.scenario_label,
    elapsedS: payload.elapsed_s,
    generation: payload.generation,
    sensorSource: payload.sensor_source,
    drill: payload.drill,
    scenarios: (payload.scenarios ?? []).map((option) => ({
      id: option.id,
      alias: option.alias,
      label: option.label,
      group: option.group,
      expectedTier: option.expected_tier,
      description: option.description
    }))
  };
}

/** A rejection the panel should show rather than treat as a transport fault. */
export interface ControlRejection {
  error: string;         // "disabled" | "unavailable" | "unknown_scenario" | ...
  message: string;
}
