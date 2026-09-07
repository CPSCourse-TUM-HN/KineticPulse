import { type ControlModel } from "./model";

/** Browser-side boundary: everything goes through the dashboard's own route. */
async function call(init: RequestInit & { signal?: AbortSignal }): Promise<ControlModel> {
  const response = await fetch("/api/control", { cache: "no-store", ...init });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw new Error(
      (payload as { message?: string } | null)?.message ??
        `Control request failed (HTTP ${response.status})`
    );
  }
  return payload as ControlModel;
}

export function fetchControlState(signal?: AbortSignal): Promise<ControlModel> {
  return call({ method: "GET", headers: { Accept: "application/json" }, signal });
}

export function selectScenario(scenario: string): Promise<ControlModel> {
  return command({ action: "scenario", scenario });
}

export function restartScenario(): Promise<ControlModel> {
  return command({ action: "restart" });
}

export function resetScenario(): Promise<ControlModel> {
  return command({ action: "reset" });
}

function command(body: Record<string, string>): Promise<ControlModel> {
  return call({
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body)
  });
}
