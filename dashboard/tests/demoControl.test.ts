import { describe, expect, it } from "vitest";
import { applyDemoScenario, demoControlModel } from "../lib/control/demoRuntime";

describe("laptop demo control", () => {
  it("activates a scenario by alias and marks a drill", () => {
    const resting = applyDemoScenario("resting");
    expect(resting.scenario).toBe("resting");
    expect(resting.drill).toBe(false);

    const trip = applyDemoScenario("trip-fall");
    expect(trip.scenario).toBe("demo_trip_fall");
    expect(trip.drill).toBe(true);
    expect(trip.generation).toBeGreaterThan(resting.generation);
    expect(demoControlModel().scenarioLabel).toBe("Trip fall");
  });
});
