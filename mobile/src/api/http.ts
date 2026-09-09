/** Android's TCP timeout is ~2 min. Without this the home screen waits on a dead Tailscale IP. */
const DEFAULT_MS = 8000;

export async function fetchTimed(
  url: string,
  init: RequestInit = {},
  ms = DEFAULT_MS
): Promise<Response> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), ms);
  try {
    return await fetch(url, { ...init, signal: ctrl.signal });
  } catch (e) {
    if (e instanceof Error && e.name === "AbortError") {
      throw new Error("Couldn't reach the monitor.");
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}
