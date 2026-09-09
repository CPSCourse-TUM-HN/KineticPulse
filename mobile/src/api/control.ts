import { AppSettings } from "@/types/session";

import { monitoringOrigin } from "./monitoring";

export type ScenarioInfo = {
  id: string;
  alias: string;
  label: string;
  group: string;
  expected_tier: string;
  description: string;
};

export type ControlState = {
  /** False when the Jetson runs with `monitoring.control_enabled: false`. */
  enabled: boolean;
  /** False when the runtime cannot accept a scenario right now. */
  available: boolean;
  reason: string;
  scenario: string;
  scenarioLabel: string;
  elapsedS: number;
  generation: number;
  /** "mock" while a scripted scenario drives telemetry, else "hardware". */
  sensorSource: string;
  /** True while scripted telemetry could escalate — i.e. this is a drill. */
  drill: boolean;
  scenarios: ScenarioInfo[];
};

/** Bench control surface — `GET /control` (panel state + scenario catalogue). */
export async function fetchControl(settings: AppSettings): Promise<ControlState> {
  const res = await fetch(`${monitoringOrigin(settings)}/control`, {
    headers: { Accept: "application/json" }
  });
  if (!res.ok) throw new Error(`Control HTTP ${res.status}`);
  const json = await res.json();
  return {
    enabled: json.enabled === true,
    available: json.available === true,
    reason: json.reason ?? "",
    scenario: json.scenario ?? "resting",
    scenarioLabel: json.scenario_label ?? json.scenario ?? "unknown",
    elapsedS: typeof json.elapsed_s === "number" ? json.elapsed_s : 0,
    generation: typeof json.generation === "number" ? json.generation : 0,
    sensorSource: json.sensor_source ?? "unknown",
    drill: json.drill === true,
    scenarios: Array.isArray(json.scenarios) ? json.scenarios : []
  };
}

async function post(settings: AppSettings, path: string, body?: unknown): Promise<void> {
  const res = await fetch(`${monitoringOrigin(settings)}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const json = await res.json();
      if (json?.error) detail = String(json.message ?? json.error);
    } catch {
      // keep the status-code message
    }
    throw new Error(detail);
  }
}

/**
 * Replay a scenario from t=0. The Jetson accepts either the canonical id or the
 * hyphenated CLI alias; send the id straight from the catalogue.
 *
 * This injects synthetic telemetry and can escalate, so the runtime reports
 * `drill: true` for anything other than the quiet `resting` baseline.
 */
export function activateScenario(settings: AppSettings, scenario: string): Promise<void> {
  return post(settings, "/control/scenario", { scenario });
}

/** Replay whatever scenario is already active. */
export function restartScenario(settings: AppSettings): Promise<void> {
  return post(settings, "/control/restart");
}

/** Back to the quiet baseline. */
export function resetScenario(settings: AppSettings): Promise<void> {
  return post(settings, "/control/reset");
}
