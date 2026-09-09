import type { BackendControlClient } from "./backendControlClient";
import { applyDemoScenario, demoControlModel, getDemoRuntime } from "./demoRuntime";

/** In-process stand-in for the Jetson control surface when KINETICPULSE_DEMO=1. */
export function getDemoControlClient(): BackendControlClient {
  return {
    async read() {
      return demoControlModel();
    },
    async selectScenario(scenario: string) {
      return applyDemoScenario(scenario);
    },
    async restart() {
      const runtime = getDemoRuntime();
      return applyDemoScenario(runtime.scenario);
    },
    async reset() {
      return applyDemoScenario("resting");
    }
  } as BackendControlClient;
}
