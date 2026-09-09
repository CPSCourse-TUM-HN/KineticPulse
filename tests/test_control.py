"""Tests for the runtime scenario control surface.

Split in three:

* the :class:`ScenarioClock` timing primitive and the mock client following it,
* :class:`ScenarioController` policy - who is allowed to press the button, and
* the HTTP endpoints plus the ``simulation`` / ``control`` payload blocks.

The gating and labelling assertions are load-bearing. Activating a Tier-2
scenario runs the real dispatch path, so "disabled by default" and "every
payload says it was a drill" are the properties that make this tool safe to
have in the tree at all. If one fails, fix the code rather than the test.
"""

from __future__ import annotations

import asyncio
import json
from typing import List, Optional

import pytest

from kineticpulse.alerts.payload import build_payload
from kineticpulse.config import AlertsConfig, MonitoringConfig, WristbandConfig
from kineticpulse.control import (
    SCENARIO_CATALOGUE,
    SENSOR_SOURCE_HARDWARE,
    SENSOR_SOURCE_MOCK,
    ControlError,
    ScenarioController,
    build_scenario_controller,
    resolve_scenario,
)
from kineticpulse.monitoring.http import MonitoringPublisher, build_monitoring_payload
from kineticpulse.runtime_status import CaregiverRuntimeStatus
from kineticpulse.sensors import build_sensor_client
from kineticpulse.sensors.mock import (
    DEMO_PLAYBOOKS,
    MOCK_SCENARIOS,
    MockSensorClient,
    ScenarioClock,
    scenario_fall_offset_s,
)
from kineticpulse.sensors.parser import AccelSample, HrSample, PulseLost
from kineticpulse.sensors.tcp import TcpSensorServer


class _FakeClock:
    """Manual millisecond clock so scenario timing needs no sleeping."""

    def __init__(self, start_ms: int = 1_000) -> None:
        self.now_ms = start_ms

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, seconds: float) -> None:
        self.now_ms += int(seconds * 1000)


# --------------------------------------------------------------------------- #
# ScenarioClock
# --------------------------------------------------------------------------- #


def test_clock_starts_at_zero_and_advances() -> None:
    fake = _FakeClock()
    clock = ScenarioClock("resting", clock=fake)
    assert clock.scenario == "resting"
    assert clock.elapsed_s == 0.0
    fake.advance(3.5)
    assert clock.elapsed_s == pytest.approx(3.5)


def test_selecting_a_scenario_replays_from_zero_and_bumps_generation() -> None:
    fake = _FakeClock()
    clock = ScenarioClock("resting", clock=fake)
    fake.advance(20.0)
    assert clock.elapsed_s == pytest.approx(20.0)

    clock.select("demo_trip_fall")
    assert clock.scenario == "demo_trip_fall"
    assert clock.elapsed_s == 0.0
    assert clock.generation == 1

    fake.advance(5.0)
    clock.restart()
    assert clock.elapsed_s == 0.0
    assert clock.generation == 2


def test_clock_rejects_unknown_scenarios() -> None:
    with pytest.raises(ValueError, match="Unknown scenario"):
        ScenarioClock("nope")
    clock = ScenarioClock("resting")
    with pytest.raises(ValueError, match="Unknown scenario"):
        clock.select("nope")
    assert clock.scenario == "resting", "a rejected switch must not take effect"


def test_fall_offset_only_applies_to_the_coarse_scenarios() -> None:
    assert scenario_fall_offset_s("resting") is None
    assert scenario_fall_offset_s("fall_a_standard") == 5.0
    for playbook in DEMO_PLAYBOOKS:
        assert scenario_fall_offset_s(playbook) is None, playbook


# --------------------------------------------------------------------------- #
# The mock client follows the shared clock
# --------------------------------------------------------------------------- #


def _drain(queue: "asyncio.Queue") -> List:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


