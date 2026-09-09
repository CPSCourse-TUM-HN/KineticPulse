"""Live annotated preview window for the running orchestrator.

``scripts/live_predict.py`` has always had a preview, but it is a standalone
tool: it opens the camera itself, so it cannot run next to
:mod:`kineticpulse.main` (one ``/dev/video0``, one owner). That left the full
pipeline with no way to *watch* what it was deciding.

This module is that window. It draws on the frames the vision worker is
already processing and overlays the state the fusion engine is already
publishing, so enabling it changes nothing about the pipeline's behaviour -
it only costs a few milliseconds of drawing per frame.

Three panels:

* **detection** - posture bbox from the 4-class checkpoint plus the COCO-17
  skeleton from the pose backbone;
* **motion** - the signals that actually drive a fall decision: torso angle,
  centroid velocity and stillness from vision, and accelerometer magnitude
  plus its classified signature from the wristband;
* **fusion** - the tier the engine has settled on and the three signatures
  behind it.

The window is strictly optional. On a headless Jetson (no ``DISPLAY``, or an
OpenCV build without a GUI backend) it disables itself with one warning and
the pipeline keeps running - a preview failing must never take the monitor
down with it.

Because the Jetson is normally driven over SSH (see ``docs/E2E_LAB.md``), the
same overlay can also be written to a file instead of, or as well as, a
window: :meth:`PreviewWindow.render` refreshes ``snapshot_path`` every
``snapshot_every`` frames via an atomic replace, so a reader never sees a
half-written PNG. That makes the overlay usable from a terminal session, and
it is how the overlay is verified in tests.

The third sink is the caregiver dashboard. Given a
:class:`~kineticpulse.vision.frame_bus.PreviewFrameBus`, the overlay is also
encoded to JPEG and published for :mod:`kineticpulse.monitoring.http` to serve
at ``/preview.mjpg``, which is what the dashboard's Detection panel renders.
Each sink has its own encoder thread and its own cadence, so a slow PNG
snapshot cannot throttle the live feed and neither can slow the pipeline: the
render path submits and moves on, dropping a frame rather than waiting.

Encoding happens on a background thread. Measured at 1280x720 on an Orin
Nano, a full-resolution PNG encode is ~55 ms - longer than a whole frame
budget - and doing it inline cost 6.4 FPS of a 17.4 FPS pipeline, because it
holds the GIL and stalls the asyncio loop that feeds the inference executor.
A snapshot is a "latest frame" view, so when a write is still in flight the
next one is dropped rather than queued: a slightly stale file is much better
than a vision loop waiting on the disk.
"""

from __future__ import annotations

import collections
import os
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Deque, Optional

import numpy as np

from kineticpulse.utils.logging import get_logger
from kineticpulse.vision.frame_bus import PreviewFrameBus

log = get_logger(__name__)

# COCO-17 skeleton. Duplicated from scripts/live_predict.py rather than
# imported: scripts/ is not an importable package, and importing that module
# would pull in its whole CLI. Keep the two in sync if the palette changes.
_LIMB_TORSO = (200, 200, 60)
_LIMB_ARM = (80, 200, 255)
_LIMB_LEG = (255, 120, 200)
_COCO17_EDGES = (
    (5, 6, _LIMB_TORSO), (5, 11, _LIMB_TORSO), (6, 12, _LIMB_TORSO),
    (11, 12, _LIMB_TORSO),
    (5, 7, _LIMB_ARM), (7, 9, _LIMB_ARM), (6, 8, _LIMB_ARM), (8, 10, _LIMB_ARM),
    (11, 13, _LIMB_LEG), (13, 15, _LIMB_LEG),
    (12, 14, _LIMB_LEG), (14, 16, _LIMB_LEG),
    (0, 5, (150, 150, 150)), (0, 6, (150, 150, 150)),
)
_KP_CONF_THRESHOLD = 0.3
_KP_COLOR = (255, 255, 255)

_CLASS_COLORS = {
    "fallen": (0, 0, 235),
    "falling": (0, 140, 255),
    "stand": (0, 200, 0),
    "sitting": (255, 180, 0),
}

