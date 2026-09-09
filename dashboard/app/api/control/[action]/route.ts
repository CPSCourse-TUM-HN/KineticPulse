import { NextRequest, NextResponse } from "next/server";
import { createControlClient } from "../../../../lib/control/backendControlClient";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

/** Jetson-shaped paths the phone uses: POST /api/control/scenario|reset|restart */
export async function POST(
  request: NextRequest,
  { params }: { params: { action: string } }
) {
  const client = createControlClient();
  try {
    if (params.action === "reset") {
      return NextResponse.json(await client.reset(), { headers: { "Cache-Control": "no-store" } });
    }
    if (params.action === "restart") {
      return NextResponse.json(await client.restart(), { headers: { "Cache-Control": "no-store" } });
    }
    if (params.action === "scenario") {
      const body = (await request.json().catch(() => null)) as { scenario?: unknown } | null;
      if (typeof body?.scenario !== "string" || !body.scenario.trim()) {
        return NextResponse.json(
          { error: "bad_request", message: 'Expected {"scenario": "..."}' },
          { status: 400 }
        );
      }
      return NextResponse.json(await client.selectScenario(body.scenario.trim()), {
        headers: { "Cache-Control": "no-store" }
      });
    }
    return NextResponse.json({ error: "bad_request", message: `Unknown action ${params.action}` }, { status: 400 });
  } catch (reason) {
    const message = reason instanceof Error ? reason.message : "Control backend unavailable";
    return NextResponse.json({ error: "unreachable", message }, { status: 503 });
  }
}