@pytest.mark.asyncio
async def test_mock_client_picks_up_a_runtime_scenario_switch() -> None:
    """The whole point of the panel: no restart between scenarios."""
    fake = _FakeClock()
    clock = ScenarioClock("resting", clock=fake)
    events: asyncio.Queue = asyncio.Queue()
    client = MockSensorClient(
        WristbandConfig(has_accelerometer=True), events, clock=clock, hr_hz=50.0
    )

    # Resting baseline: HR near 72, gravity-only accelerometer.
    resting = client._hr_at(clock.scenario, clock.elapsed_s)
    assert resting is not None and 68 <= resting <= 76

    clock.select("fall_c_syncope")
    fake.advance(9.0)     # 4 s past the scripted collapse at t=5 s
    assert client._hr_at(clock.scenario, clock.elapsed_s) is None, "pulse should be lost"

    clock.select("fall_b_seizure")
    fake.advance(7.0)
    seizure = client._hr_at(clock.scenario, clock.elapsed_s)
    assert seizure is not None and seizure >= 130


def test_mock_client_exposes_the_active_scenario_and_source() -> None:
    clock = ScenarioClock("demo_trip_fall")
    client = MockSensorClient(WristbandConfig(), asyncio.Queue(), clock=clock)
    assert client.scenario == "demo_trip_fall"
    assert client.sensor_source == SENSOR_SOURCE_MOCK
    clock.select("resting")
    assert client.scenario == "resting", "the property must not cache"


def test_fixed_scenario_constructor_still_works() -> None:
    """Back-compat: --mock-ble-scenario without a shared clock."""
    client = MockSensorClient(WristbandConfig(), asyncio.Queue(), scenario="fall_a_standard")
    assert client.scenario == "fall_a_standard"
    assert isinstance(client.clock, ScenarioClock)


def test_hardware_clients_are_not_controllable() -> None:
    tcp = TcpSensorServer(WristbandConfig(transport="tcp"), asyncio.Queue())
    assert tcp.sensor_source == SENSOR_SOURCE_HARDWARE
    controller = build_scenario_controller(tcp, enabled=True)
    assert controller.available is False
    tcp.stop()


def test_factory_threads_the_shared_clock_into_the_mock_client() -> None:
    clock = ScenarioClock("resting")
    client = build_sensor_client(
        WristbandConfig(), asyncio.Queue(), mock=True, clock=clock
    )
    assert client.clock is clock


# --------------------------------------------------------------------------- #
# Controller policy
# --------------------------------------------------------------------------- #


def test_catalogue_covers_every_mock_scenario() -> None:
    """A scenario the panel cannot reach is a scenario nobody tests."""
    assert {info.id for info in SCENARIO_CATALOGUE} == set(MOCK_SCENARIOS)


def test_scenarios_resolve_by_id_and_by_cli_alias() -> None:
    assert resolve_scenario("trip-fall").id == "demo_trip_fall"
    assert resolve_scenario("demo_trip_fall").alias == "trip-fall"
    assert resolve_scenario("  resting  ").id == "resting"
    with pytest.raises(ControlError) as exc:
        resolve_scenario("teleport")
    assert exc.value.code == "unknown_scenario"


def test_control_is_refused_while_disabled() -> None:
    """The default. A button that can page a caregiver stays off until asked."""
    controller = ScenarioController(ScenarioClock("resting"), enabled=False)
    assert controller.enabled is False
    with pytest.raises(ControlError) as exc:
        controller.select("trip-fall")
    assert exc.value.code == "disabled"


def test_control_is_refused_without_a_scenario_to_drive() -> None:
    controller = ScenarioController(None, enabled=True)
    with pytest.raises(ControlError) as exc:
        controller.select("trip-fall")
    assert exc.value.code == "unavailable"
    state = controller.state()
    assert state.available is False
    assert "mock-ble" in state.reason


def test_select_activates_and_reports_state() -> None:
    fake = _FakeClock()
    controller = ScenarioController(ScenarioClock("resting", clock=fake), enabled=True)
    state = controller.select("trip-fall")
    assert state.scenario == "demo_trip_fall"
    assert state.scenario_label == "Trip fall"
    assert state.elapsed_s == 0.0
    assert state.drill is True
    assert state.sensor_source == SENSOR_SOURCE_MOCK


def test_reset_returns_to_the_baseline_and_clears_the_drill_flag() -> None:
    controller = ScenarioController(ScenarioClock("resting"), enabled=True)
    controller.select("fall-b-seizure")
    assert controller.drill is True
    state = controller.reset()
    assert state.scenario == "resting"
    assert state.drill is False


