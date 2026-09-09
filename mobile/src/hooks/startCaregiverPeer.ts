import { registerGlobals, MediaStream, RTCIceCandidate, RTCPeerConnection, RTCSessionDescription } from "react-native-webrtc";

import { buildWsUrl, parseIceServers } from "@/api/sessions";
import { AppSettings, SessionMeta } from "@/types/session";

import type { PeerState } from "./useCaregiverPeer";

registerGlobals();

type TrackEvent = { streams: MediaStream[] };
type IceEvent = { candidate: RTCIceCandidate | null };
type PeerConnection = RTCPeerConnection & {
  onconnectionstatechange: (() => void) | null;
  ontrack: ((event: TrackEvent) => void) | null;
  onicecandidate: ((event: IceEvent) => void) | null;
};

export type PeerSnapshot = {
  connectionState: PeerState;
  remoteStream: { toURL: () => string } | null;
  sessionMeta: SessionMeta | null;
  error: string;
};

type Args = {
  sessionId: string;
  settings: AppSettings;
};

/** Loaded only when the native WebRTC module exists (not Expo Go). */
export function startCaregiverPeer(
  { sessionId, settings }: Args,
  onChange: (patch: Partial<PeerSnapshot>) => void
): () => void {
  let alive = true;
  const pc = new RTCPeerConnection({
    iceServers: parseIceServers(settings.iceServersText)
  }) as PeerConnection;
  const ws = new WebSocket(buildWsUrl(settings));

  const cleanup = () => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "close-session", payload: { session_id: sessionId } }));
    }
    ws.close();
    pc.close();
  };

  onChange({ connectionState: "connecting", error: "" });

  pc.onconnectionstatechange = () => {
    const state = pc.connectionState as PeerState;
    if (state === "connected" || state === "failed" || state === "closed") {
      onChange({ connectionState: state });
    }
  };

  pc.ontrack = (event) => {
    const [stream] = event.streams;
    if (stream) onChange({ remoteStream: stream });
  };

  pc.onicecandidate = (event) => {
    if (!event.candidate || ws.readyState !== WebSocket.OPEN) return;
    ws.send(
      JSON.stringify({
        type: "ice-candidate",
        payload: {
          session_id: sessionId,
          role: "caregiver",
          candidate: event.candidate.candidate,
          sdpMid: event.candidate.sdpMid,
          sdpMLineIndex: event.candidate.sdpMLineIndex
        }
      })
    );
  };

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "join-session", payload: { session_id: sessionId } }));
  };

  ws.onmessage = async (event) => {
    let msg: { type?: string; payload?: Record<string, unknown> };
    try {
      msg = JSON.parse(String(event.data));
    } catch {
      return;
    }
    const type = msg.type;
    const payload = msg.payload ?? {};
    if (type === "offer") {
      const offer = payload.offer as { sdp: string; type: string };
      const meta = payload.meta as SessionMeta | undefined;
      if (meta) onChange({ sessionMeta: meta });
      await pc.setRemoteDescription(new RTCSessionDescription(offer));
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      ws.send(
        JSON.stringify({
          type: "answer",
          payload: {
            session_id: sessionId,
            answer: { type: pc.localDescription?.type, sdp: pc.localDescription?.sdp }
          }
        })
      );
    } else if (type === "ice-candidate" && payload.candidate) {
      await pc.addIceCandidate(
        new RTCIceCandidate({
          candidate: String(payload.candidate),
          sdpMid: (payload.sdpMid as string | null) ?? undefined,
          sdpMLineIndex: (payload.sdpMLineIndex as number | null) ?? undefined
        })
      );
    } else if (type === "session-closed") {
      onChange({ connectionState: "closed" });
    } else if (type === "error") {
      onChange({ error: String((payload as { message?: string }).message ?? "signaling error") });
    }
  };

  ws.onerror = () => {
    if (alive) onChange({ error: "WebSocket connection failed" });
  };

  return () => {
    alive = false;
    cleanup();
  };
}
