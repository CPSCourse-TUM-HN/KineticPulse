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

These are unauthenticated, so they stay disabled by default and the reasons
are spelled out in :mod:`kineticpulse.control`. No CORS headers are emitted:
the dashboard reaches them from its own server-side API route, and a browser
on the LAN should not be able to page a caregiver.
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
) -> Dict[str, Any]:
    """Build the JSON envelope consumed by the Next.js real-mode adapter.

    ``control`` adds the scenario-panel state and the ``simulation`` block.
    Both are always present when a controller is supplied - a marker that
    only appears during a drill is a marker nobody checks for.
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
    """Tiny asyncio HTTP server: ``GET /monitoring``, ``/control`` (+ ``/healthz``)."""

    #: Cap on a control request body. The largest legitimate one is a few
    #: dozen bytes of JSON, so anything bigger is a mistake or an attack.
    MAX_BODY_BYTES = 4096

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
    ) -> None:
        self.host = host
        self.port = port
        self.alerts = alerts
        self.latest_snapshot = latest_snapshot
        self.sensors = sensors
        self.runtime_status = runtime_status or CaregiverRuntimeStatus()
        self.control = control
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
                )
            ),
        )

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
