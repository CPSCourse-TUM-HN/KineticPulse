import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import {
  BackendControlClient,
  ControlRequestError
} from "../lib/control/backendControlClient";
import {
  handleControlCommand,
  handleControlRead
} from "../lib/control/controlApiHandler";
import { mapControlPayload } from "../lib/control/model";
import { controlPayload } from "./controlFixture";

function stubClient(overrides: Partial<BackendControlClient> = {}): BackendControlClient {
  const base = {
    async read() {
      return mapControlPayload(controlPayload());
    },
    async selectScenario(scenario: string) {
      const payload = controlPayload();
      payload.scenario = scenario === "trip-fall" ? "demo_trip_fall" : scenario;
      payload.drill = scenario !== "resting";
      return mapControlPayload(payload);
    },
    async restart() {
      return mapControlPayload(controlPayload());
    },
    async reset() {
      return mapControlPayload(controlPayload());
    }
  };
  return { ...base, ...overrides } as BackendControlClient;
}

function post(body: unknown): NextRequest {
  return new NextRequest("http://localhost/api/control", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
}

describe("control API handler", () => {
  it("reads the panel state", async () => {
    const response = await handleControlRead({ createClient: () => stubClient() });
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({ scenario: "resting" });
  });

  it("activates a scenario", async () => {
    const response = await handleControlCommand(
      post({ action: "scenario", scenario: "trip-fall" }),
      { createClient: () => stubClient() }
    );
    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toMatchObject({
      scenario: "demo_trip_fall",
      drill: true
    });
  });

  it("treats a bare scenario body as an activation", async () => {
    const response = await handleControlCommand(post({ scenario: "trip-fall" }), {
      createClient: () => stubClient()
    });
    expect(response.status).toBe(200);
  });

  it("rejects a command with no scenario", async () => {
    const response = await handleControlCommand(post({ action: "scenario" }), {
      createClient: () => stubClient()
    });
    expect(response.status).toBe(400);
  });

  it("rejects an unknown action", async () => {
    const response = await handleControlCommand(post({ action: "detonate" }), {
      createClient: () => stubClient()
    });
    expect(response.status).toBe(400);
  });

  // The panel has to tell "switched off" from "nothing to drive" from "the
  // Jetson is gone", so the backend's status and code must survive the proxy.
  it.each([
    [403, "disabled"],
    [409, "unavailable"],
    [400, "unknown_scenario"]
  ])("passes through the backend's HTTP %i / %s rejection", async (status, code) => {
    const response = await handleControlCommand(post({ scenario: "trip-fall" }), {
      createClient: () =>
        stubClient({
          async selectScenario() {
            throw new ControlRequestError("nope", code, status);
          }
        })
    });
    expect(response.status).toBe(status);
    await expect(response.json()).resolves.toMatchObject({ error: code });
  });

  it("returns 503 when the Jetson is unreachable", async () => {
    const response = await handleControlRead({
      createClient: () =>
        stubClient({
          async read() {
            throw new Error("connect ECONNREFUSED 127.0.0.1:8790");
          }
        })
    });
    expect(response.status).toBe(503);
    await expect(response.json()).resolves.toMatchObject({ error: "unreachable" });
  });
});

describe("backend control client", () => {
  it("derives the control base URL from the monitoring URL", async () => {
    const calls: string[] = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (url: string) => {
      calls.push(String(url));
      return new Response(JSON.stringify(controlPayload()), {
        status: 200,
        headers: { "Content-Type": "application/json" }
      });
    }) as typeof fetch;
    try {
      await new BackendControlClient("http://jetson.local:8790").read();
    } finally {
      globalThis.fetch = originalFetch;
    }
    expect(calls).toEqual(["http://jetson.local:8790/control"]);
  });

  it("raises a ControlRequestError carrying the backend code and status", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async () =>
      new Response(JSON.stringify({ error: "disabled", message: "turned off" }), {
        status: 403,
        headers: { "Content-Type": "application/json" }
      })) as typeof fetch;
    try {
      await expect(
        new BackendControlClient("http://x:8790").selectScenario("trip-fall")
      ).rejects.toMatchObject({ code: "disabled", status: 403, message: "turned off" });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
