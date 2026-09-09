"""Alert payload construction.

Per PRD section 2.3 the payload sent to webhooks must include the subject
identifier, location, alert nature, and current vital signs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

from kineticpulse.config import AlertsConfig
from kineticpulse.fusion.engine import FusionSnapshot
from kineticpulse.fusion.tiers import EmergencyTier
from kineticpulse.sensors.ppg_sim import PPG_SOURCE_HARDWARE, PPG_SOURCE_SIMULATED
from kineticpulse.utils.timing import now_ms


_TIER_TO_NATURE = {
    EmergencyTier.TIER_1_VERIFY: "Unverified Fall",
    EmergencyTier.TIER_2_SEIZURE: "Suspected Seizure",
    EmergencyTier.TIER_2_CARDIAC: "Cardiovascular Emergency",
}


@dataclass
class AlertPayload:
    subject_id: str
    location: str
    nature: str
    scenario: str
    severity: str
    tier: str
    reason: str
    vitals: Dict[str, Any] = field(default_factory=dict)
    detector: Dict[str, Any] = field(default_factory=dict)
    voice: Dict[str, Any] = field(default_factory=dict)
    session: Dict[str, Any] = field(default_factory=dict)
    #: Provenance of the telemetry behind this alert. ``drill: true`` means a
    #: scripted scenario produced it, not a measured event - see
    #: :mod:`kineticpulse.control`. Always populated so a recipient can check
    #: one field instead of inferring from absence.
    simulation: Dict[str, Any] = field(default_factory=dict)
    timestamp_ms: int = 0

    def as_json(self) -> Dict[str, Any]:
        return asdict(self)


def build_payload(
    alerts_cfg: AlertsConfig,
    snapshot: FusionSnapshot,
    *,
    nature_override: Optional[str] = None,
    voice_extra: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    ppg_source: str = PPG_SOURCE_HARDWARE,
    simulation: Optional[Dict[str, Any]] = None,
) -> AlertPayload:
    """Assemble the webhook payload for one confirmed emergency.

    ``ppg_source`` records where the heart rate came from. On a bench run
    with a dead MAX30102 it is ``"simulated"``, and the recipient needs to
    see that in the payload rather than trust a synthetic BPM - see
    :mod:`kineticpulse.sensors.ppg_sim`.

    ``simulation`` carries the same idea one level up: when a control-panel
    scenario is driving the pipeline, this alert is a drill and must say so.
    :meth:`kineticpulse.control.ScenarioController.simulation_payload`
    produces it. Defaults to a hardware, non-drill block.
    """
    decision = snapshot.decision
    nature = nature_override or _TIER_TO_NATURE.get(decision.tier, "Fall Event")
    severity = "critical" if decision.tier.bypasses_voice else "elevated"

    vitals = {
        "heart_rate_bpm": snapshot.latest_hr_bpm,
        "hr_signature": snapshot.hr.value,
        "heart_rate_source": ppg_source,
        "heart_rate_simulated": ppg_source == PPG_SOURCE_SIMULATED,
        "accel_magnitude_g": snapshot.latest_accel_g,
        "accel_signature": snapshot.accel.value,
    }

    detector = {
        "class": snapshot.detector_class,
        "confidence": snapshot.detector_conf,
        "pose_signature": snapshot.pose.value,
        "action_class": snapshot.action_class,
        "action_confidence": snapshot.action_conf,
    }

    return AlertPayload(
        subject_id=alerts_cfg.subject_id,
        location=alerts_cfg.location,
        nature=nature,
        scenario=decision.scenario,
        severity=severity,
        tier=decision.tier.value,
        reason=decision.reason,
        vitals=vitals,
        detector=detector,
        voice=voice_extra or {},
        session={"id": session_id} if session_id else {},
        simulation=simulation or {
            "drill": False,
            "sensor_source": "hardware",
            "ppg_source": ppg_source,
            "scenario": None,
        },
        timestamp_ms=snapshot.timestamp_ms or now_ms(),
    )
