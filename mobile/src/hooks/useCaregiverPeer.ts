import { useEffect, useState } from "react";

import { hasWebRTC } from "@/lib/hasWebRTC";
import { AppSettings, SessionMeta } from "@/types/session";

export type PeerState = "idle" | "connecting" | "connected" | "failed" | "closed";

type Args = {
  sessionId: string;
  settings: AppSettings | null;
  enabled: boolean;
};

const idle = {
  connectionState: "idle" as PeerState,
  remoteStream: null as { toURL: () => string } | null,
  sessionMeta: null as SessionMeta | null,
  error: ""
};

/**
 * WebRTC is a native module. Expo Go does not ship it, so this hook stays a
 * stub there and only loads `startCaregiverPeer` in a real native build.
 */
export function useCaregiverPeer({ sessionId, settings, enabled }: Args) {
  const [state, setState] = useState(idle);

  useEffect(() => {
    if (!enabled || !settings || !sessionId) {
      setState(idle);
      return;
    }
    if (!hasWebRTC) {
      setState({
        ...idle,
        error: "Live video needs a native build — Expo Go cannot load WebRTC."
      });
      return;
    }
    let cancelled = false;
    let stop = () => {};
    import("./startCaregiverPeer").then(({ startCaregiverPeer }) => {
      if (cancelled) return;
      stop = startCaregiverPeer({ sessionId, settings }, (patch) => {
        setState((prev) => ({ ...prev, ...patch }));
      });
    });
    return () => {
      cancelled = true;
      stop();
    };
  }, [sessionId, settings, enabled]);

  return state;
}