def test_every_activation_reaches_the_caregiver_event_feed() -> None:
    """A drill has to be visible in the same timeline as real events."""
    status = CaregiverRuntimeStatus()
    controller = ScenarioController(
        ScenarioClock("resting"), enabled=True, runtime_status=status
    )
    controller.select("fall-c-syncope")
    events = status.events_payload()
    assert events, "activation pushed no event"
    latest = events[0]
    assert latest["severity"] == "warning"
    assert latest["category"] == "sensor"
    assert "Drill" in latest["title"]
    assert "scripted, not measured" in latest["detail"]


def test_returning_to_baseline_is_logged_as_info_not_a_warning() -> None:
    status = CaregiverRuntimeStatus()
    controller = ScenarioController(
        ScenarioClock("fall_a_standard"), enabled=True, runtime_status=status
    )
    controller.reset()
    assert status.events_payload()[0]["severity"] == "info"


def test_simulation_payload_is_always_populated() -> None:
    controller = ScenarioController(ScenarioClock("resting"), enabled=True)
    block = controller.simulation_payload(ppg_source="simulated")
    assert block == {
        "drill": False,
        "sensor_source": SENSOR_SOURCE_MOCK,
        "ppg_source": "simulated",
        "scenario": "resting",
    }
    controller.select("trip-fall")
    assert controller.simulation_payload(ppg_source="hardware")["drill"] is True


# --------------------------------------------------------------------------- #
# Payload blocks
# --------------------------------------------------------------------------- #


def test_monitoring_payload_carries_control_and_simulation_blocks() -> None:
    controller = ScenarioController(ScenarioClock("resting"), enabled=True)
    controller.select("tonic-clonic")
    payload = build_monitoring_payload(
        alerts=AlertsConfig(), snapshot=None, control=controller
    )
    assert payload["simulation"]["drill"] is True
    assert payload["simulation"]["scenario"] == "demo_tonic_clonic"
    assert payload["control"]["scenario"] == "demo_tonic_clonic"
    assert payload["control"]["enabled"] is True
    aliases = [s["alias"] for s in payload["control"]["scenarios"]]
    assert "trip-fall" in aliases


def test_monitoring_payload_defaults_to_a_non_drill_block() -> None:
    payload = build_monitoring_payload(alerts=AlertsConfig(), snapshot=None)
    assert payload["simulation"] == {
        "drill": False,
        "sensor_source": "hardware",
        "ppg_source": "hardware",
        "scenario": None,
    }
    assert payload["control"] is None


def test_alert_payload_marks_a_scripted_emergency_as_a_drill() -> None:
    """A scripted Tier-2 fires real webhooks; the recipient must be told."""
    from kineticpulse.fusion.engine import FusionSnapshot
    from kineticpulse.fusion.rules import AccelSignature, HrSignature, PoseSignature
    from kineticpulse.fusion.tiers import EmergencyTier, TierDecision

    snapshot = FusionSnapshot(
        pose=PoseSignature.PRONE,
        accel=AccelSignature.SOFT_COLLAPSE,
        hr=HrSignature.PULSE_LOST,
        decision=TierDecision(
            tier=EmergencyTier.TIER_2_CARDIAC,
            scenario="fall_c_syncope",
            reason="Pulse lost.",
        ),
        latest_hr_bpm=None,
        latest_accel_g=2.6,
        detector_class="fallen",
        detector_conf=0.93,
        timestamp_ms=1_700_000_000_000,
    )
    controller = ScenarioController(ScenarioClock("resting"), enabled=True)
    controller.select("fall-c-syncope")

    drill = build_payload(
        AlertsConfig(),
        snapshot,
        simulation=controller.simulation_payload(ppg_source="hardware"),
    ).as_json()
    assert drill["simulation"]["drill"] is True
    assert drill["simulation"]["scenario"] == "fall_c_syncope"

    real = build_payload(AlertsConfig(), snapshot).as_json()
    assert real["simulation"]["drill"] is False
    assert real["simulation"]["sensor_source"] == "hardware"


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #


def _publisher(controller: Optional[ScenarioController]) -> MonitoringPublisher:
    return MonitoringPublisher(
        host="127.0.0.1",
        port=0,
        alerts=AlertsConfig(),
        latest_snapshot=lambda: None,
        control=controller,
    )


