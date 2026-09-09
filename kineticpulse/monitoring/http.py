"""Stdlib HTTP publisher for the caregiver dashboard monitoring contract.

Serves ``GET /monitoring`` as the ``MonitoringWirePayload`` envelope expected by
``dashboard/lib/monitoring/backendMonitoringAdapter.ts``. Continuous vitals
come from :attr:`FusionEngine.latest`; emergencies still go through webhooks /
WebRTC separately.

Also serves the bench control surface behind ``monitoring.control_enabled``:

* ``GET  /control``           - panel state + the scenario catalogue
* ``POST /control/scenario``  - ``{"scenario": "trip-fall"}``, replays from t=0
* ``POST /control/restart``   - replay the active scenario
* ``POST /control/reset``     - back to the quiet baseline

...and the detection feed behind ``monitoring.preview_stream``:

* ``GET /preview.mjpg``  - the annotated overlay as ``multipart/x-mixed-replace``,
  which an ``<img>`` renders natively; this is what the dashboard's Detection
  panel shows
* ``GET /preview.jpg``   - the newest single frame, for a poster image or a
  client that cannot hold a stream open

Both read :class:`~kineticpulse.vision.frame_bus.PreviewFrameBus`, which the
preview overlay fills. A feed that has stopped producing is reported as ``503``
and the stream is closed rather than left showing the last frame: a still image
of a calm room is the one thing a stopped monitor must not look like.

These are all unauthenticated, so they stay disabled by default and the reasons
are spelled out in :mod:`kineticpulse.control` and in ``config.example.yaml``.
No CORS headers are emitted: the dashboard reaches them from its own
server-side API route, and a browser on the LAN should not be able to page a
caregiver - or watch one.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import unquote_plus

from kineticpulse.config import AlertsConfig
from kineticpulse.control import ControlError, ScenarioController
from kineticpulse.fusion.engine import FusionSnapshot
from kineticpulse.runtime_status import CaregiverRuntimeStatus
from kineticpulse.sensors.ppg_sim import PPG_SOURCE_HARDWARE, PPG_SOURCE_SIMULATED
from kineticpulse.vision.frame_bus import PreviewFrameBus
from kineticpulse.utils.logging import get_logger

log = get_logger(__name__)

_VISION = frozenset({"fallen", "falling", "stand", "sitting"})


def _sensor_connection(sensors: Any) -> str:
    connected = getattr(sensors, "connected", None)
    if connected is True:
        return "connected"
    if connected is False:
        return "disconnected"
    return "degraded"


def _ppg_source(sensors: Any) -> str:
    """Where the heart rate is coming from: ``hardware`` or ``simulated``.

    Surfaced so the dashboard can badge a synthetic pulse. See
    :mod:`kineticpulse.sensors.ppg_sim` for why this must never be dropped
    from the payload.
    """
    source = getattr(sensors, "ppg_source", PPG_SOURCE_HARDWARE)
    return PPG_SOURCE_SIMULATED if source == PPG_SOURCE_SIMULATED else PPG_SOURCE_HARDWARE


def _sensor_source(sensors: Any) -> str:
    """``mock`` when the telemetry is synthesised, else ``hardware``."""
    return str(getattr(sensors, "sensor_source", "hardware"))


def _vision_class(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    key = value.lower()
    return key if key in _VISION else None


def build_monitoring_payload(
    *,
    alerts: AlertsConfig,
    snapshot: Optional[FusionSnapshot],
    sensors: Any = None,
    voice_status: str = "not_required",
    alert_dispatch_status: str = "idle",
    events: Optional[List[Dict[str, Any]]] = None,
    published_at_ms: Optional[int] = None,
    control: Optional[ScenarioController] = None,
    runtime: Optional[Dict[str, Any]] = None,
    preview: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the JSON envelope consumed by the Next.js real-mode adapter.

    ``control`` adds the scenario-panel state and the ``simulation`` block.
    Both are always present when a controller is supplied - a marker that
    only appears during a drill is a marker nobody checks for.

    ``runtime`` carries vision throughput and the accelerator in use. It is on
    the caregiver payload rather than buried in the logs because a CPU
    fallback is a *safety* regression: the same pipeline runs at ~16 FPS on the
    GPU and ~0.65 FPS on the CPU, and at that rate it steps over a fall.

    ``preview`` describes the detection feed - see :func:`preview_payload`. It
    is always present so the dashboard can tell "off in the config" apart from
    "on, but no frames are arriving", and render the reason either way instead
    of a broken image.
    """
    wire_timestamp_ms = (
        published_at_ms
        if published_at_ms is not None
        else int(time.time() * 1000)
    )
    sensor_conn = _sensor_connection(sensors)
    ppg_source = _ppg_source(sensors)
    hr_simulated = ppg_source == PPG_SOURCE_SIMULATED
    event_list = list(events or [])
    control_block = control.state().as_json() if control is not None else None
    runtime_block = runtime or {"health": "unknown", "accelerator": "unknown",
                                "accelerator_device": None, "vision_fps": None,
                                "detector_backend": None, "pose_backend": None}
    preview_block = preview if preview is not None else preview_payload(None)
    simulation_block = (
        control.simulation_payload(ppg_source=ppg_source)
        if control is not None
        else {
            "drill": False,
            "sensor_source": _sensor_source(sensors),
            "ppg_source": ppg_source,
            "scenario": None,
        }
    )
    if snapshot is None:
        return {
            "subject_id": alerts.subject_id,
            "location": alerts.location,
            "system": {"connection": "connected"},
            "sensor": {"connection": sensor_conn, "ppg_source": ppg_source},
            "snapshot": {
                "decision": {
                    "tier": "none",
                    "scenario": "monitoring",
                    "reason": "Fusion engine warming up.",
                },
                "pose": "unknown",
                "accel": "unknown",
                "hr": "unknown",
                "latest_hr_bpm": None,
                "hr_simulated": hr_simulated,
                "latest_accel_g": None,
                "detector_class": None,
                "detector_conf": None,
                "action_class": None,
                "action_conf": None,
                "timestamp_ms": wire_timestamp_ms,
            },
            "voice": {"status": voice_status},
            "alert_dispatch": {"status": alert_dispatch_status},
            "events": event_list,
            "simulation": simulation_block,
            "control": control_block,
            "runtime": runtime_block,
            "preview": preview_block,
        }

    return {
        "subject_id": alerts.subject_id,
        "location": alerts.location,
        "system": {"connection": "connected"},
        "sensor": {"connection": sensor_conn, "ppg_source": ppg_source},
        "snapshot": {
            "decision": {
                "tier": snapshot.decision.tier.value,
                "scenario": snapshot.decision.scenario,
                "reason": snapshot.decision.reason,
            },
            "pose": snapshot.pose.value,
            "accel": snapshot.accel.value,
            "hr": snapshot.hr.value,
            "latest_hr_bpm": snapshot.latest_hr_bpm,
            "hr_simulated": hr_simulated,
            "latest_accel_g": snapshot.latest_accel_g,
            "detector_class": _vision_class(snapshot.detector_class),
            "detector_conf": snapshot.detector_conf,
            "action_class": snapshot.action_class,
            "action_conf": snapshot.action_conf,
            # Fusion timestamps are monotonic for sensor-window calculations;
            # the dashboard contract requires Unix epoch milliseconds.
            "timestamp_ms": wire_timestamp_ms,
        },
        "voice": {"status": voice_status},
        "alert_dispatch": {"status": alert_dispatch_status},
        "events": event_list,
        "simulation": simulation_block,
        "control": control_block,
        "runtime": runtime_block,
        "preview": preview_block,
    }


