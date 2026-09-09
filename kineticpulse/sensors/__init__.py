"""Wearable sensor inputs (TCP wristband, with BLE as fallback)."""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Optional, Protocol

from kineticpulse.config import WristbandConfig
from kineticpulse.sensors.ble import BleClient
from kineticpulse.sensors.mock import MockBleClient, MockSensorClient, ScenarioClock
from kineticpulse.sensors.parser import (
    AccelSample,
    HrSample,
    PulseLost,
    SensorEvent,
    parse_accel_packet,
    parse_hr_packet,
)
from kineticpulse.sensors.ppg import PpgProcessor, PpgSample, parse_ppg_packet
from kineticpulse.sensors.ppg_sim import (
    PPG_SOURCE_HARDWARE,
    PPG_SOURCE_SIMULATED,
    PPG_SOURCES,
    SIMULATED_HR_NOTICE,
    PpgSimConfig,
    PpgWaveformSimulator,
    SimulatedPpgClient,
    SimulatedPpgSensorClient,
    ppg_sim_config_from_wristband,
)
from kineticpulse.sensors.tcp import TcpSensorServer
from kineticpulse.utils.logging import get_logger

log = get_logger(__name__)


class _SensorClient(Protocol):
    async def run(self) -> None: ...
    def stop(self) -> None: ...


def build_sensor_client(
    cfg: WristbandConfig,
    events: "asyncio.Queue[SensorEvent]",
    *,
    mock: bool = False,
    scenario: str = "resting",
    clock: Optional[ScenarioClock] = None,
) -> _SensorClient:
    """Pick the right sensor client based on ``cfg.transport``.

    The ``mock`` flag short-circuits everything: it returns a
    :class:`MockSensorClient` regardless of transport so the rest of
    the pipeline can be exercised end-to-end without any hardware.

    ``cfg.ppg_source == "simulated"`` is the narrower degraded mode for a
    dead MAX30102: the transport client below still supplies motion, but
    the heart rate is synthesised by
    :class:`~kineticpulse.sensors.ppg_sim.SimulatedPpgClient` and labelled
    as simulated everywhere it surfaces.
    """
    if mock:
        return MockSensorClient(cfg, events, scenario=scenario, clock=clock)

    ppg_source = (cfg.ppg_source or PPG_SOURCE_HARDWARE).strip().lower()
    if ppg_source not in PPG_SOURCES:
        raise ValueError(
            f"Unknown wristband.ppg_source: {cfg.ppg_source!r}. "
            f"Expected one of: {', '.join(repr(s) for s in PPG_SOURCES)}."
        )

    if ppg_source == PPG_SOURCE_SIMULATED:
        # Motion keeps coming from the real transport; only the pulse is
        # synthesised. The hardware client is told there is no raw PPG so it
        # drops any burst the wristband still emits, and the wrapper filters
        # stray hr / pulse_lost lines - see kineticpulse.sensors.ppg_sim.
        hardware_cfg = dataclasses.replace(cfg, has_ppg_raw=False)
        hardware_events: "asyncio.Queue[SensorEvent]" = asyncio.Queue(maxsize=1024)
        hardware = _build_transport_client(
            hardware_cfg, hardware_events, scenario=scenario, clock=clock
        )
        return SimulatedPpgSensorClient(
            hardware,
            hardware_events,
            events,
            SimulatedPpgClient(events, cfg=ppg_sim_config_from_wristband(cfg)),
        )

    return _build_transport_client(cfg, events, scenario=scenario, clock=clock)


def _build_transport_client(
    cfg: WristbandConfig,
    events: "asyncio.Queue[SensorEvent]",
    *,
    scenario: str = "resting",
    clock: Optional[ScenarioClock] = None,
) -> _SensorClient:
    """Resolve ``cfg.transport`` to a concrete hardware client."""
    transport = (cfg.transport or "tcp").strip().lower()
    if transport == "tcp":
        return TcpSensorServer(cfg, events)
    if transport == "ble":
        if not cfg.mac:
            log.warning(
                "wristband.transport=ble but wristband.mac is not set; "
                "falling back to the mock sensor client."
            )
            return MockSensorClient(cfg, events, scenario=scenario, clock=clock)
        return BleClient(cfg, events)
    raise ValueError(
        f"Unknown wristband.transport: {cfg.transport!r}. "
        f"Expected one of: 'tcp', 'ble'."
    )


# Back-compat shim: existing code / scripts that imported build_ble_client
# keep working. New code should call build_sensor_client.
def build_ble_client(
    cfg: WristbandConfig,
    events: "asyncio.Queue[SensorEvent]",
    *,
    mock: bool = False,
    scenario: str = "resting",
) -> _SensorClient:
    """Deprecated alias for :func:`build_sensor_client`."""
    return build_sensor_client(cfg, events, mock=mock, scenario=scenario)


__all__ = [
    "AccelSample",
    "HrSample",
    "PulseLost",
    "SensorEvent",
    "parse_accel_packet",
    "parse_hr_packet",
    "parse_ppg_packet",
    "PpgSample",
    "PpgProcessor",
    "PPG_SOURCE_HARDWARE",
    "PPG_SOURCE_SIMULATED",
    "PPG_SOURCES",
    "SIMULATED_HR_NOTICE",
    "PpgSimConfig",
    "PpgWaveformSimulator",
    "SimulatedPpgClient",
    "SimulatedPpgSensorClient",
    "ppg_sim_config_from_wristband",
    "BleClient",
    "TcpSensorServer",
    "MockSensorClient",
    "MockBleClient",          # back-compat alias
    "ScenarioClock",
    "build_sensor_client",
    "build_ble_client",       # back-compat alias
]
