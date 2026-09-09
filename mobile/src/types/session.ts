export type SessionMeta = {
  session_id?: string;
  timestamp_ms?: number;
  tier?: string;
  scenario?: string;
  subject_id?: string;
  location?: string;
  reason?: string;
  detector_class?: string;
  action_class?: string;
  action_confidence?: number;
  heart_rate_bpm?: number | null;
  hr_signature?: string | null;
  accel_magnitude_g?: number | null;
  accel_signature?: string | null;
  extra?: { voice_verdict?: string };
};

export type SessionSummary = {
  session_id: string;
  status: string;
  created_at_ms: number;
  updated_at_ms?: number;
  meta?: SessionMeta;
};

export type IceServerConfig = {
  urls: string | string[];
  username?: string;
  credential?: string;
};

export type AppSettings = {
  signalingHttpBase: string;
  signalingWsBase: string;
  caregiverToken: string;
  /** One STUN/TURN URL per line, or a JSON array of IceServerConfig */
  iceServersText: string;
  /** Jetson GET /monitoring URL; default derived from signaling host :8790 */
  monitoringHttpBase?: string;
};

/**
 * Demo defaults point at the Jetson's Tailscale address so the app works on a
 * fresh install without scanning the setup QR. Scanning still overrides these.
 */
export const DEFAULT_SETTINGS: AppSettings = {
  signalingHttpBase: "http://100.71.200.114:8787",
  signalingWsBase: "ws://100.71.200.114:8787/ws",
  caregiverToken: "",
  iceServersText: "stun:stun.l.google.com:19302",
  monitoringHttpBase: "http://100.71.200.114:8790/monitoring"
};
