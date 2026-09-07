import { describe, expect, it } from "vitest";
import { mapControlPayload } from "../lib/control/model";
import { controlPayload } from "./controlFixture";

describe("control payload adapter", () => {
  it("maps the Jetson wire contract into the panel model", () => {
    const model = mapControlPayload(controlPayload());

    expect(model.enabled).toBe(true);
    expect(model.available).toBe(true);
    expect(model.scenario).toBe("resting");
    expect(model.scenarioLabel).toBe("Resting");
    expect(model.elapsedS).toBe(12.5);
    expect(model.sensorSource).toBe("mock");
    expect(model.drill).toBe(false);
    expect(model.scenarios.map((s) => s.alias)).toEqual([
      "resting",
      "trip-fall",
      "fall-c-syncope"
    ]);
    expect(model.scenarios[1].expectedTier).toBe("tier_1_verify");
  });

  it("survives a payload with no scenario catalogue", () => {
    const payload = { ...controlPayload(), scenarios: undefined } as never;
    expect(mapControlPayload(payload).scenarios).toEqual([]);
  });

  it("carries the unavailable reason through verbatim for the operator", () => {
    const payload = controlPayload();
    payload.available = false;
    payload.reason = "Scenario control needs the synthetic sensor client.";
    const model = mapControlPayload(payload);
    expect(model.available).toBe(false);
    expect(model.reason).toContain("synthetic sensor client");
  });
});