#: Paths the dashboard uses for the detection feed. Published in the payload
#: rather than hard-coded in the dashboard so one side can move without the
#: other guessing.
PREVIEW_STREAM_PATH = "/preview.mjpg"
PREVIEW_FRAME_PATH = "/preview.jpg"


def preview_payload(
    frames: Optional[PreviewFrameBus],
    *,
    clients: int = 0,
    max_clients: int = 0,
) -> Dict[str, Any]:
    """Describe the detection feed for the dashboard's Detection panel.

    Shaped like the ``control`` block on purpose: ``enabled`` is what the
    config says, ``available`` is whether frames are actually arriving, and
    ``reason`` explains a False. The panel needs all three - "the operator
    turned this off" and "the camera stopped" call for different words on
    screen, and neither should be guessed from a failed image load.
    """
    if frames is None:
        return {
            "enabled": False,
            "available": False,
            "reason": (
                "The detection feed is off in the runtime config "
                "(monitoring.preview_stream)."
            ),
            "stream_path": PREVIEW_STREAM_PATH,
            "frame_path": PREVIEW_FRAME_PATH,
            "fps": None,
            "clients": 0,
            "max_clients": 0,
        }

    frame = frames.latest()
    if frame is None:
        available, reason = False, "Waiting for the first annotated frame."
    elif frame.age_s() > frames.stale_after_s:
        available = False
        reason = (
            f"No frame for {frame.age_s():.0f}s - the vision pipeline has "
            "stopped producing."
        )
    else:
        available, reason = True, ""

    return {
        "enabled": True,
        "available": available,
        "reason": reason,
        "stream_path": PREVIEW_STREAM_PATH,
        "frame_path": PREVIEW_FRAME_PATH,
        "fps": None if frame is None or frame.fps is None else round(frame.fps, 1),
        "clients": clients,
        "max_clients": max_clients,
    }


