import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { mapControlPayload, type ControlModel, type ScenarioOption } from "./model";

export type DemoRuntime = {
  scenario: string;
  startedAt: number;
  generation: number;
};

const CATALOGUE: ScenarioOption[] = [
  { id: "resting", alias: "resting", label: "Resting", group: "baseline", expectedTier: "none", description: "Quiet baseline." },
  { id: "fall_a_standard", alias: "fall-a-standard", label: "Standard fall", group: "scenario", expectedTier: "tier_1_verify", description: "Impact then stillness." },
  { id: "fall_b_seizure", alias: "fall-b-seizure", label: "Seizure", group: "scenario", expectedTier: "tier_2_seizure", description: "Impact then tremor." },
  { id: "fall_c_syncope", alias: "fall-c-syncope", label: "Syncope", group: "scenario", expectedTier: "tier_2_cardiac", description: "Soft collapse, then pulse loss." },
  { id: "demo_trip_fall", alias: "trip-fall", label: "Trip fall", group: "playbook", expectedTier: "tier_1_verify", description: "Gait, then impact." }
];

const byKey = new Map<string, ScenarioOption>();
for (const s of CATALOGUE) {
  byKey.set(s.id, s);
  byKey.set(s.alias, s);
}

// ponytail: file, not globalThis — Next workers / two `next dev`s were serving
// resting vitals while SQLite still had the drill event.
const STATE_FILE = join(process.cwd(), "runtime", "demo-runtime.json");

function readState(): DemoRuntime | null {
  try {
    const parsed = JSON.parse(readFileSync(STATE_FILE, "utf8")) as DemoRuntime;
    if (typeof parsed.scenario === "string" && typeof parsed.generation === "number") return parsed;
  } catch {
    // missing or junk — caller creates a baseline
  }
  return null;
}

function writeState(runtime: DemoRuntime): void {
  mkdirSync(join(process.cwd(), "runtime"), { recursive: true });
  writeFileSync(STATE_FILE, JSON.stringify(runtime));
}

export function getDemoRuntime(): DemoRuntime {
  const existing = readState();
  if (existing) return existing;
  const fresh = { scenario: "resting", startedAt: Date.now(), generation: 0 };
  writeState(fresh);
  return fresh;
}

export function resolveDemoScenario(name: string): ScenarioOption {
  const info = byKey.get(name.trim()) ?? byKey.get(name.trim().replace(/_/g, "-"));
  if (!info) throw new Error(`Unknown scenario ${name}`);
  return info;
}

export function demoCatalogue(): ScenarioOption[] {
  return CATALOGUE;
}

export function applyDemoScenario(name: string): ControlModel {
  const info = resolveDemoScenario(name);
  const prev = readState();
  writeState({
    scenario: info.id,
    startedAt: Date.now(),
    generation: (prev?.generation ?? 0) + 1
  });
  return demoControlModel();
}

export function demoControlModel(): ControlModel {
  const runtime = getDemoRuntime();
  const info = byKey.get(runtime.scenario) ?? CATALOGUE[0];
  return mapControlPayload({
    enabled: true,
    available: true,
    reason: "",
    scenario: info.id,
    scenario_label: info.label,
    elapsed_s: (Date.now() - runtime.startedAt) / 1000,
    generation: runtime.generation,
    sensor_source: "mock",
    drill: info.id !== "resting",
    scenarios: CATALOGUE.map((s) => ({
      id: s.id,
      alias: s.alias,
      label: s.label,
      group: s.group,
      expected_tier: s.expectedTier,
      description: s.description
    }))
  });
}
