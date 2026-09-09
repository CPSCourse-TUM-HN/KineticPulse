"""Synthetic PPG source for running without a working MAX30102.

Why this exists
---------------
The MAX30102 sits on the ESP32 (``src/main.cpp`` gates every PPG read
behind ``ppgOk = ppg.begin(...)``), so when the sensor dies the firmware
simply stops emitting ``hr`` / ``ppg`` lines. The Jetson then sees no
pulse sample at all, :func:`kineticpulse.fusion.rules.aggregate_hr`
grows ``pulse_lost_s`` without bound, and the fusion engine parks on the
``PULSE_LOST`` cardiac-arrest indicator. That makes the whole rig
useless for working on vision, fusion tiers, or the dashboard while a
replacement sensor is in the post.

This module fills the gap: it synthesises a plausible resting PPG
waveform, pushes it through the *real*
:class:`~kineticpulse.sensors.ppg.PpgProcessor`, and emits the resulting
:class:`~kineticpulse.sensors.parser.HrSample` events. Motion still comes
from the hardware transport, so only the heart-rate leg is substituted.

Why it is loudly labelled
-------------------------
``latest_hr_bpm`` drives a *cardiac-arrest* decision tier. A synthetic
"normal" BPM that is indistinguishable from a real one would suppress
:attr:`~kineticpulse.fusion.rules.HrSignature.PULSE_LOST` forever - the
system would look healthy while being structurally incapable of
reporting a stopped heart. So every path this data reaches carries the
source with it:

* a startup ``WARNING`` plus a periodic reminder in the logs,
* ``sensor.ppg_source`` / ``snapshot.hr_simulated`` in the monitoring
  payload (and a badge in the dashboard),
* ``vitals.heart_rate_source`` in any dispatched alert payload,
* a warning-severity entry in the caregiver event feed.

Do not remove those labels, and do not ship a build with
``wristband.ppg_source: simulated`` to anyone who would read the heart
rate as real.
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass
from typing import Iterator, List, Optional

from kineticpulse.config import WristbandConfig
from kineticpulse.sensors.parser import HrSample, PulseLost, SensorEvent
from kineticpulse.sensors.ppg import PpgProcessor, PpgSample
from kineticpulse.utils.logging import get_logger
from kineticpulse.utils.timing import now_ms

log = get_logger(__name__)

PPG_SOURCE_HARDWARE = "hardware"
PPG_SOURCE_SIMULATED = "simulated"
PPG_SOURCES = (PPG_SOURCE_HARDWARE, PPG_SOURCE_SIMULATED)

#: Single wording for the "this pulse is not real" notice, reused by the
#: logs, the caregiver event feed, the monitoring payload and the docs so
#: the warning cannot drift out of sync between surfaces.
SIMULATED_HR_NOTICE = (
    "Heart rate is SIMULATED (wristband.ppg_source=simulated): the MAX30102 is "
    "not supplying data, so pulse-loss and cardiac tiers cannot fire. "
    "Not valid for monitoring a person."
)


def beat_waveform(phase: float) -> float:
    """Normalised single-beat PPG shape for ``phase`` in ``[0, 1)``.

    Two Gaussians: the sharp systolic upstroke plus a smaller, broader
    dicrotic wave. The dicrotic amplitude is kept below
    :class:`PpgProcessor`'s ``0.35 * peak`` detection threshold so the
    processor counts one beat per cycle rather than two.
    """
    systolic = math.exp(-(((phase - 0.16) / 0.070) ** 2))
    dicrotic = 0.22 * math.exp(-(((phase - 0.45) / 0.100) ** 2))
    return systolic + dicrotic


@dataclass
class PpgSimConfig:
    """Shape of the synthetic waveform.

    Defaults describe a healthy adult at rest: ~72 BPM, SDNN in the
    normal 20-30 ms band, respiratory sinus arrhythmia at 15 breaths per
    minute, and a perfusion index around 2 % on the IR channel.
    """

    sample_rate_hz: int = 100
    resting_bpm: float = 72.0
    hrv_sd_ms: float = 22.0            # beat-to-beat RR jitter (resting SDNN)
    respiration_per_min: float = 15.0  # RSA + baseline wander frequency
    rsa_depth: float = 0.03            # RR modulation depth from breathing
    baseline_wander: float = 0.004     # DC drift from breathing, fraction of DC
    dc_ir: int = 100_000               # MAX30102 IR counts at rest
    dc_red_ratio: float = 0.62         # red DC relative to IR DC
    ac_fraction: float = 0.02          # IR perfusion index
    spo2_ratio: float = 0.50           # R = (AC/DC)red / (AC/DC)ir; ~98 % SpO2
    noise_fraction: float = 0.0015     # sensor noise, fraction of DC
    min_bpm: float = 40.0              # clamp on the jittered instantaneous rate
    max_bpm: float = 180.0
    seed: int = 0


class PpgWaveformSimulator:
    """Stateful generator of :class:`PpgSample` values.

    Advances one sample per :meth:`next_sample` call, re-drawing the RR
    interval at each beat boundary so the output has real beat-to-beat
    variability instead of a metronome pulse. Deterministic for a given
    ``seed``, which keeps the tests reproducible.
    """

    def __init__(self, cfg: Optional[PpgSimConfig] = None) -> None:
        self.cfg = cfg or PpgSimConfig()
        if self.cfg.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if not (self.cfg.min_bpm < self.cfg.resting_bpm < self.cfg.max_bpm):
            raise ValueError("resting_bpm must sit between min_bpm and max_bpm")
        self._rng = random.Random(self.cfg.seed)
        self._t_s = 0.0
        self._phase = 0.0
        self._rr_s = 60.0 / self.cfg.resting_bpm
        self._draw_rr()

    @property
    def elapsed_s(self) -> float:
        return self._t_s

    @property
    def instantaneous_bpm(self) -> float:
        """Rate implied by the RR interval of the beat in progress."""
        return 60.0 / self._rr_s

    def next_sample(self, timestamp_ms: int) -> PpgSample:
        """Produce the next sample and advance time by ``1 / fs``."""
        cfg = self.cfg
        resp_phase = 2 * math.pi * (cfg.respiration_per_min / 60.0) * self._t_s
        wander = 1.0 + cfg.baseline_wander * math.sin(resp_phase)
        pulse = beat_waveform(self._phase)

        dc_ir = cfg.dc_ir * wander
        ac_ir = dc_ir * cfg.ac_fraction * pulse
        dc_red = dc_ir * cfg.dc_red_ratio
        ac_red = dc_red * cfg.ac_fraction * cfg.spo2_ratio * pulse

        noise_ir = self._rng.gauss(0.0, cfg.dc_ir * cfg.noise_fraction)
        noise_red = self._rng.gauss(0.0, cfg.dc_ir * cfg.noise_fraction)

        # The MAX30102 FIFO is unsigned 18-bit; keep the synthetic counts in
        # the same range so anything downstream sees hardware-shaped values.
        ir = _clamp_counts(dc_ir + ac_ir + noise_ir)
        red = _clamp_counts(dc_red + ac_red + noise_red)

        step = 1.0 / cfg.sample_rate_hz
        self._t_s += step
        self._phase += step / self._rr_s
        while self._phase >= 1.0:
            self._phase -= 1.0
            self._draw_rr()

        return PpgSample(ir=ir, red=red, timestamp_ms=timestamp_ms)

    def burst(self, count: int, end_timestamp_ms: int) -> List[PpgSample]:
        """``count`` samples whose *last* entry lands on ``end_timestamp_ms``.

        Mirrors the timestamping of :func:`kineticpulse.sensors.ppg.parse_ppg_packet`
        and of the TCP server's ``ppg`` burst handling.
        """
        period_ms = 1000.0 / self.cfg.sample_rate_hz
        out: List[PpgSample] = []
        for i in range(max(0, count)):
            ts = int(end_timestamp_ms - (count - 1 - i) * period_ms)
            out.append(self.next_sample(ts))
        return out

    def _draw_rr(self) -> None:
        cfg = self.cfg
        resp_phase = 2 * math.pi * (cfg.respiration_per_min / 60.0) * self._t_s
        # RSA: RR shortens on inhale, lengthens on exhale.
        rsa = 1.0 - cfg.rsa_depth * math.sin(resp_phase)
        rr = (60.0 / cfg.resting_bpm) * rsa + self._rng.gauss(0.0, cfg.hrv_sd_ms / 1000.0)
        self._rr_s = min(max(rr, 60.0 / cfg.max_bpm), 60.0 / cfg.min_bpm)


def _clamp_counts(value: float) -> int:
    return int(min(max(value, 0.0), 262_143.0))


def iter_samples(
    cfg: PpgSimConfig,
    duration_s: float,
    start_timestamp_ms: int = 0,
) -> Iterator[PpgSample]:
    """Offline helper: synthesise ``duration_s`` of PPG for tests / replay."""
    sim = PpgWaveformSimulator(cfg)
    total = int(duration_s * cfg.sample_rate_hz)
    period_ms = 1000.0 / cfg.sample_rate_hz
    for i in range(total):
        yield sim.next_sample(int(start_timestamp_ms + i * period_ms))


class SimulatedPpgClient:
    """Emits :class:`HrSample` events from the synthetic waveform.

    Runs the samples through a real :class:`PpgProcessor` rather than
    publishing a BPM directly, so the on-Jetson peak detection, smoothing
    and rate limiting are all exercised exactly as they are with a live
    sensor. Anything the processor rejects stays rejected.
    """

    #: How often the "this pulse is synthetic" warning is repeated.
    WARN_PERIOD_S = 60.0

    #: Longest catch-up burst after the loop falls behind, in seconds of
    #: samples. A longer stall is treated as a gap (like a dropped wristband
    #: connection) and the cursor resynchronises instead of dumping thousands
    #: of backdated samples into the processor.
    MAX_CATCHUP_S = 2.0

    ppg_source = PPG_SOURCE_SIMULATED

    def __init__(
        self,
        events: "asyncio.Queue[SensorEvent]",
        *,
        cfg: Optional[PpgSimConfig] = None,
        burst_period_s: float = 0.25,
    ) -> None:
        self.cfg = cfg or PpgSimConfig()
        self.events = events
        self.burst_period_s = float(burst_period_s)
        self._sim = PpgWaveformSimulator(self.cfg)
        self._processor = PpgProcessor(sample_rate_hz=self.cfg.sample_rate_hz)
        self._stop = asyncio.Event()
        self._period_ms = 1000.0 / self.cfg.sample_rate_hz
        self._cursor_ms: Optional[int] = None
        self._next_warn_s = self.WARN_PERIOD_S

    @property
    def connected(self) -> bool:
        return not self._stop.is_set()

    async def run(self) -> None:
        log.warning("%s", SIMULATED_HR_NOTICE)
        log.warning(
            "SimulatedPpgClient: %.0f BPM baseline, %d Hz, SDNN %.0f ms, bursts every %.0f ms",
            self.cfg.resting_bpm, self.cfg.sample_rate_hz,
            self.cfg.hrv_sd_ms, self.burst_period_s * 1000,
        )
        while not self._stop.is_set():
            for ev in self.emit_due(now_ms()):
                self._submit(ev)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.burst_period_s)
            except asyncio.TimeoutError:
                continue

    def emit_due(self, now_ms_: int) -> List[SensorEvent]:
        """Generate every sample owed at ``now_ms_`` and return the HR events.

        The sample count is derived from *elapsed wall-clock time*, not from
        how often this is called. Two things depend on that:

        * :class:`PpgProcessor` computes BPM from sample timestamps, so a
          burst that is too small or too large for the time it covers would
          report a rate that is not the simulated one; and
        * :func:`kineticpulse.fusion.rules.aggregate_hr` ages the latest
          sample against the wall clock, so timestamps that lag real time
          would look like a fading pulse.

        The cursor advances by whole sample periods and keeps the remainder,
        so repeated truncation cannot make the synthetic heart run slow.
        Exposed (rather than inlined into :meth:`run`) so tests can drive it
        from a synthetic clock instead of sleeping.
        """
        if self._cursor_ms is None:
            self._cursor_ms = now_ms_
            return []

        due = int((now_ms_ - self._cursor_ms) / self._period_ms)
        if due <= 0:
            return []

        max_due = max(1, int(self.MAX_CATCHUP_S * self.cfg.sample_rate_hz))
        if due > max_due:
            log.warning(
                "SimulatedPpgClient: fell %.1fs behind; resynchronising "
                "(dropping %d samples).",
                (now_ms_ - self._cursor_ms) / 1000.0, due - max_due,
            )
            due = max_due
            self._cursor_ms = now_ms_ - int(due * self._period_ms)

        end_ms = self._cursor_ms + int(due * self._period_ms)
        out: List[SensorEvent] = []
        for sample in self._sim.burst(due, end_ms):
            hr = self._processor.push(sample)
            if hr is not None:
                out.append(hr)
        self._cursor_ms = end_ms

        if self._sim.elapsed_s >= self._next_warn_s:
            log.warning("%s", SIMULATED_HR_NOTICE)
            self._next_warn_s += self.WARN_PERIOD_S
        return out

    def stop(self) -> None:
        self._stop.set()

    def _submit(self, ev: SensorEvent) -> None:
        try:
            self.events.put_nowait(ev)
        except asyncio.QueueFull:
            try:
                _ = self.events.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.events.put_nowait(ev)


class SimulatedPpgSensorClient:
    """Hardware transport for motion, :class:`SimulatedPpgClient` for pulse.

    The wrapped hardware client writes into a private queue; everything
    except heart-rate events is forwarded to the real events queue. Any
    ``hr`` / ``pulse_lost`` line the wristband still manages to send is
    dropped, so the two sources cannot interleave and produce a BPM that
    flickers between real and synthetic.
    """

    ppg_source = PPG_SOURCE_SIMULATED

    def __init__(
        self,
        hardware: object,
        hardware_events: "asyncio.Queue[SensorEvent]",
        events: "asyncio.Queue[SensorEvent]",
        ppg: SimulatedPpgClient,
    ) -> None:
        self.hardware = hardware
        self.hardware_events = hardware_events
        self.events = events
        self.ppg = ppg
        self._stop = asyncio.Event()

    @property
    def connected(self) -> bool:
        """Link state of the *hardware* transport (motion is still real)."""
        return bool(getattr(self.hardware, "connected", False))

    @property
    def sensor_source(self) -> str:
        """Motion is real, so this is not a fully synthetic run.

        The synthetic pulse is reported separately via
        :attr:`ppg_source`; conflating the two would either hide the real
        accelerometer or overstate the heart rate.
        """
        return str(getattr(self.hardware, "sensor_source", "hardware"))

    async def run(self) -> None:
        await asyncio.gather(
            self.hardware.run(),      # type: ignore[attr-defined]
            self.ppg.run(),
            self._forward_hardware_events(),
        )

    def stop(self) -> None:
        self._stop.set()
        self.ppg.stop()
        stop = getattr(self.hardware, "stop", None)
        if callable(stop):
            stop()

    async def _forward_hardware_events(self) -> None:
        while not self._stop.is_set():
            try:
                ev = await asyncio.wait_for(self.hardware_events.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            if isinstance(ev, (HrSample, PulseLost)):
                # Pulse comes from the simulator in this mode.
                continue
            try:
                self.events.put_nowait(ev)
            except asyncio.QueueFull:
                try:
                    _ = self.events.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                self.events.put_nowait(ev)


def ppg_sim_config_from_wristband(cfg: WristbandConfig) -> PpgSimConfig:
    """Build a :class:`PpgSimConfig` from the wristband config block."""
    return PpgSimConfig(
        sample_rate_hz=cfg.ppg_sample_rate_hz,
        resting_bpm=cfg.ppg_sim_resting_bpm,
        hrv_sd_ms=cfg.ppg_sim_hrv_sd_ms,
        seed=cfg.ppg_sim_seed,
    )


__all__ = [
    "PPG_SOURCE_HARDWARE",
    "PPG_SOURCE_SIMULATED",
    "PPG_SOURCES",
    "SIMULATED_HR_NOTICE",
    "PpgSimConfig",
    "PpgWaveformSimulator",
    "SimulatedPpgClient",
    "SimulatedPpgSensorClient",
    "beat_waveform",
    "iter_samples",
    "ppg_sim_config_from_wristband",
]
