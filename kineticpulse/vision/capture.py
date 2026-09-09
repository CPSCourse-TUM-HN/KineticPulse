"""Streaming video capture with pluggable sources.

Sources are constructed via :func:`build_source` from a :class:`CameraConfig`.
All concrete sources use OpenCV's ``VideoCapture`` for portability; the
GStreamer pipeline strings are written to take advantage of NVDEC /
``nvarguscamerasrc`` on Jetson when OpenCV has been built with GStreamer
support (the JetPack OpenCV does).

USB webcams go through :func:`open_device_capture`, which negotiates the
capture format explicitly (MJPG first -- see :attr:`CameraConfig.fourcc`)
instead of accepting whatever the driver picks by default.

The :class:`FrameQueue` is a thread-safe bounded queue with drop-oldest
backpressure to keep latency bounded (PRD: tight time-sync).
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from queue import Empty, Full, Queue
from typing import Optional, Union

import numpy as np

from kineticpulse.config import CameraConfig
from kineticpulse.utils.logging import get_logger
from kineticpulse.utils.timing import now_ms

log = get_logger(__name__)



def _fourcc_name(cv2, cap) -> str:
    """Read back the negotiated FOURCC as a 4-character string."""
    raw = int(cap.get(cv2.CAP_PROP_FOURCC))
    if raw <= 0:
        return "????"
    return "".join(chr((raw >> (8 * i)) & 0xFF) for i in range(4))


def open_device_capture(cv2, device: Union[str, int], cfg: CameraConfig):
    """Open a V4L2 / UVC capture device with the format negotiated explicitly.

    Shared by the runtime capture thread and the WebRTC track so both open
    the camera the same way. Three things matter and none of them are the
    OpenCV default:

    * **Backend.** ``CAP_V4L2`` is requested first on Linux; the generic
      ``CAP_ANY`` probe can land on a backend that ignores ``CAP_PROP_FOURCC``.
    * **FOURCC before the frame size.** V4L2 resolves the format at the
      first ``set()``, so requesting MJPG after the size leaves the device
      in YUYV -- which on a UVC webcam caps 1280x720 at ~7 FPS.
    * **A shallow driver buffer.** V4L2 defaults to 4 queued frames, so a
      frame can be ~4 frame-periods stale before the fusion engine sees it.
      Two is the floor that still lets the driver fill the next buffer while
      we decode the current one: measured on the Jetson's UGREEN cam at
      1280x720 MJPG, BUFFERSIZE 2 and 4 both sustain 28.7 FPS, while 1
      collapses to 14.3 FPS because every ``read()`` waits a full frame.

    Returns an opened ``VideoCapture``, or ``None`` if no backend opened it.
    """
    backends = [("V4L2", cv2.CAP_V4L2), ("ANY", cv2.CAP_ANY)]
    if not sys.platform.startswith("linux"):
        backends = [("ANY", cv2.CAP_ANY)]

    for name, backend in backends:
        cap = cv2.VideoCapture(device, backend)
        if not cap.isOpened():
            cap.release()
            continue

        wanted = (cfg.fourcc or "").strip().upper()
        if wanted and wanted not in ("NONE", "AUTO") and len(wanted) == 4:
            try:
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*wanted))
            except Exception:  # pragma: no cover - backend/driver dependent
                log.debug("Backend %s rejected FOURCC %s", name, wanted)
        if cfg.width > 0:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
        if cfg.height > 0:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
        if cfg.fps > 0:
            cap.set(cv2.CAP_PROP_FPS, cfg.fps)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        except Exception:  # pragma: no cover - not all backends expose it
            pass

        # Some UVC webcams hand out nothing until a few reads have gone by,
        # and a device that opens but never delivers is worse than one that
        # fails to open -- the capture thread would just log read failures.
        ready = False
        for _ in range(5):
            ok, _img = cap.read()
            if ok and _img is not None:
                ready = True
                break
            time.sleep(0.05)
        if not ready:
            log.warning("Backend %s opened %r but delivered no frame.", name, device)
            cap.release()
            continue

        got_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        got_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        got_fourcc = _fourcc_name(cv2, cap)
        log.info("Camera %r opened via %s: %dx%d %s @ %.0f FPS",
                 device, name, got_w, got_h, got_fourcc,
                 cap.get(cv2.CAP_PROP_FPS))
        if (cfg.width, cfg.height) != (got_w, got_h):
            log.warning("Requested %dx%d but the device negotiated %dx%d; "
                        "keypoint normalisation in temporal.image_width/height "
                        "should match the negotiated size.",
                        cfg.width, cfg.height, got_w, got_h)
        if wanted and wanted not in ("NONE", "AUTO") and got_fourcc != wanted:
            log.warning("Requested FOURCC %s but the device negotiated %s. "
                        "Uncompressed modes often cap the frame rate well "
                        "below camera.fps at this resolution.",
                        wanted, got_fourcc)
        return cap

    return None


@dataclass
class Frame:
    """A single captured frame with a monotonic timestamp."""

    image: np.ndarray            # BGR HxWxC uint8
    timestamp_ms: int
    seq: int = 0


class FrameQueue:
    """Bounded FIFO that drops the oldest frame when full (latency-first)."""

    def __init__(self, maxsize: int = 2) -> None:
        self._q: Queue[Frame] = Queue(maxsize=maxsize)
        self._dropped = 0

    def put(self, frame: Frame) -> None:
        try:
            self._q.put_nowait(frame)
        except Full:
            try:
                self._q.get_nowait()
                self._dropped += 1
            except Empty:
                pass
            try:
                self._q.put_nowait(frame)
            except Full:
                self._dropped += 1

    def get(self, timeout: Optional[float] = 1.0) -> Optional[Frame]:
        try:
            return self._q.get(timeout=timeout)
        except Empty:
            return None

    @property
    def dropped(self) -> int:
        return self._dropped

    def qsize(self) -> int:
        return self._q.qsize()


class FrameSource:
    """Base class. Concrete sources override :meth:`_pipeline`.

    ``capture_kind`` selects how :meth:`open` hands the spec to OpenCV:
    ``"gstreamer"`` treats it as a pipeline string, ``"device"`` as a V4L2
    capture device to be configured by :func:`open_device_capture`. It is a
    class attribute rather than a check on the spec type because a USB
    webcam may legitimately be addressed by path (``/dev/video0``) instead
    of by index, and a path is not a GStreamer pipeline.
    """

    capture_kind = "gstreamer"

    def __init__(self, cfg: CameraConfig) -> None:
        self.cfg = cfg
        self._cap = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._seq = 0
        self.queue = FrameQueue(maxsize=2)

    def _pipeline(self) -> Union[str, int]:  # pragma: no cover - subclass duty
        raise NotImplementedError

    def open(self) -> None:
        import cv2

        spec = self._pipeline()
        if self.capture_kind == "device":
            self._cap = open_device_capture(cv2, spec, self.cfg)
            if self._cap is None:
                raise RuntimeError(
                    f"Failed to open camera device {spec!r}. Check that it is "
                    f"listed in /dev/video*, that this user is in the 'video' "
                    f"group, and that no other process holds the camera."
                )
        else:
            self._cap = cv2.VideoCapture(spec, cv2.CAP_GSTREAMER)
            if not self._cap.isOpened():
                log.warning("GStreamer pipeline failed, falling back to default backend.")
                self._cap = cv2.VideoCapture(spec)

        if not self._cap.isOpened():
            raise RuntimeError(f"Failed to open camera source: {spec!r}")
        log.info("Camera opened: %s (%dx%d @ %d FPS desired)",
                 type(self).__name__, self.cfg.width, self.cfg.height, self.cfg.fps)

    def start(self) -> None:
        if self._cap is None:
            self.open()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _loop(self) -> None:
        backoff = 0.1
        while not self._stop.is_set():
            ok, img = self._cap.read()
            if not ok or img is None:
                log.warning("Frame read failed; retrying in %.2fs", backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2.0, 2.0)
                continue
            backoff = 0.1
            self._seq += 1
            self.queue.put(Frame(image=img, timestamp_ms=now_ms(), seq=self._seq))


class UsbWebcam(FrameSource):
    capture_kind = "device"

    def _pipeline(self) -> Union[str, int]:
        try:
            return int(self.cfg.device)
        except ValueError:
            return self.cfg.device


class CsiCamera(FrameSource):
    def _pipeline(self) -> str:
        sensor_id = int(self.cfg.device) if str(self.cfg.device).isdigit() else 0
        return (
            f"nvarguscamerasrc sensor-id={sensor_id} ! "
            f"video/x-raw(memory:NVMM),width={self.cfg.width},height={self.cfg.height},"
            f"framerate={self.cfg.fps}/1,format=NV12 ! "
            f"nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! "
            f"video/x-raw,format=BGR ! appsink drop=true max-buffers=1"
        )


class RtspStream(FrameSource):
    def _pipeline(self) -> str:
        return (
            f"rtspsrc location={self.cfg.device} latency=100 ! "
            f"rtph264depay ! h264parse ! nvv4l2decoder ! "
            f"nvvidconv ! video/x-raw,format=BGRx ! videoconvert ! "
            f"video/x-raw,format=BGR ! appsink drop=true max-buffers=1"
        )


class FileSource(FrameSource):
    """For testing: read frames from a video file."""

    def _pipeline(self) -> str:
        return self.cfg.device


def build_source(cfg: CameraConfig) -> FrameSource:
    """Construct the appropriate :class:`FrameSource` for ``cfg.source``."""
    source = (cfg.source or "usb").lower()
    if source == "usb":
        return UsbWebcam(cfg)
    if source == "csi":
        return CsiCamera(cfg)
    if source == "rtsp":
        return RtspStream(cfg)
    if source == "file":
        return FileSource(cfg)
    raise ValueError(f"Unknown camera source: {cfg.source!r}")