# Fusion tier -> BGR. Deliberately the same red/amber/grey language as the
# dashboard so a glance at either surface reads the same.
_TIER_COLORS = {
    "none": (150, 150, 150),
    "tier_0_dismiss": (150, 150, 150),
    "tier_1_verify": (0, 170, 255),
    "tier_2_seizure": (0, 0, 235),
    "tier_2_cardiac": (0, 0, 235),
}
_TIER_LABELS = {
    "none": "MONITORING",
    "tier_0_dismiss": "DISMISSED",
    "tier_1_verify": "TIER 1 - VERIFY",
    "tier_2_seizure": "TIER 2 - SEIZURE",
    "tier_2_cardiac": "TIER 2 - CARDIAC",
}

_ACCEL_LABELS = {
    "unknown": "no data",
    "quiet": "quiet",
    "impact": "IMPACT",
    "impact_tremor": "IMPACT + TREMOR",
    "soft_collapse": "SOFT COLLAPSE",
}

#: |a| in g that fills the motion bar. The impact thresholds sit around 2.5-4 g.
_ACCEL_BAR_FULL_G = 5.0

# Panel geometry. Both bottom panels share it so they stay aligned, and it is
# sized to the tallest content (MOTION: four vision rows, the accel row, the
# bar, and the signature caption) so nothing spills past the frame edge.
_PANEL_W = 268
_PANEL_H = 184
_PANEL_BOTTOM_MARGIN = 12
_ROW_STEP = 22
_ROW_FIRST = 46


@dataclass
class PreviewStats:
    """Rolling frame timing, so the overlay can show a real FPS."""

    window: int = 30

    def __post_init__(self) -> None:
        self._dt: Deque[float] = collections.deque(maxlen=self.window)
        self._last_ms: Optional[int] = None

    def tick(self, timestamp_ms: int) -> Optional[float]:
        if self._last_ms is not None:
            dt = (timestamp_ms - self._last_ms) / 1000.0
            if dt > 0:
                self._dt.append(dt)
        self._last_ms = timestamp_ms
        if not self._dt:
            return None
        mean = sum(self._dt) / len(self._dt)
        return 1.0 / mean if mean > 0 else None


