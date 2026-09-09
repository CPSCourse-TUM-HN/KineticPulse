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
 * Laptop dashboard defaults (same Wi-Fi as this machine). Expo Go reads these
 * on a fresh install. Override in Server settings if the LAN IP changed.
 */
export const DEFAULT_SETTINGS: AppSettings = {
  signalingHttpBase: "http://192.168.0.220:8787",
  signalingWsBase: "ws://192.168.0.220:8787/ws",
  caregiverToken: "dev-caregiver",
  iceServersText: "stun:stun.l.google.com:19302",
  monitoringHttpBase: "http://192.168.0.220:3000/api/monitoring"
};
