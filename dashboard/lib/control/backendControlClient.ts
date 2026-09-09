import { getDemoControlClient } from "./demoControlClient";
import { mapControlPayload, type ControlModel, type ControlWirePayload } from "./model";

/**
 * Server-side client for the Jetson control surface.
 *
 * Only ever called from the Next.js route handler, never from the browser.
 * That is deliberate: the Jetson endpoints are unauthenticated, so the
 * dashboard server is the single place that can reach them and the buttons
 * are not exposed to anything else on the LAN.
 */
export class BackendControlClient {
  constructor(private readonly baseUrl: string) {}

  async read(): Promise<ControlModel> {
    return mapControlPayload(await this.request("/control", "GET"));
  }

  async selectScenario(scenario: string): Promise<ControlModel> {
    return mapControlPayload(
      await this.request("/control/scenario", "POST", { scenario })
    );
  }

  async restart(): Promise<ControlModel> {
    return mapControlPayload(await this.request("/control/restart", "POST", {}));
  }

  async reset(): Promise<ControlModel> {
    return mapControlPayload(await this.request("/control/reset", "POST", {}));
  }

  private async request(
    path: string,
    method: "GET" | "POST",
    body?: unknown
  ): Promise<ControlWirePayload> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      cache: "no-store",
      headers: {
        Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" })
      },
      body: body === undefined ? undefined : JSON.stringify(body)
    });

    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      // The backend's own error codes carry the operator-facing reason
      // (disabled / unavailable / unknown_scenario); pass them through rather
      // than flattening everything to "request failed".
      const error = new ControlRequestError(
        (payload as { message?: string } | null)?.message ??
          `Control request failed (HTTP ${response.status})`,
        (payload as { error?: string } | null)?.error ?? "request_failed",
        response.status
      );
      throw error;
    }
    return payload as ControlWirePayload;
  }
}

export class ControlRequestError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number
  ) {
    super(message);
    this.name = "ControlRequestError";
  }
}

export function createControlClient(): BackendControlClient {
  if (process.env.KINETICPULSE_DEMO === "1") {
    return getDemoControlClient();
  }
  const monitoringUrl =
    process.env.KINETICPULSE_MONITORING_HTTP_URL ??
    "http://127.0.0.1:8790/monitoring";
  return new BackendControlClient(monitoringUrl.replace(/\/monitoring\/?$/, ""));
}
