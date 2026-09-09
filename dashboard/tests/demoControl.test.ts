import { afterEach, describe, expect, it } from "vitest";
import { applyDemoScenario, demoControlModel } from "../lib/control/demoRuntime";
import { DemoMonitoringDataSource } from "../lib/monitoring/demoDataSource";

afterEach(() => {
  applyDemoScenario("resting");
});

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

  it("drives monitoring vitals from the same scenario file", async () => {
    applyDemoScenario("fall-c-syncope");
    const model = await new DemoMonitoringDataSource().read();
    expect(model.emergency.level).toBe("tier_2_cardiac");
    expect(model.emergency.scenario).toBe("fall_c_syncope");
    expect(model.fall.detected).toBe(true);
    expect(model.heartRate.status).toBe("pulse_lost");
    expect(model.alertDispatch.status).toBe("sent");
  });
});