class _CapturingWriter:
    """Minimal StreamWriter stand-in that records the response bytes."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None

    @property
    def status(self) -> int:
        return int(bytes(self.buffer).split(b" ", 2)[1])

    def json(self) -> dict:
        _, _, body = bytes(self.buffer).partition(b"\r\n\r\n")
        return json.loads(body.decode("utf-8"))


async def _post(publisher: MonitoringPublisher, path: str, body: dict) -> _CapturingWriter:
    writer = _CapturingWriter()
    await publisher._handle_post(writer, path, json.dumps(body).encode("utf-8"))
    return writer


@pytest.mark.asyncio
async def test_get_control_returns_the_panel_state() -> None:
    publisher = _publisher(ScenarioController(ScenarioClock("resting"), enabled=True))
    writer = _CapturingWriter()
    await publisher._handle_get(writer, "/control")
    assert writer.status == 200
    assert writer.json()["scenario"] == "resting"


@pytest.mark.asyncio
async def test_post_scenario_switches_and_returns_ok() -> None:
    controller = ScenarioController(ScenarioClock("resting"), enabled=True)
    writer = await _post(_publisher(controller), "/control/scenario", {"scenario": "trip-fall"})
    assert writer.status == 200
    payload = writer.json()
    assert payload["ok"] is True
    assert payload["scenario"] == "demo_trip_fall"
    assert controller.clock.scenario == "demo_trip_fall"


@pytest.mark.asyncio
async def test_post_scenario_is_forbidden_while_control_is_disabled() -> None:
    controller = ScenarioController(ScenarioClock("resting"), enabled=False)
    writer = await _post(_publisher(controller), "/control/scenario", {"scenario": "trip-fall"})
    assert writer.status == 403
    assert writer.json()["error"] == "disabled"
    assert controller.clock.scenario == "resting", "a refused request must change nothing"


@pytest.mark.asyncio
async def test_post_scenario_conflicts_against_real_hardware() -> None:
    writer = await _post(_publisher(ScenarioController(None, enabled=True)),
                         "/control/scenario", {"scenario": "trip-fall"})
    assert writer.status == 409
    assert writer.json()["error"] == "unavailable"


@pytest.mark.asyncio
async def test_post_rejects_an_unknown_scenario_and_a_bad_body() -> None:
    publisher = _publisher(ScenarioController(ScenarioClock("resting"), enabled=True))

    writer = await _post(publisher, "/control/scenario", {"scenario": "teleport"})
    assert writer.status == 400
    assert writer.json()["error"] == "unknown_scenario"

    writer = _CapturingWriter()
    await publisher._handle_post(writer, "/control/scenario", b"")
    assert writer.status == 400

    writer = _CapturingWriter()
    await publisher._handle_post(writer, "/control/scenario", b"{not json")
    assert writer.status == 400


@pytest.mark.asyncio
async def test_post_accepts_a_form_encoded_body_for_curl() -> None:
    publisher = _publisher(ScenarioController(ScenarioClock("resting"), enabled=True))
    writer = _CapturingWriter()
    await publisher._handle_post(writer, "/control/scenario", b"scenario=night-syncope")
    assert writer.status == 200
    assert writer.json()["scenario"] == "demo_night_syncope"


@pytest.mark.asyncio
async def test_restart_and_reset_actions() -> None:
    controller = ScenarioController(ScenarioClock("resting"), enabled=True)
    controller.select("fall-b-seizure")
    publisher = _publisher(controller)

    writer = await _post(publisher, "/control/restart", {})
    assert writer.status == 200
    assert writer.json()["scenario"] == "fall_b_seizure"

    writer = await _post(publisher, "/control/reset", {})
    assert writer.status == 200
    assert writer.json()["scenario"] == "resting"


@pytest.mark.asyncio
async def test_unknown_control_action_and_missing_controller_are_404() -> None:
    publisher = _publisher(ScenarioController(ScenarioClock("resting"), enabled=True))
    writer = await _post(publisher, "/control/explode", {})
    assert writer.status == 404

    bare = _publisher(None)
    writer = await _post(bare, "/control/scenario", {"scenario": "resting"})
    assert writer.status == 404
    writer = _CapturingWriter()
    await bare._handle_get(writer, "/control")
    assert writer.status == 404


def test_control_is_disabled_in_the_default_config() -> None:
    assert MonitoringConfig().control_enabled is False


# --------------------------------------------------------------------------- #
# Runtime health reaches the dashboard
# --------------------------------------------------------------------------- #


def test_runtime_payload_grades_the_cpu_fallback_as_critical() -> None:
    """A CPU fallback is a safety regression, not a performance note: the same
    pipeline runs ~16 FPS on the Orin GPU and ~0.65 FPS on the CPU."""
    from kineticpulse.runtime_status import (
        VISION_FPS_CRITICAL,
        VISION_FPS_WARN,
    )

    status = CaregiverRuntimeStatus()
    assert status.runtime_payload()["health"] == "unknown"

    status.set_accelerator("cuda", "Orin")
    status.set_vision_fps(16.4)
    status.set_backends("tensorrt", "tensorrt")
    ok = status.runtime_payload()
    assert ok["health"] == "ok"
    assert ok["accelerator"] == "cuda"
    assert ok["accelerator_device"] == "Orin"
    assert ok["vision_fps"] == 16.4
    assert ok["detector_backend"] == "tensorrt"

    status.set_vision_fps(VISION_FPS_WARN - 1)
    assert status.runtime_payload()["health"] == "degraded"

    status.set_vision_fps(VISION_FPS_CRITICAL - 1)
    assert status.runtime_payload()["health"] == "critical"

    # CPU is critical regardless of the rate it happens to be reporting.
    status.set_accelerator("cpu")
    status.set_vision_fps(60.0)
    assert status.runtime_payload()["health"] == "critical"


def test_monitoring_payload_carries_the_runtime_block() -> None:
    status = CaregiverRuntimeStatus()
    status.set_accelerator("cuda", "Orin")
    status.set_vision_fps(16.4)
    payload = build_monitoring_payload(
        alerts=AlertsConfig(), snapshot=None, runtime=status.runtime_payload()
    )
    assert payload["runtime"]["health"] == "ok"
    assert payload["runtime"]["vision_fps"] == 16.4


def test_monitoring_payload_runtime_defaults_to_unknown() -> None:
    """Never absent: the dashboard reads one field instead of inferring."""
    payload = build_monitoring_payload(alerts=AlertsConfig(), snapshot=None)
    assert payload["runtime"]["health"] == "unknown"
    assert payload["runtime"]["accelerator"] == "unknown"
    assert payload["runtime"]["vision_fps"] is None


def test_backend_is_named_from_the_weights_suffix() -> None:
    """The dashboard shows whether TensorRT engines are actually in use."""
    from kineticpulse.main import _backend_of

    assert _backend_of("runs/detect/x/weights/best.engine") == "tensorrt"
    assert _backend_of("yolov8s-pose.pt") == "pytorch"
    assert _backend_of("model.onnx") == "onnx"
    assert _backend_of("weird") == "weird"


def test_accelerator_check_records_into_runtime_status(monkeypatch) -> None:
    import sys
    import types

    from kineticpulse import main as kp_main

    status = CaregiverRuntimeStatus()
    monkeypatch.setitem(
        sys.modules, "torch",
        types.SimpleNamespace(
            __version__="2.9.1",
            version=types.SimpleNamespace(cuda="12.6"),
            cuda=types.SimpleNamespace(
                is_available=lambda: True, get_device_name=lambda _i: "Orin"
            ),
        ),
    )
    kp_main._log_accelerator_status(no_camera=False, runtime_status=status)
    assert status.accelerator == "cuda"
    assert status.accelerator_device == "Orin"

    monkeypatch.setitem(
        sys.modules, "torch",
        types.SimpleNamespace(
            __version__="2.12.0+cu130", __file__="/x/torch/__init__.py",
            version=types.SimpleNamespace(cuda="13.0"),
            cuda=types.SimpleNamespace(is_available=lambda: False),
        ),
    )
    kp_main._log_accelerator_status(no_camera=False, runtime_status=status)
    assert status.accelerator == "cpu"
    assert status.runtime_payload()["health"] == "critical"
