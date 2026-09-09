"""Runtime scenario control for the bench control panel.

What this is
------------
The mock sensor client can already replay any of the PRD scenarios, but
until now the choice was fixed at process start by
``--mock-ble-scenario`` / ``--demo``. Testing four scenarios meant four
restarts, each one reloading the pose models. This module lets the
scenario be switched while the pipeline runs, so the dashboard can offer
a panel of buttons: press "Trip fall" and the synthetic telemetry *and*
the scripted posture replay that playbook from t=0 together.

:class:`ScenarioController` is the policy layer around
:class:`kineticpulse.sensors.mock.ScenarioClock`: it decides whether
control is permitted at all, validates the requested scenario, and
records every activation. The clock itself is the shared timing
primitive read by the sensor loops and the scripted-posture loop.

Why it is off by default
------------------------
Activating a scenario injects telemetry that the fusion engine treats as
real. A Tier-2 scenario therefore fires actual webhooks, opens a WebRTC
session and plays the voice prompt - the full emergency path, aimed at
whatever endpoint ``alerts.webhooks`` points to. A button that can page a
caregiver is not something to leave reachable by default, so:

* ``monitoring.control_enabled`` defaults to ``False`` and the endpoints
  return ``403`` until it is turned on;
* control only works when the runtime is actually driving synthetic
  sensors (``--mock-ble``). Against real hardware the panel reports that
  it is unavailable instead of pretending a button did something;
* every activation is logged at ``WARNING`` and pushed to the caregiver
  event feed, so a drill is visible in the same timeline as real events;
* while a scenario is active the monitoring and alert payloads carry
  ``simulation.drill = true``, so a scripted fall cannot be mistaken for
  a measured one downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from kineticpulse.runtime_status import CaregiverRuntimeStatus
from kineticpulse.sensors.mock import (
    DEMO_CLI,
    DEMO_NARRATION,
    DEMO_PLAYBOOKS,
    MOCK_SCENARIOS,
    ScenarioClock,
)
from kineticpulse.utils.logging import get_logger

log = get_logger(__name__)

SENSOR_SOURCE_HARDWARE = "hardware"
SENSOR_SOURCE_MOCK = "mock"


@dataclass(frozen=True)
class ScenarioInfo:
    """One button on the control panel."""

    id: str                 # canonical mock id, e.g. "demo_trip_fall"
    alias: str              # CLI / URL-friendly name, e.g. "trip-fall"
    label: str              # button text
    group: str              # "baseline" | "scenario" | "playbook"
    expected_tier: str      # the tier this scenario is meant to reach
    description: str


def _playbook(mock_id: str, alias: str, label: str, expected_tier: str) -> ScenarioInfo:
    return ScenarioInfo(
        id=mock_id,
        alias=alias,
        label=label,
        group="playbook",
        expected_tier=expected_tier,
        description=DEMO_NARRATION[mock_id],
    )


#: Ordered for display: baseline first, then the coarse scenarios, then the
#: scripted playbooks. Every entry's ``id`` must be in ``MOCK_SCENARIOS``.
SCENARIO_CATALOGUE: Tuple[ScenarioInfo, ...] = (
    ScenarioInfo(
        id="resting",
        alias="resting",
        label="Resting",
        group="baseline",
        expected_tier="none",
        description="Quiet baseline: HR ~72 BPM, gravity-only accelerometer. Nothing should escalate.",
    ),
    ScenarioInfo(
        id="fall_a_standard",
        alias="fall-a-standard",
        label="Standard fall",
        group="scenario",
        expected_tier="tier_1_verify",
        description="PRD scenario A: impact spike at t=5s, then stillness with a panic HR climb.",
    ),
    ScenarioInfo(
        id="fall_b_seizure",
        alias="fall-b-seizure",
        label="Seizure",
        group="scenario",
        expected_tier="tier_2_seizure",
        description="PRD scenario B: hard impact at t=5s, 5 Hz tremor, HR climbing past 130 BPM.",
    ),
    ScenarioInfo(
        id="fall_c_syncope",
        alias="fall-c-syncope",
        label="Syncope",
        group="scenario",
        expected_tier="tier_2_cardiac",
        description="PRD scenario C: soft collapse at t=5s, bradycardia, then the pulse disappears.",
    ),
    _playbook("demo_syncope_seizure", "syncope-seizure", "Syncope → seizure", "tier_2_cardiac"),
    _playbook("demo_trip_fall", "trip-fall", "Trip fall", "tier_1_verify"),
    _playbook("demo_night_syncope", "night-syncope", "Night syncope", "tier_2_cardiac"),
    _playbook("demo_tonic_clonic", "tonic-clonic", "Tonic-clonic", "tier_2_seizure"),
    _playbook("demo_silent_slump", "silent-slump", "Silent slump", "tier_1_verify"),
)

_BY_ID = {info.id: info for info in SCENARIO_CATALOGUE}
_BY_ALIAS = {info.alias: info for info in SCENARIO_CATALOGUE}


def resolve_scenario(name: str) -> ScenarioInfo:
    """Look a scenario up by canonical id or by CLI alias.

    Both spellings are accepted because the CLI has always used the
    hyphenated form (``--demo trip-fall``) while the mock module uses
    snake_case ids. Raises :class:`ControlError` so the HTTP layer can turn
    a typo into a 400 rather than a 500.
    """
    key = (name or "").strip()
    info = _BY_ID.get(key) or _BY_ALIAS.get(key.replace("_", "-"))
    if info is None:
        raise ControlError(
            "unknown_scenario",
            f"Unknown scenario {name!r}. Expected one of: "
            f"{', '.join(i.alias for i in SCENARIO_CATALOGUE)}.",
        )
    return info


class ControlError(Exception):
    """A control request that was rejected, with a machine-readable code.

    ``code`` is one of ``disabled``, ``unavailable`` or
    ``unknown_scenario``; the HTTP layer maps those to 403 / 409 / 400.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ControlState:
    """Everything the control panel needs to render itself."""

    enabled: bool           # the HTTP control surface is switched on
    available: bool         # ...and this runtime can actually honour it
    reason: str             # why not, when available is False
    scenario: str
    scenario_label: str
    elapsed_s: float
    generation: int
    sensor_source: str
    drill: bool             # a non-baseline scenario is being driven

    def as_json(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "reason": self.reason,
            "scenario": self.scenario,
            "scenario_label": self.scenario_label,
            "elapsed_s": round(self.elapsed_s, 2),
            "generation": self.generation,
            "sensor_source": self.sensor_source,
            "drill": self.drill,
            "scenarios": [
                {
                    "id": info.id,
                    "alias": info.alias,
                    "label": info.label,
                    "group": info.group,
                    "expected_tier": info.expected_tier,
                    "description": info.description,
                }
                for info in SCENARIO_CATALOGUE
            ],
        }