def _json(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _scenario_from_body(body: bytes) -> str:
    """Pull ``scenario`` out of a POST body, as JSON or as a form field."""
    text = body.decode("utf-8", errors="replace").strip()
    if not text:
        raise ControlError("bad_request", "Request body is empty; expected {\"scenario\": ...}.")
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ControlError("bad_request", f"Body is not valid JSON: {exc}") from exc
        if not isinstance(obj, dict):
            raise ControlError("bad_request", "Body must be a JSON object.")
        value = obj.get("scenario")
        if not isinstance(value, str) or not value.strip():
            raise ControlError("bad_request", 'Body must include a "scenario" string.')
        return value.strip()
    # Tolerate `scenario=trip-fall` so the endpoint is usable from a bare curl.
    key, _, value = text.partition("=")
    if key.strip() == "scenario" and value.strip():
        return unquote_plus(value.strip())
    raise ControlError("bad_request", 'Expected {"scenario": "..."} or scenario=...')


class MonitoringPublisher:
    """Tiny asyncio HTTP server: ``GET /monitoring``, ``/control``,
    ``/preview.mjpg`` (+ ``/healthz``)."""

    #: Cap on a control request body. The largest legitimate one is a few
    #: dozen bytes of JSON, so anything bigger is a mistake or an attack.
    MAX_BODY_BYTES = 4096

    #: How often a stream checks the bus for a new frame. The bus holds one
    #: slot, so this only has to be short relative to a frame interval: at 8 ms
    #: a 17 FPS pipeline (59 ms/frame) is passed through without adding a
    #: visible frame of latency, and an idle stream costs 125 cheap wakeups a
    #: second rather than a blocked thread.
    PREVIEW_POLL_S = 0.008

    def __init__(
        self,
        *,
        host: str,
        port: int,
        alerts: AlertsConfig,
        latest_snapshot: Callable[[], Optional[FusionSnapshot]],
        sensors: Any = None,
        runtime_status: Optional[CaregiverRuntimeStatus] = None,
        control: Optional[ScenarioController] = None,
        frames: Optional[PreviewFrameBus] = None,
        preview_max_clients: int = 4,
    ) -> None:
        self.host = host
        self.port = port
        self.alerts = alerts
        self.latest_snapshot = latest_snapshot
        self.sensors = sensors
        self.runtime_status = runtime_status or CaregiverRuntimeStatus()
        self.control = control
        self.frames = frames
        self.preview_max_clients = max(1, int(preview_max_clients))
        self._preview_clients = 0
        self._server: Optional[asyncio.AbstractServer] = None
        self._stop = asyncio.Event()

    async def run(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, host=self.host, port=self.port
        )
        socks = ", ".join(str(s.getsockname()) for s in self._server.sockets or [])
        log.info("Monitoring HTTP listening on %s (GET /monitoring)", socks)
        if self.control is not None and self.control.enabled:
            log.warning(
                "Scenario control surface is ENABLED on %s (POST /control/*). "
                "Activating a scenario injects synthetic telemetry and can fire "
                "real alerts. Bench use only.",
                socks,
            )
        if self.frames is not None:
            log.warning(
                "Detection feed is ENABLED on %s (GET %s). This is "
                "unauthenticated live video of the monitored room: anything "
                "that can reach this port can watch. Bind monitoring.host to "
                "127.0.0.1 unless the dashboard is remote.",
                socks, PREVIEW_STREAM_PATH,
            )
        async with self._server:
            try:
                await self._stop.wait()
            finally:
                self._server.close()
                try:
                    await self._server.wait_closed()
                except Exception:
                    pass

    def stop(self) -> None:
        self._stop.set()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5.0)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            writer.close()
            return

        lines = raw.split(b"\r\n")
        request_line = lines[0].decode("latin-1", errors="replace")
        parts = request_line.split()
        method = parts[0] if parts else ""
        target = parts[1] if len(parts) > 1 else "/"
        path = target.split("?", 1)[0]

        if method == "OPTIONS":
            await self._respond(writer, 204, b"", content_type=None)
            return

        if method == "GET":
            await self._handle_get(writer, path)
            return

        if method == "POST":
            body = await self._read_body(reader, lines)
            if body is None:
                await self._respond(
                    writer, 413, b'{"ok":false,"error":"body_too_large"}'
                )
                return
            await self._handle_post(writer, path, body)
            return

        await self._respond(writer, 405, b'{"ok":false,"error":"method_not_allowed"}')

    async def _handle_get(self, writer: asyncio.StreamWriter, path: str) -> None:
        if path in ("/healthz", "/"):
            await self._respond(writer, 200, b'{"ok":true}')
            return

        if path == "/control":
            if self.control is None:
                await self._respond(
                    writer, 404, b'{"ok":false,"error":"control_not_configured"}'
                )
                return
            await self._respond(writer, 200, _json(self.control.state().as_json()))
            return

        if path == PREVIEW_FRAME_PATH:
            await self._serve_preview_frame(writer)
            return

        if path == PREVIEW_STREAM_PATH:
            await self._stream_preview(writer)
            return

        if path != "/monitoring":
            await self._respond(writer, 404, b'{"ok":false,"error":"not_found"}')
            return

        status = self.runtime_status
        await self._respond(
            writer,
            200,
            _json(
                build_monitoring_payload(
                    alerts=self.alerts,
                    snapshot=self.latest_snapshot(),
                    sensors=self.sensors,
                    voice_status=status.voice_status,
                    alert_dispatch_status=status.alert_dispatch_status,
                    events=status.events_payload(),
                    control=self.control,
                    runtime=status.runtime_payload(),
                    preview=preview_payload(
                        self.frames,
                        clients=self._preview_clients,
                        max_clients=self.preview_max_clients,
                    ),
                )
            ),
        )

    # -- detection feed --------------------------------------------------- #

    async def _serve_preview_frame(self, writer: asyncio.StreamWriter) -> None:
        """``GET /preview.jpg`` - the newest overlay frame, or why there isn't one.

        Serves only a *fresh* frame. Handing back the last one the pipeline
        produced would let a poster image outlive the pipeline that drew it,
        which is the failure this whole panel exists to make visible.
        """
        if self.frames is None:
            await self._respond(
                writer, 404, b'{"ok":false,"error":"preview_not_enabled"}'
            )
            return
        frame = self.frames.fresh()
        if frame is None:
            await self._respond(
                writer,
                503,
                _json({
                    "ok": False,
                    "error": "preview_unavailable",
                    "message": preview_payload(self.frames)["reason"],
                }),
            )
            return
        await self._respond(
            writer, 200, frame.jpeg, content_type="image/jpeg"
        )

    async def _stream_preview(self, writer: asyncio.StreamWriter) -> None:
        """``GET /preview.mjpg`` - the overlay as ``multipart/x-mixed-replace``.

        An ``<img src>`` renders this natively, so the dashboard needs no
        player, no WebRTC negotiation and no polling: one connection, and the
        browser swaps each part in as it arrives.

        The stream ends - rather than stalling - as soon as the pipeline stops
        feeding it, so the panel's ``onError`` fires and the caregiver is told
        the feed stopped instead of watching a frozen room.
        """
        if self.frames is None:
            await self._respond(
                writer, 404, b'{"ok":false,"error":"preview_not_enabled"}'
            )
            return
        if self._preview_clients >= self.preview_max_clients:
            # A left-open tab per device would otherwise pin one JPEG-encoding
            # copy of every frame each; the cap keeps the feed from crowding
            # out the actual monitor.
            log.info(
                "Detection feed refused: %d/%d clients already streaming.",
                self._preview_clients, self.preview_max_clients,
            )
            await self._respond(
                writer,
                503,
                b'{"ok":false,"error":"too_many_preview_clients"}',
            )
            return

        boundary = "kineticpulseframe"
        headers = [
            "HTTP/1.1 200 OK",
            "Connection: close",
            "Cache-Control: no-store, no-cache, must-revalidate",
            "Pragma: no-cache",
            f"Content-Type: multipart/x-mixed-replace; boundary={boundary}",
        ]
        separator = f"--{boundary}\r\n".encode("latin-1")
        deadline = self.frames.stale_after_s
        last_seq = 0
        sent = 0

        self._preview_clients += 1
        try:
            writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("latin-1"))
            await writer.drain()
            idle_since = time.monotonic()

            while not self._stop.is_set():
                frame = self.frames.fresh()
                if frame is None or frame.seq == last_seq:
                    if time.monotonic() - idle_since > deadline:
                        log.info(
                            "Detection feed closed after %.0fs without a new "
                            "frame (%d sent).", deadline, sent,
                        )
                        break
                    await asyncio.sleep(self.PREVIEW_POLL_S)
                    continue

                last_seq = frame.seq
                idle_since = time.monotonic()
                writer.write(
                    separator
                    + b"Content-Type: image/jpeg\r\n"
                    + f"Content-Length: {len(frame.jpeg)}\r\n\r\n".encode("latin-1")
                    + frame.jpeg
                    + b"\r\n"
                )
                # drain() is the backpressure signal *and* the disconnect
                # signal: a closed tab raises here rather than silently
                # buffering frames nobody will read.
                await writer.drain()
                sent += 1

        except (ConnectionResetError, BrokenPipeError):
            pass                       # caregiver closed the tab; not an error
        except asyncio.CancelledError:
            raise
        except Exception as exc:       # pragma: no cover - transport oddities
            log.debug("Detection feed ended: %s", exc)
        finally:
            self._preview_clients -= 1
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _handle_post(
        self, writer: asyncio.StreamWriter, path: str, body: bytes
    ) -> None:
        if not path.startswith("/control/"):
            await self._respond(writer, 404, b'{"ok":false,"error":"not_found"}')
            return
        if self.control is None:
            await self._respond(
                writer, 404, b'{"ok":false,"error":"control_not_configured"}'
            )
            return

        action = path[len("/control/"):].strip("/")
        try:
            if action == "scenario":
                state = self.control.select(_scenario_from_body(body))
            elif action == "restart":
                state = self.control.restart()
            elif action == "reset":
                state = self.control.reset()
            else:
                await self._respond(
                    writer, 404, b'{"ok":false,"error":"unknown_control_action"}'
                )
                return
        except ControlError as exc:
            status = {
                "disabled": 403,
                "unavailable": 409,
                "unknown_scenario": 400,
                "bad_request": 400,
            }.get(exc.code, 400)
            log.warning("Scenario control rejected (%s): %s", exc.code, exc.message)
            await self._respond(
                writer,
                status,
                _json({"ok": False, "error": exc.code, "message": exc.message}),
            )
            return

        await self._respond(writer, 200, _json({"ok": True, **state.as_json()}))

    async def _read_body(
        self, reader: asyncio.StreamReader, header_lines: List[bytes]
    ) -> Optional[bytes]:
        """Read exactly ``Content-Length`` bytes, or ``None`` if oversized."""
        length = 0
        for line in header_lines[1:]:
            name, _, value = line.partition(b":")
            if name.strip().lower() == b"content-length":
                try:
                    length = int(value.strip())
                except ValueError:
                    length = 0
                break
        if length <= 0:
            return b""
        if length > self.MAX_BODY_BYTES:
            return None
        try:
            return await asyncio.wait_for(reader.readexactly(length), timeout=5.0)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError):
            return b""

    async def _respond(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes,
        *,
        content_type: Optional[str] = "application/json",
    ) -> None:
        reason = {
            200: "OK",
            204: "No Content",
            400: "Bad Request",
            403: "Forbidden",
            404: "Not Found",
            405: "Method Not Allowed",
            409: "Conflict",
            413: "Payload Too Large",
            503: "Service Unavailable",
        }.get(status, "OK")
        headers = [
            f"HTTP/1.1 {status} {reason}",
            "Connection: close",
            "Cache-Control: no-store",
            f"Content-Length: {len(body)}",
        ]
        if content_type:
            headers.append(f"Content-Type: {content_type}")
        writer.write(("\r\n".join(headers) + "\r\n\r\n").encode("latin-1") + body)
        try:
            await writer.drain()
        except Exception:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