class PreviewWindow:
    """An OpenCV window showing detection, motion and fusion state.

    ``render`` returns ``False`` once the operator presses ``q`` or ``ESC``,
    which the caller turns into a pipeline shutdown.
    """

    DEFAULT_TITLE = "KineticPulse - motion detection"

    def __init__(
        self,
        *,
        title: str = DEFAULT_TITLE,
        scale: float = 1.0,
        window: bool = True,
        snapshot_path: Optional[str] = None,
        snapshot_every: int = 15,
        frame_bus: Optional[PreviewFrameBus] = None,
        stream_every: int = 1,
        stream_quality: int = 72,
    ) -> None:
        self.title = title
        self.scale = float(scale)
        self.want_window = bool(window)
        self.snapshot_path = snapshot_path
        self.snapshot_every = max(1, int(snapshot_every))
        self.frame_bus = frame_bus
        self.stream_every = max(1, int(stream_every))
        self.stream_quality = max(1, min(100, int(stream_quality)))
        self._cv2: Any = None
        self._ready = False
        self._disabled_reason: Optional[str] = None
        self._stats = PreviewStats()
        self._window_open = False
        self._frames = 0
        self._fps: Optional[float] = None
        self._snapshot_failed = False
        self._snapshot_pool: Optional[ThreadPoolExecutor] = None
        self._snapshot_future: Optional[Future] = None
        self._snapshot_dropped = 0
        self._snapshot_written = 0
        self._stream_failed = False
        self._stream_pool: Optional[ThreadPoolExecutor] = None
        self._stream_future: Optional[Future] = None
        self._stream_dropped = 0

    # -- lifecycle -------------------------------------------------------- #

    def open(self) -> bool:
        """Start the preview. Returns False (and explains why) if impossible."""
        if self._disabled_reason is not None:
            return False
        try:
            import cv2  # noqa: PLC0415 - optional dependency of the preview only
        except Exception as exc:                     # pragma: no cover
            self._disable(f"OpenCV import failed: {exc}")
            return False
        self._cv2 = cv2

        if self.want_window:
            self._window_open = self._open_window()
        else:
            self._window_open = False

        if not self._window_open and not self.snapshot_path and self.frame_bus is None:
            # Nothing left to render to. _note_no_window() has already recorded
            # a reason when a window was attempted; say so explicitly when the
            # caller asked for no output at all.
            if self._disabled_reason is None:
                self._disable(
                    "no output requested: neither a window, a snapshot path "
                    "nor a frame bus"
                )
            return False

        self._ready = True
        if self.snapshot_path:
            self._snapshot_pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="preview-snap"
            )
            log.info(
                "Preview snapshots -> %s (every %d frames, encoded off-thread)",
                self.snapshot_path, self.snapshot_every,
            )
        if self.frame_bus is not None:
            # A pool of its own, not the snapshot pool: a 1280x720 PNG encode
            # takes an order of magnitude longer than the JPEG the feed wants,
            # and sharing one worker would let the snapshot cadence throttle
            # the live feed to its own rate.
            self._stream_pool = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="preview-stream"
            )
            log.info(
                "Preview feed -> dashboard (every %d frame(s), JPEG q%d, "
                "encoded off-thread)",
                self.stream_every, self.stream_quality,
            )
        return True

    def _open_window(self) -> bool:
        cv2 = self._cv2
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            self._note_no_window(
                "no DISPLAY / WAYLAND_DISPLAY in the environment (headless session)"
            )
            return False
        try:
            cv2.namedWindow(self.title, cv2.WINDOW_NORMAL)
        except Exception as exc:
            # An OpenCV built without GUI support raises here rather than at
            # import, so this is the real capability check.
            self._note_no_window(f"OpenCV has no usable GUI backend: {exc}")
            return False
        log.info("Preview window open: %r (press q or ESC to stop)", self.title)
        return True

    def _note_no_window(self, reason: str) -> None:
        """No window, but snapshots may still be wanted - do not disable those."""
        if self.snapshot_path:
            log.warning(
                "Preview window unavailable (%s); writing snapshots to %s instead.",
                reason, self.snapshot_path,
            )
        else:
            self._disable(reason)

    def close(self) -> None:
        pool, self._snapshot_pool = self._snapshot_pool, None
        if pool is not None:
            # Wait briefly so the last frame actually lands on disk, but do
            # not hold shutdown open on a slow encode.
            future = self._snapshot_future
            if future is not None:
                try:
                    future.result(timeout=2.0)
                except Exception:
                    pass
            pool.shutdown(wait=False)
        if self._snapshot_dropped:
            log.info(
                "Preview snapshots: %d written, %d dropped while encoding.",
                self._snapshot_written, self._snapshot_dropped,
            )

        stream_pool, self._stream_pool = self._stream_pool, None
        if stream_pool is not None:
            stream_pool.shutdown(wait=False)
        if self.frame_bus is not None:
            # Drop the last frame rather than leaving it served: a still image
            # of a calm room is exactly what a stopped pipeline must not look
            # like. The panel reports the feed as stopped instead.
            log.info(
                "Preview feed: %d frame(s) published, %d dropped while "
                "encoding.", self.frame_bus.published, self._stream_dropped,
            )
            self.frame_bus.clear()
        if not self._ready or self._cv2 is None or not self._window_open:
            self._ready = False
            return
        try:
            self._cv2.destroyWindow(self.title)
            # Qt needs a few event-loop turns to actually tear the window down.
            for _ in range(4):
                self._cv2.waitKey(1)
        except Exception:
            pass
        self._ready = False

    @property
    def active(self) -> bool:
        return self._ready

    @property
    def window_open(self) -> bool:
        """True when an on-screen window exists (vs snapshots only)."""
        return self._window_open

    @property
    def disabled_reason(self) -> Optional[str]:
        return self._disabled_reason

    def _disable(self, reason: str) -> None:
        self._disabled_reason = reason
        self._ready = False
        log.warning("Preview disabled: %s. The pipeline continues without it.", reason)

    # -- rendering -------------------------------------------------------- #

    def render(
        self,
        image: np.ndarray,
        *,
        timestamp_ms: int,
        detection: Any = None,
        pose: Any = None,
        features: Any = None,
        snapshot: Any = None,
        simulation: Optional[dict] = None,
    ) -> bool:
        """Draw one frame. Returns False when the operator asked to quit."""
        if not self._ready or self._cv2 is None:
            return True
        cv2 = self._cv2
        fps = self._stats.tick(timestamp_ms)
        self._fps = fps

        try:
            canvas = image.copy()
            if self.scale != 1.0 and self.scale > 0:
                canvas = cv2.resize(
                    canvas, None, fx=self.scale, fy=self.scale,
                    interpolation=cv2.INTER_AREA,
                )
                factor = self.scale
            else:
                factor = 1.0

            self._draw_pose(canvas, pose, factor)
            self._draw_detection(canvas, detection, factor)
            self._draw_header(canvas, fps, snapshot)
            self._draw_motion_panel(canvas, features, snapshot)
            self._draw_fusion_panel(canvas, snapshot)
            self._draw_provenance(canvas, simulation)

        except Exception as exc:
            # A GUI that dies mid-run (display unplugged, X restarted) must not
            # take the monitoring pipeline with it.
            self._disable(f"render failed: {exc}")
            return True

        self._frames += 1
        self._write_snapshot(canvas)
        self._publish_frame(canvas)

        if not self._window_open:
            return True
        try:
            cv2.imshow(self.title, canvas)
            key = cv2.waitKey(1) & 0xFF
        except Exception as exc:
            self._note_no_window(f"imshow failed: {exc}")
            self._window_open = False
            return True

        if key in (ord("q"), 27):
            log.info("Preview: quit key pressed; stopping the pipeline.")
            return False
        return True

    def _write_snapshot(self, canvas: np.ndarray) -> None:
        """Hand the frame to the encoder thread, if a snapshot was requested.

        Never blocks: an encode still in flight means this snapshot is
        dropped. ``canvas`` is a per-render copy that nothing mutates
        afterwards, so it is safe to pass across the thread boundary.
        """
        if not self.snapshot_path or self._snapshot_failed:
            return
        if self._frames % self.snapshot_every != 0:
            return
        pool = self._snapshot_pool
        if pool is None:
            return
        if self._snapshot_future is not None and not self._snapshot_future.done():
            self._snapshot_dropped += 1
            # Report periodically: silently dropping every snapshot would look
            # like a stalled pipeline rather than a too-short interval.
            if self._snapshot_dropped % 50 == 1:
                log.info(
                    "Preview: dropped %d snapshot(s) still encoding; raise "
                    "--preview-snapshot-every or use a .jpg path.",
                    self._snapshot_dropped,
                )
            return
        try:
            self._snapshot_future = pool.submit(self._encode_snapshot, canvas)
        except RuntimeError:
            # Pool already shut down (close() raced with a final render).
            return

    def _publish_frame(self, canvas: np.ndarray) -> None:
        """Hand the frame to the JPEG encoder for the dashboard feed.

        Same contract as :meth:`_write_snapshot`: never blocks, and drops the
        frame if the previous encode is still running. Dropping is the right
        failure here - the panel wants the newest frame, so queueing would only
        add latency to an already-late feed.
        """
        bus = self.frame_bus
        if bus is None or self._stream_failed:
            return
        if self._frames % self.stream_every != 0:
            return
        pool = self._stream_pool
        if pool is None:
            return
        if self._stream_future is not None and not self._stream_future.done():
            self._stream_dropped += 1
            if self._stream_dropped % 200 == 1:
                log.info(
                    "Preview feed: dropped %d frame(s) still encoding; raise "
                    "monitoring.preview_stream_every to encode less often.",
                    self._stream_dropped,
                )
            return
        fps = self._fps
        try:
            self._stream_future = pool.submit(self._encode_frame, canvas, fps)
        except RuntimeError:
            # Pool already shut down (close() raced with a final render).
            return

    def _encode_frame(self, canvas: np.ndarray, fps: Optional[float]) -> None:
        """Encode one overlay frame to JPEG and publish it. Off the hot loop."""
        bus = self.frame_bus
        if bus is None:
            return
        try:
            ok, buf = self._cv2.imencode(
                ".jpg", canvas,
                [int(self._cv2.IMWRITE_JPEG_QUALITY), self.stream_quality],
            )
            if not ok:
                raise OSError("imencode returned False")
            bus.publish(buf.tobytes(), fps=fps)
        except Exception as exc:
            # Losing the feed must never take the monitor down, so record the
            # reason once and let the rest of the preview carry on.
            self._stream_failed = True
            log.warning(
                "Preview feed encode failed: %s. The dashboard Detection "
                "panel will report the feed as unavailable; monitoring "
                "continues.", exc,
            )

    def flush_frame(self, timeout: float = 5.0) -> bool:
        """Block until any in-flight feed encode has finished.

        The mirror of :meth:`flush_snapshot`, for callers that need to observe
        the bus rather than the file - tests, and the one-shot capture path.
        """
        future = self._stream_future
        if future is None:
            return True
        try:
            future.result(timeout=timeout)
            return True
        except Exception:
            return False

    def flush_snapshot(self, timeout: float = 5.0) -> bool:
        """Block until any in-flight snapshot encode has finished.

        For callers that need to *observe* the file - tests, or a one-shot
        capture. The render path never waits; that is the whole point of the
        encoder thread. Returns False if the wait timed out.
        """
        future = self._snapshot_future
        if future is None:
            return True
        try:
            future.result(timeout=timeout)
            return True
        except Exception:
            return False

    def _encode_snapshot(self, canvas: np.ndarray) -> None:
        """Encode and atomically replace the snapshot. Runs off the hot loop."""
        try:
            # Write beside the target then rename, so a reader polling the file
            # never opens a half-written image. The temp name has to keep the
            # real extension: imwrite picks its encoder from the suffix, and a
            # bare ".tmp" has none.
            root, ext = os.path.splitext(self.snapshot_path or "")
            if not ext:
                raise ValueError(
                    "snapshot path needs an image extension, e.g. .png or .jpg"
                )
            tmp = f"{root}.partial{ext}"
            if not self._cv2.imwrite(tmp, canvas):
                raise OSError("imwrite returned False")
            os.replace(tmp, self.snapshot_path)
            self._snapshot_written += 1
        except Exception as exc:
            self._snapshot_failed = True
            log.warning(
                "Preview snapshot to %s failed: %s. Continuing without it.",
                self.snapshot_path, exc,
            )

    # -- drawing helpers -------------------------------------------------- #

    def _draw_pose(self, img: np.ndarray, pose: Any, factor: float) -> None:
        cv2 = self._cv2
        kpts = getattr(pose, "keypoints", None) if pose is not None else None
        if kpts is None or getattr(kpts, "shape", (0,))[0] < 17:
            return
        confs = kpts[:, 2]
        for a, b, color in _COCO17_EDGES:
            if confs[a] < _KP_CONF_THRESHOLD or confs[b] < _KP_CONF_THRESHOLD:
                continue
            pa = (int(kpts[a, 0] * factor), int(kpts[a, 1] * factor))
            pb = (int(kpts[b, 0] * factor), int(kpts[b, 1] * factor))
            cv2.line(img, pa, pb, color, 2, cv2.LINE_AA)
        for i in range(17):
            if confs[i] < _KP_CONF_THRESHOLD:
                continue
            cv2.circle(
                img, (int(kpts[i, 0] * factor), int(kpts[i, 1] * factor)),
                3, _KP_COLOR, -1, cv2.LINE_AA,
            )

    def _draw_detection(self, img: np.ndarray, detection: Any, factor: float) -> None:
        cv2 = self._cv2
        if detection is None:
            return
        bbox = getattr(detection, "bbox_xyxy", None)
        if bbox is None:
            return
        cls = getattr(detection, "cls", None)
        label = getattr(cls, "value", str(cls or "person"))
        conf = float(getattr(detection, "confidence", 0.0) or 0.0)
        color = _CLASS_COLORS.get(label, (200, 200, 200))

        x1, y1, x2, y2 = (int(v * factor) for v in bbox)
        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2, cv2.LINE_AA)

        caption = f"{label} {conf:.2f}"
        (tw, th), _ = cv2.getTextSize(caption, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        # A box near the right edge would otherwise push its own label out of
        # frame, which is exactly when you most want to read it.
        cap_w = tw + 10
        cap_x = min(x1, max(0, img.shape[1] - cap_w))
        cap_bottom = y1 if y1 - th - 10 >= 0 else min(img.shape[0], y2 + th + 10)
        cv2.rectangle(
            img, (cap_x, max(0, cap_bottom - th - 10)), (cap_x + cap_w, cap_bottom),
            color, -1,
        )
        cv2.putText(
            img, caption, (cap_x + 5, cap_bottom - 6),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2, cv2.LINE_AA,
        )

    def _draw_header(self, img: np.ndarray, fps: Optional[float], snapshot: Any) -> None:
        cv2 = self._cv2
        h, w = img.shape[:2]
        tier = _tier_value(snapshot)
        color = _TIER_COLORS.get(tier, (150, 150, 150))
        text = _TIER_LABELS.get(tier, tier.upper())

        cv2.rectangle(img, (0, 0), (w, 40), (24, 24, 24), -1)
        cv2.rectangle(img, (0, 0), (w, 40), color, 2)
        cv2.putText(
            img, text, (12, 27),
            cv2.FONT_HERSHEY_SIMPLEX, 0.72, color, 2, cv2.LINE_AA,
        )
        right = f"{w}x{h}" + (f"  {fps:.1f} FPS" if fps else "")
        (tw, _), _ = cv2.getTextSize(right, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        cv2.putText(
            img, right, (w - tw - 12, 26),
            cv2.FONT_HERSHEY_SIMPLEX, 0.55, (190, 190, 190), 1, cv2.LINE_AA,
        )

    def _draw_motion_panel(self, img: np.ndarray, features: Any, snapshot: Any) -> None:
        """The signals a fall decision is actually made from."""
        cv2 = self._cv2
        h = img.shape[0]
        width = _PANEL_W
        x = 12
        y = h - _PANEL_H - _PANEL_BOTTOM_MARGIN

        _panel(cv2, img, x, y, width, _PANEL_H, "MOTION")

        rows = [
            ("torso angle", _fmt(getattr(features, "torso_angle_deg", None), "{:.0f} deg")),
            ("descent vel", _fmt(getattr(features, "centroid_vel_pps", None), "{:+.2f} bl/s")),
            ("stillness", _fmt(getattr(features, "stillness", None), "{:.2f}")),
            ("aspect w/h", _fmt(getattr(features, "aspect_ratio", None), "{:.2f}")),
        ]
        row_y = y + _ROW_FIRST
        for name, value in rows:
            cv2.putText(img, name, (x + 10, row_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (170, 170, 170), 1, cv2.LINE_AA)
            cv2.putText(img, value, (x + 148, row_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (245, 245, 245), 1, cv2.LINE_AA)
            row_y += _ROW_STEP

        # Accelerometer: the wristband half of "motion".
        accel_g = getattr(snapshot, "latest_accel_g", None) if snapshot else None
        accel_sig = _enum_value(getattr(snapshot, "accel", None)) or "unknown"
        sig_color = (0, 0, 235) if accel_sig in ("impact", "impact_tremor") else (
            (0, 170, 255) if accel_sig == "soft_collapse" else (0, 200, 0)
        )
        cv2.putText(img, "accel |a|", (x + 10, row_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.48, (170, 170, 170), 1, cv2.LINE_AA)
        cv2.putText(img, _fmt(accel_g, "{:.2f} g"), (x + 148, row_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (245, 245, 245), 1, cv2.LINE_AA)
        row_y += 8

        bar_x, bar_w, bar_h = x + 10, width - 20, 9
        cv2.rectangle(img, (bar_x, row_y), (bar_x + bar_w, row_y + bar_h),
                      (80, 80, 80), 1, cv2.LINE_AA)
        if accel_g is not None:
            fill = int(bar_w * min(1.0, max(0.0, float(accel_g) / _ACCEL_BAR_FULL_G)))
            if fill > 0:
                cv2.rectangle(img, (bar_x, row_y), (bar_x + fill, row_y + bar_h),
                              sig_color, -1, cv2.LINE_AA)
        row_y += bar_h + 21
        cv2.putText(img, _ACCEL_LABELS.get(accel_sig, accel_sig), (x + 10, row_y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.56, sig_color, 2, cv2.LINE_AA)

    def _draw_fusion_panel(self, img: np.ndarray, snapshot: Any) -> None:
        cv2 = self._cv2
        h, w = img.shape[:2]
        width = _PANEL_W
        x = w - width - 12
        y = h - _PANEL_H - _PANEL_BOTTOM_MARGIN

        _panel(cv2, img, x, y, width, _PANEL_H, "FUSION")

        bpm = getattr(snapshot, "latest_hr_bpm", None) if snapshot else None
        rows = [
            ("pose", _enum_value(getattr(snapshot, "pose", None)) or "-"),
            ("accel", _enum_value(getattr(snapshot, "accel", None)) or "-"),
            ("heart rate", _enum_value(getattr(snapshot, "hr", None)) or "-"),
            ("bpm", str(bpm) if bpm is not None else "-"),
            ("action", str(getattr(snapshot, "action_class", None) or "-")),
        ]
        row_y = y + _ROW_FIRST
        for name, value in rows:
            cv2.putText(img, name, (x + 10, row_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (170, 170, 170), 1, cv2.LINE_AA)
            cv2.putText(img, value, (x + 128, row_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (245, 245, 245), 1, cv2.LINE_AA)
            row_y += _ROW_STEP

        reason = getattr(getattr(snapshot, "decision", None), "reason", "") or ""
        for line in _wrap(reason, 34)[:2]:
            cv2.putText(img, line, (x + 10, row_y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (150, 190, 150), 1, cv2.LINE_AA)
            row_y += 15

    def _draw_provenance(self, img: np.ndarray, simulation: Optional[dict]) -> None:
        """Say so when the telemetry behind the overlay is synthetic.

        The camera feed is real but the wristband numbers next to it may not
        be, and the preview is exactly where that would otherwise be invisible.
        """
        cv2 = self._cv2
        if not simulation:
            return
        notes = []
        if simulation.get("drill"):
            scenario = simulation.get("scenario") or "scripted"
            notes.append(f"DRILL: {scenario} - telemetry is scripted")
        elif simulation.get("sensor_source") == "mock":
            notes.append("SYNTHETIC SENSORS - wristband telemetry is generated")
        if simulation.get("ppg_source") == "simulated":
            notes.append("SIMULATED HEART RATE - pulse loss cannot be detected")
        if not notes:
            return

        w = img.shape[1]
        band_h = 22 * len(notes) + 10
        top = 40
        overlay = img[top:top + band_h, 0:w]
        if overlay.size:
            img[top:top + band_h, 0:w] = (overlay * 0.35).astype(overlay.dtype)
        y = top + 20
        for note in notes:
            cv2.putText(img, note, (12, y),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.52, (0, 190, 255), 2, cv2.LINE_AA)
            y += 22


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _panel(cv2: Any, img: np.ndarray, x: int, y: int, w: int, h: int, title: str) -> None:
    """Semi-transparent backdrop plus a title, so text stays readable."""
    y = max(0, y)
    region = img[y:y + h, x:x + w]
    if region.size:
        img[y:y + h, x:x + w] = (region * 0.3).astype(region.dtype)
    cv2.rectangle(img, (x, y), (x + w, y + h), (90, 90, 90), 1, cv2.LINE_AA)
    cv2.putText(img, title, (x + 10, y + 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (230, 230, 230), 1, cv2.LINE_AA)


def _fmt(value: Any, template: str) -> str:
    if value is None:
        return "-"
    try:
        return template.format(float(value))
    except (TypeError, ValueError):
        return str(value)


def _enum_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _tier_value(snapshot: Any) -> str:
    decision = getattr(snapshot, "decision", None)
    tier = getattr(decision, "tier", None)
    return _enum_value(tier) or "none"


def _wrap(text: str, width: int) -> list:
    words = text.split()
    lines: list = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


__all__ = ["PreviewWindow", "PreviewStats"]
