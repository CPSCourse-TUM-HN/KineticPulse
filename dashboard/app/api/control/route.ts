import { NextRequest } from "next/server";
import { createControlClient } from "../../../lib/control/backendControlClient";
import {
  handleControlCommand,
  handleControlRead
} from "../../../lib/control/controlApiHandler";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const dependencies = { createClient: createControlClient };

export async function GET() {
  return handleControlRead(dependencies);
}

export async function POST(request: NextRequest) {
  return handleControlCommand(request, dependencies);
}
