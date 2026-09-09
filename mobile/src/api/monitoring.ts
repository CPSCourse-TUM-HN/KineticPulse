import { AppSettings } from "@/types/session";

export type MonitoringEvent = {
  id: string;
  timestamp_ms: number;
  severity: string;
  category: string;
  title: string;
  detail: string;
};

export type LiveVitals = {
  bpm: number | null;
  hrStatus: string;
  hrSignature: string;
  sensorConnection: string;
  /** "hardware" once the ESP32 wristband is streaming, "simulated" on synthetic PPG. */
  ppgSource: string;
  hrSimulated: boolean;
  emergencyTier: string;
  scenario: string;
  reason: string;
  pose: string;
  accel: string;
  accelG: number | null;
  detectorClass: string | null;
  detectorConf: number | null;
  actionClass: string | null;
  actionConf: number | null;
  subjectId: string;
  location: string;
  events: MonitoringEvent[];
  updatedAtMs: number;
};

/** Full Jetson `GET /monitoring` URL. */
export function monitoringUrl(settings: AppSettings): string {
  if (settings.monitoringHttpBase?.trim()) {
    return settings.monitoringHttpBase.trim().replace(/\/$/, "");
  }
  try {
    const u = new URL(settings.signalingHttpBase);
    u.port = "8790";
    u.pathname = "/monitoring";
    u.search = "";
    u.hash = "";
    return u.toString().replace(/\/$/, "");
  } catch {
    return "http://localhost:8790/monitoring";
  }
}

/**
 * Origin of the monitoring server, without the `/monitoring` path — the base
 * for the sibling `/control` and `/preview.jpg` endpoints. The setup QR ships
 * `monitoringHttpBase` as a full URL including the path, so strip it.
 */
export function monitoringOrigin(settings: AppSettings): string {
  return monitoringUrl(settings).replace(/\/monitoring\/?$/, "");
}

/** Latest annotated frame. RN Image can decode JPEG; it cannot decode MJPEG. */
export function previewStreamUrl(settings: AppSettings): string {
  return `${monitoringOrigin(settings)}/preview.jpg`;
}

function hrStatusFrom(sensorConnection: unknown, hrSig: unknown, hr: unknown): string {
  if (sensorConnection === "disconnected") return "unavailable";
  if (hrSig === "pulse_lost") return "pulse_lost";
  if (typeof hr !== "number") return "unavailable";
  if (hr < 50) return "low";
  if (hr > 100) return "elevated";
  return "normal";
}

/** Poll Jetson GET /monitoring for continuous vitals (same contract as dashboard). */
export async function fetchLiveVitals(settings: AppSettings): Promise<LiveVitals> {
  const response = await fetch(monitoringUrl(settings), {
    headers: { Accept: "application/json" }
  });
  if (!response.ok) throw new Error(`Monitoring HTTP ${response.status}`);
  const json = await response.json();
  const snap = json.snapshot ?? {};
  const hr = snap.latest_hr_bpm;

  return {
    bpm: typeof hr === "number" ? hr : null,
    hrStatus: hrStatusFrom(json.sensor?.connection, snap.hr, hr),
    hrSignature: snap.hr ?? "unknown",
    sensorConnection: json.sensor?.connection ?? "unknown",
    ppgSource: json.sensor?.ppg_source ?? "unknown",
    hrSimulated: snap.hr_simulated === true || json.sensor?.ppg_source === "simulated",
    emergencyTier: snap.decision?.tier ?? "none",
    scenario: snap.decision?.scenario ?? "",
    reason: snap.decision?.reason ?? "",
    pose: snap.pose ?? "unknown",
    accel: snap.accel ?? "unknown",
    accelG: typeof snap.latest_accel_g === "number" ? snap.latest_accel_g : null,
    detectorClass: snap.detector_class ?? null,
    detectorConf: typeof snap.detector_conf === "number" ? snap.detector_conf : null,
    actionClass: snap.action_class ?? null,
    actionConf: typeof snap.action_conf === "number" ? snap.action_conf : null,
    subjectId: json.subject_id ?? "unknown",
    location: json.location ?? "unknown",
    events: Array.isArray(json.events) ? json.events : [],
    updatedAtMs: snap.timestamp_ms ?? Date.now()
  };
}
