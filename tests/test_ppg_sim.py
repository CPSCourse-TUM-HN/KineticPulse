"""Tests for the synthetic PPG source used when the MAX30102 is dead.

Two things matter here and they pull in opposite directions:

1. the synthetic waveform must be *good enough* that the real
   :class:`PpgProcessor` recovers the intended rate, otherwise the mode is
   useless for bench work; and
2. the resulting heart rate must be *impossible to mistake* for a measured
   one, because ``latest_hr_bpm`` feeds a cardiac-arrest tier.

The labelling assertions below are the load-bearing ones. If one starts
failing, fix the labelling - do not relax the test.
"""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from kineticpulse.alerts.payload import build_payload
from kineticpulse.config import AlertsConfig, WristbandConfig
from kineticpulse.monitoring.http import build_monitoring_payload
from kineticpulse.sensors import build_sensor_client
from kineticpulse.sensors.parser import AccelSample, HrSample, PulseLost
from kineticpulse.sensors.ppg import PpgProcessor
from kineticpulse.sensors.ppg_sim import (
    PPG_SOURCE_HARDWARE,
    PPG_SOURCE_SIMULATED,
    PpgSimConfig,
    PpgWaveformSimulator,
    SimulatedPpgClient,
    SimulatedPpgSensorClient,
    beat_waveform,
    iter_samples,
    ppg_sim_config_from_wristband,
)


# --------------------------------------------------------------------------- #
# Waveform quality
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("target_bpm", [50, 60, 72, 85, 100, 120])
def test_processor_recovers_the_simulated_rate(target_bpm: int) -> None:
    """The real HR estimator must read the synthetic waveform correctly."""
    cfg = PpgSimConfig(resting_bpm=float(target_bpm), seed=7)
    processor = PpgProcessor(sample_rate_hz=cfg.sample_rate_hz)
    readings = [
        hr.bpm
        for sample in iter_samples(cfg, duration_s=30.0)
        if (hr := processor.push(sample)) is not None
    ]
    assert len(readings) > 20
    settled = readings[5:]     # skip the smoothing ramp-in
    assert all(abs(bpm - target_bpm) <= 4 for bpm in settled), settled


def test_waveform_has_a_single_dominant_peak_per_beat() -> None:
    """The dicrotic notch must stay under the processor's 0.35 * peak gate.

    Otherwise every beat is counted twice and the reported rate doubles.
    """
    systolic = max(beat_waveform(p / 1000.0) for p in range(0, 300))
    dicrotic = max(beat_waveform(p / 1000.0) for p in range(300, 800))
    assert dicrotic < 0.35 * systolic


def test_simulator_produces_beat_to_beat_variability() -> None:
    """A metronome pulse would be a giveaway that the data is fake, and it
    would also hide the smoothing behaviour we want to exercise."""
    sim = PpgWaveformSimulator(PpgSimConfig(seed=3))
    rates = []
    last = sim.instantaneous_bpm
    for i in range(3000):          # 30 s at 100 Hz
        sim.next_sample(i * 10)
        if sim.instantaneous_bpm != last:
            last = sim.instantaneous_bpm
            rates.append(last)
    assert len(rates) > 20
    assert len(set(rates)) > 10
    assert all(40.0 < r < 180.0 for r in rates)


def test_simulated_counts_stay_in_the_max30102_range() -> None:
    cfg = PpgSimConfig(seed=1)
    for sample in iter_samples(cfg, duration_s=5.0):
        assert 0 <= sample.ir <= 262_143
        assert 0 <= sample.red <= 262_143
        # Red DC below IR DC, as on real hardware.
        assert sample.red < sample.ir


def test_simulator_rejects_a_baseline_outside_its_clamps() -> None:
    with pytest.raises(ValueError):
        PpgWaveformSimulator(PpgSimConfig(resting_bpm=300.0))
    with pytest.raises(ValueError):
        PpgWaveformSimulator(PpgSimConfig(sample_rate_hz=0))


def test_config_maps_from_the_wristband_block() -> None:
    wb = WristbandConfig(
        ppg_sample_rate_hz=50,
        ppg_sim_resting_bpm=64.0,
        ppg_sim_hrv_sd_ms=18.0,
        ppg_sim_seed=11,
    )
    cfg = ppg_sim_config_from_wristband(wb)
    assert cfg.sample_rate_hz == 50
    assert cfg.resting_bpm == 64.0
    assert cfg.hrv_sd_ms == 18.0
    assert cfg.seed == 11