class ScenarioController:
    """Gate and audit runtime scenario changes.

    ``available`` is the honest answer to "would pressing a button do
    anything?". It is ``False`` when the runtime has no
    :class:`ScenarioClock` to drive - i.e. the sensors are real - and the
    panel renders itself disabled with :attr:`unavailable_reason` shown to
    the operator.
    """

    def __init__(
        self,
        clock: Optional[ScenarioClock] = None,
        *,
        enabled: bool = False,
        runtime_status: Optional[CaregiverRuntimeStatus] = None,
        unavailable_reason: str = "",
    ) -> None:
        self.clock = clock
        self.enabled = bool(enabled)
        self.runtime_status = runtime_status
        self.unavailable_reason = unavailable_reason or (
            "Scenario control needs the synthetic sensor client "
            "(start the pipeline with --mock-ble)."
        )

    # -- queries ---------------------------------------------------------- #

    @property
    def available(self) -> bool:
        return self.clock is not None

    @property
    def sensor_source(self) -> str:
        return SENSOR_SOURCE_MOCK if self.clock is not None else SENSOR_SOURCE_HARDWARE

    @property
    def scenario(self) -> str:
        return self.clock.scenario if self.clock is not None else "resting"

    @property
    def drill(self) -> bool:
        """True while scripted telemetry could escalate.

        ``resting`` is excluded: it is the quiet baseline and never
        escalates, so flagging it as a drill would make the marker noise
        that operators learn to ignore.
        """
        return self.clock is not None and self.clock.scenario != "resting"

    def state(self) -> ControlState:
        info = _BY_ID.get(self.scenario)
        return ControlState(
            enabled=self.enabled,
            available=self.available,
            reason="" if self.available else self.unavailable_reason,
            scenario=self.scenario,
            scenario_label=info.label if info else self.scenario,
            elapsed_s=self.clock.elapsed_s if self.clock is not None else 0.0,
            generation=self.clock.generation if self.clock is not None else 0,
            sensor_source=self.sensor_source,
            drill=self.drill,
        )

    def simulation_payload(self, *, ppg_source: str) -> Dict[str, Any]:
        """The ``simulation`` block for the monitoring / alert payloads.

        Downstream consumers use this to tell a drill from a real event.
        Keep it on every payload, not just the drills - a field that only
        appears sometimes is a field nobody checks for.
        """
        return {
            "drill": self.drill,
            "sensor_source": self.sensor_source,
            "ppg_source": ppg_source,
            "scenario": self.scenario if self.clock is not None else None,
        }

    # -- commands --------------------------------------------------------- #

    def select(self, name: str) -> ControlState:
        """Activate a scenario by id or alias, replaying it from t=0."""
        info = resolve_scenario(name)
        clock = self._require_clock()
        clock.select(info.id)
        self._audit("activated", info)
        return self.state()

    def restart(self) -> ControlState:
        """Replay the active scenario from t=0."""
        clock = self._require_clock()
        clock.restart()
        self._audit("restarted", _BY_ID.get(clock.scenario))
        return self.state()

    def reset(self) -> ControlState:
        """Return to the quiet baseline - the panel's "stop" button."""
        return self.select("resting")

    # -- internals -------------------------------------------------------- #

    def _require_clock(self) -> ScenarioClock:
        if not self.enabled:
            raise ControlError(
                "disabled",
                "Scenario control is disabled. Set monitoring.control_enabled: "
                "true to enable it (bench use only - it can fire real alerts).",
            )
        if self.clock is None:
            raise ControlError("unavailable", self.unavailable_reason)
        return self.clock

    def _audit(self, verb: str, info: Optional[ScenarioInfo]) -> None:
        label = info.label if info else self.scenario
        detail = (
            f"Synthetic scenario '{label}' {verb} from the control panel. "
            f"Telemetry is scripted, not measured"
            + (
                f"; expect {info.expected_tier}. {info.description}"
                if info
                else "."
            )
        )
        log.warning("Scenario control: %s", detail)
        if self.runtime_status is not None:
            self.runtime_status.push_event(
                severity="warning" if self.drill else "info",
                category="sensor",
                title=f"Drill: {label}" if self.drill else f"Baseline: {label}",
                detail=detail,
            )


def build_scenario_controller(
    sensors: Any,
    *,
    enabled: bool,
    runtime_status: Optional[CaregiverRuntimeStatus] = None,
) -> ScenarioController:
    """Wire a controller to whatever sensor client the runtime built.

    Duck-typed on the ``clock`` attribute so only the synthetic client is
    controllable; the TCP and BLE clients have no scenario to switch and
    the controller reports itself unavailable.
    """
    clock = getattr(sensors, "clock", None)
    if not isinstance(clock, ScenarioClock):
        clock = None
    return ScenarioController(clock, enabled=enabled, runtime_status=runtime_status)


__all__ = [
    "SENSOR_SOURCE_HARDWARE",
    "SENSOR_SOURCE_MOCK",
    "SCENARIO_CATALOGUE",
    "ControlError",
    "ControlState",
    "ScenarioController",
    "ScenarioInfo",
    "build_scenario_controller",
    "resolve_scenario",
]
