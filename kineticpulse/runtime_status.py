"""Shared caregiver-facing runtime status for monitoring + session meta.

Updated by the dispatch worker; read by ``MonitoringPublisher``.
"""

from __future__ import annotations

import collections
import time
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional


#: Vision throughput below this reads as degraded on the dashboard. A fall
#: lasts well under a second, so a handful of frames per second is the point
#: at which the detector starts stepping over the event entirely. Measured on
#: an Orin Nano: 16-17 FPS healthy on the GPU, 0.65 FPS on the CPU fallback.
VISION_FPS_WARN = 10.0
VISION_FPS_CRITICAL = 5.0


@dataclass
class CaregiverRuntimeStatus:
    voice_status: str = "not_required"
    alert_dispatch_status: str = "idle"

    # --- Runtime health ---------------------------------------------------- #
    # Surfaced to the dashboard because a silent CPU fallback is a safety
    # regression, not just a performance one: the same pipeline that runs at
    # 16 FPS on the GPU runs at 0.65 FPS on the CPU and steps over falls. It
    # used to be visible only in the logs, which nobody watches.
    accelerator: str = "unknown"          # "cuda" | "cpu" | "unknown"
    accelerator_device: Optional[str] = None
    vision_fps: Optional[float] = None
    detector_backend: Optional[str] = None    # tensorrt | pytorch | onnx | ...
    pose_backend: Optional[str] = None

    _events: Deque[Dict] = field(default_factory=lambda: collections.deque(maxlen=40))
    _event_seq: int = 0

    def set_voice(self, status: str) -> None:
        self.voice_status = status

    def set_alert(self, status: str) -> None:
        self.alert_dispatch_status = status

    def set_accelerator(self, kind: str, device: Optional[str] = None) -> None:
        self.accelerator = kind
        self.accelerator_device = device

    def set_vision_fps(self, fps: Optional[float]) -> None:
        self.vision_fps = fps

    def set_backends(
        self,
        detector: Optional[str] = None,
        pose: Optional[str] = None,
    ) -> None:
        if detector is not None:
            self.detector_backend = detector
        if pose is not None:
            self.pose_backend = pose

    def runtime_payload(self) -> Dict:
        """Runtime health for the dashboard's ``runtime`` block."""
        fps = self.vision_fps
        if self.accelerator == "cpu":
            health = "critical"
        elif fps is None:
            health = "unknown"
        elif fps < VISION_FPS_CRITICAL:
            health = "critical"
        elif fps < VISION_FPS_WARN:
            health = "degraded"
        else:
            health = "ok"
        return {
            "health": health,
            "accelerator": self.accelerator,
            "accelerator_device": self.accelerator_device,
            "vision_fps": round(fps, 1) if fps is not None else None,
            "detector_backend": self.detector_backend,
            "pose_backend": self.pose_backend,
            "fps_warn_below": VISION_FPS_WARN,
            "fps_critical_below": VISION_FPS_CRITICAL,
        }

    def push_event(
        self,
        *,
        severity: str,
        category: str,
        title: str,
        detail: str,
        timestamp_ms: Optional[int] = None,
    ) -> None:
        self._event_seq += 1
        # Runtime callers pass fusion's monotonic timestamps. Those are valid
        # for internal ordering but not for dashboard dates or SQLite history.
        if timestamp_ms is None or timestamp_ms < 1_000_000_000_000:
            timestamp_ms = int(time.time() * 1000)
        self._events.append(
            {
                "id": f"runtime-{self._event_seq}",
                "timestamp_ms": timestamp_ms,
                "severity": severity,
                "category": category,
                "title": title,
                "detail": detail,
            }
        )

    def events_payload(self) -> List[Dict]:
        return list(reversed(self._events))