# --------------------------------------------------------------------------- #
# Client behaviour
# --------------------------------------------------------------------------- #


def _drive(client: SimulatedPpgClient, duration_s: float, step_s: float, t0_ms: int = 10_000):
    """Run ``client.emit_due`` off a synthetic clock instead of sleeping."""
    out = []
    t_ms = t0_ms
    client.emit_due(t_ms)          # first call only anchors the cursor
    while t_ms < t0_ms + duration_s * 1000:
        t_ms += int(step_s * 1000)
        out.extend(client.emit_due(t_ms))
    return out


def test_client_emits_heart_rate_samples_and_no_pulse_loss() -> None:
    client = SimulatedPpgClient(
        asyncio.Queue(),
        cfg=PpgSimConfig(resting_bpm=72.0, seed=5),
    )
    events = _drive(client, duration_s=30.0, step_s=0.25)
    assert events, "simulated client produced no events"
    assert all(isinstance(ev, HrSample) for ev in events)
    assert not any(isinstance(ev, PulseLost) for ev in events)
    settled = [ev.bpm for ev in events[5:]]
    assert all(abs(bpm - 72) <= 4 for bpm in settled), settled


def test_client_rate_is_independent_of_how_often_it_is_polled() -> None:
    """BPM must come out right whether bursts are frequent or laggy - a
    truncating cursor would make the synthetic heart run slow."""
    for step_s in (0.05, 0.25, 0.4, 0.9):
        client = SimulatedPpgClient(
            asyncio.Queue(), cfg=PpgSimConfig(resting_bpm=72.0, seed=5)
        )
        events = _drive(client, duration_s=30.0, step_s=step_s)
        settled = [ev.bpm for ev in events[5:]]
        assert settled, step_s
        mean = sum(settled) / len(settled)
        assert abs(mean - 72) <= 2, (step_s, mean)


def test_client_timestamps_track_the_wall_clock() -> None:
    """Fusion ages the latest HR sample against wall time; a lagging
    timestamp would read as a fading pulse and trip pulse-loss."""
    client = SimulatedPpgClient(asyncio.Queue(), cfg=PpgSimConfig(seed=2))
    t0 = 500_000
    events = _drive(client, duration_s=30.0, step_s=0.25, t0_ms=t0)
    assert events
    assert abs(events[-1].timestamp_ms - (t0 + 30_000)) < 500


def test_client_resynchronises_after_a_long_stall() -> None:
    """A suspended process must not dump minutes of backdated samples."""
    client = SimulatedPpgClient(asyncio.Queue(), cfg=PpgSimConfig(seed=2))
    client.emit_due(1_000)
    client.emit_due(1_000 + 300_000)          # 5 minutes later
    # The cursor is back within the catch-up window of the current clock.
    assert client._cursor_ms >= 1_000 + 300_000 - int(
        client.MAX_CATCHUP_S * 1000
    ) - 50


@pytest.mark.asyncio
async def test_client_run_loop_publishes_to_the_queue() -> None:
    events: asyncio.Queue = asyncio.Queue()
    client = SimulatedPpgClient(
        events, cfg=PpgSimConfig(resting_bpm=72.0, seed=5), burst_period_s=0.02
    )
    task = asyncio.create_task(client.run())
    try:
        await asyncio.sleep(3.2)      # past the 2 s warm-up plus an output tick
    finally:
        client.stop()
        await asyncio.wait_for(task, timeout=2.0)

    drained = []
    while not events.empty():
        drained.append(events.get_nowait())
    assert drained, "run() published nothing"
    assert all(isinstance(ev, HrSample) for ev in drained)


def test_client_declares_itself_as_simulated() -> None:
    client = SimulatedPpgClient(asyncio.Queue())
    assert client.ppg_source == PPG_SOURCE_SIMULATED
    assert SimulatedPpgSensorClient.ppg_source == PPG_SOURCE_SIMULATED


