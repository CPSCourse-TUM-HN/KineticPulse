import { NextRequest, NextResponse } from "next/server";
import { ControlRequestError, type BackendControlClient } from "./backendControlClient";

export interface ControlApiDependencies {
  createClient: () => BackendControlClient;
}

/** Injectable server handler kept outside the route module for testing. */
export async function handleControlRead(dependencies: ControlApiDependencies) {
  try {
    const model = await dependencies.createClient().read();
    return NextResponse.json(model, { headers: { "Cache-Control": "no-store" } });
  } catch (reason) {
    return errorResponse(reason);
  }
}

export async function handleControlCommand(
  request: NextRequest,
  dependencies: ControlApiDependencies
) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { error: "bad_request", message: "Expected a JSON body." },
      { status: 400 }
    );
  }

  const action = (body as { action?: unknown } | null)?.action;
  const scenario = (body as { scenario?: unknown } | null)?.scenario;
  const client = dependencies.createClient();

  try {
    if (action === "restart") {
      return NextResponse.json(await client.restart());
    }
    if (action === "reset") {
      return NextResponse.json(await client.reset());
    }
    if (action === "scenario" || action === undefined) {
      if (typeof scenario !== "string" || !scenario.trim()) {
        return NextResponse.json(
          { error: "bad_request", message: 'Expected a "scenario" string.' },
          { status: 400 }
        );
      }
      return NextResponse.json(await client.selectScenario(scenario.trim()));
    }
    return NextResponse.json(
      { error: "bad_request", message: `Unknown action ${String(action)}.` },
      { status: 400 }
    );
  } catch (reason) {
    return errorResponse(reason);
  }
}

function errorResponse(reason: unknown) {
  if (reason instanceof ControlRequestError) {
    // Preserve the Jetson's status so the panel can distinguish "turned off"
    // (403) from "nothing to drive" (409) from a genuine outage (503).
    return NextResponse.json(
      { error: reason.code, message: reason.message },
      { status: reason.status }
    );
  }
  const message =
    reason instanceof Error ? reason.message : "Control backend unavailable";
  return NextResponse.json({ error: "unreachable", message }, { status: 503 });
}