@pytest.mark.asyncio
async def test_wrapper_forwards_motion_but_drops_hardware_pulse() -> None:
    """A half-working sensor must not interleave real and synthetic BPM."""
    hardware_events: asyncio.Queue = asyncio.Queue()
    events: asyncio.Queue = asyncio.Queue()

    class _StubHardware:
        connected = True

        async def run(self) -> None:
            await asyncio.sleep(3600)

        def stop(self) -> None:
            pass

    wrapper = SimulatedPpgSensorClient(
        _StubHardware(),
        hardware_events,
        events,
        SimulatedPpgClient(events, burst_period_s=3600.0),
    )
    forwarder = asyncio.create_task(wrapper._forward_hardware_events())
    try:
        accel = AccelSample(ax=0.1, ay=0.0, az=1.0, timestamp_ms=1)
        hardware_events.put_nowait(accel)
        hardware_events.put_nowait(HrSample(bpm=200, timestamp_ms=2))
        hardware_events.put_nowait(PulseLost(duration_s=5.0, timestamp_ms=3))
        await asyncio.sleep(0.1)
    finally:
        wrapper.stop()
        forwarder.cancel()

    forwarded = []
    while not events.empty():
        forwarded.append(events.get_nowait())
    assert forwarded == [accel]


def test_factory_wraps_the_transport_when_ppg_source_is_simulated() -> None:
    cfg = WristbandConfig(transport="tcp", ppg_source="simulated", has_ppg_raw=True)
    client = build_sensor_client(cfg, asyncio.Queue())
    assert isinstance(client, SimulatedPpgSensorClient)
    assert client.ppg_source == PPG_SOURCE_SIMULATED
    # The hardware leg is told there is no raw PPG so a stray burst from a
    # half-alive sensor is ignored rather than fighting the simulator.
    assert client.hardware.cfg.has_ppg_raw is False
    client.stop()


def test_factory_leaves_hardware_mode_untouched() -> None:
    cfg = WristbandConfig(transport="tcp", ppg_source="hardware")
    client = build_sensor_client(cfg, asyncio.Queue())
    assert not isinstance(client, SimulatedPpgSensorClient)
    assert getattr(client, "ppg_source", PPG_SOURCE_HARDWARE) == PPG_SOURCE_HARDWARE
    client.stop()


def test_factory_rejects_an_unknown_ppg_source() -> None:
    cfg = WristbandConfig(transport="tcp", ppg_source="pretend")
    with pytest.raises(ValueError, match="ppg_source"):
        build_sensor_client(cfg, asyncio.Queue())


# --------------------------------------------------------------------------- #
# Labelling - the reason this mode is allowed to exist at all
# --------------------------------------------------------------------------- #


class _SimulatedSensors:
    connected = True
    ppg_source = PPG_SOURCE_SIMULATED


class _HardwareSensors:
    connected = True
    ppg_source = PPG_SOURCE_HARDWARE


def test_monitoring_payload_labels_a_simulated_pulse() -> None:
    payload = build_monitoring_payload(
        alerts=AlertsConfig(), snapshot=None, sensors=_SimulatedSensors()
    )
    assert payload["sensor"]["ppg_source"] == PPG_SOURCE_SIMULATED
    assert payload["snapshot"]["hr_simulated"] is True


def test_monitoring_payload_defaults_to_hardware() -> None:
    for sensors in (_HardwareSensors(), None, object()):
        payload = build_monitoring_payload(
            alerts=AlertsConfig(), snapshot=None, sensors=sensors
        )
        assert payload["sensor"]["ppg_source"] == PPG_SOURCE_HARDWARE
        assert payload["snapshot"]["hr_simulated"] is False


def test_alert_payload_labels_a_simulated_pulse(monkeypatch) -> None:
    from kineticpulse.fusion.engine import FusionSnapshot
    from kineticpulse.fusion.rules import AccelSignature, HrSignature, PoseSignature
    from kineticpulse.fusion.tiers import EmergencyTier, TierDecision

    snapshot = FusionSnapshot(
        pose=PoseSignature.PRONE,
        accel=AccelSignature.IMPACT_ONLY,
        hr=HrSignature.PANIC_SPIKE,
        decision=TierDecision(
            tier=EmergencyTier.TIER_1_VERIFY,
            scenario="fall_a_standard",
            reason="Impact then stillness.",
        ),
        latest_hr_bpm=118,
        latest_accel_g=4.1,
        detector_class="fallen",
        detector_conf=0.9,
        action_class="fall",
        action_conf=0.8,
        timestamp_ms=1_700_000_000_000,
    )

    simulated = build_payload(
        AlertsConfig(), snapshot, ppg_source=PPG_SOURCE_SIMULATED
    ).as_json()
    assert simulated["vitals"]["heart_rate_source"] == PPG_SOURCE_SIMULATED
    assert simulated["vitals"]["heart_rate_simulated"] is True

    real = build_payload(AlertsConfig(), snapshot).as_json()
    assert real["vitals"]["heart_rate_source"] == PPG_SOURCE_HARDWARE
    assert real["vitals"]["heart_rate_simulated"] is False
