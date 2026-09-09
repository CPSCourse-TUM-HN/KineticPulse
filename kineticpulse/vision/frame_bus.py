"""Single-slot bus carrying the newest annotated preview frame.

:mod:`kineticpulse.vision.preview` already draws everything a caregiver would
want to look at - the posture box, the skeleton, the motion signals and the
tier the fusion engine settled on. Until now the only way for that overlay to
leave the Jetson was a PNG on local disk (``--preview-snapshot``), which works
over SSH but is useless to the dashboard.

This module is the seam that lets :mod:`kineticpulse.monitoring.http` serve the
same overlay over HTTP. The preview encodes each annotated canvas to JPEG on
its own worker thread and publishes it here; the HTTP server reads the newest
one and hands it to the browser.

Two properties matter, and both come from what a live feed actually needs:

* **Latest-frame, not a queue.** A caregiver watching a feed wants the current
  frame, never a backlog. A slow or paused client must not be able to make the
  producer allocate, so a publish overwrites whatever was there and an
  unconsumed frame is simply lost.
* **Staleness is explicit.** A frozen image is the most dangerous thing this
  panel could show: it looks exactly like a calm room. Frames therefore carry
  the monotonic instant they were published, and :meth:`fresh` returns
  ``None`` once nothing has arrived for ``stale_after_s`` - which the HTTP
  layer turns into a 503 so the dashboard can say the feed stopped instead of
  rendering a lie.

Wall-clock is deliberately not used: frame timestamps come from the capture
clock (see :class:`kineticpulse.vision.capture.Frame`), which is not an epoch,
so freshness is measured with :func:`time.monotonic` at publish time.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

#: A feed that has not produced a frame for this long is reported stale rather
#: than served. Four seconds is several frames even on a badly degraded
#: pipeline (the CPU-fallback floor is ~0.65 FPS) while still being shorter
#: than a caregiver would take to notice a frozen picture themselves.
DEFAULT_STALE_AFTER_S = 4.0


@dataclass(frozen=True)
class PreviewFrame:
    """One encoded overlay frame, plus what the server needs to describe it."""

    jpeg: bytes
    #: Monotonically increasing; lets a stream skip re-sending the same frame.
    seq: int
    #: ``time.monotonic()`` when this frame was published.
    published_at: float
    #: Pipeline throughput when the frame was drawn, if the preview knew it.
    fps: Optional[float] = None

    def age_s(self, *, now: Optional[float] = None) -> float:
        return (time.monotonic() if now is None else now) - self.published_at


class PreviewFrameBus:
    """Thread-safe latest-frame slot shared by the preview and the HTTP server.

    The producer is the preview's encoder thread; the consumers are coroutines
    on the runtime's event loop. A plain lock is enough for that: the critical
    section is a single reference swap, and consumers poll rather than block so
    nothing on the event loop ever waits on a worker thread.
    """

    def __init__(self, *, stale_after_s: float = DEFAULT_STALE_AFTER_S) -> None:
        self.stale_after_s = float(stale_after_s)
        self._lock = threading.Lock()
        self._frame: Optional[PreviewFrame] = None
        self._seq = 0
        self._published = 0

    def publish(self, jpeg: bytes, *, fps: Optional[float] = None) -> PreviewFrame:
        """Replace the current frame. Called from the preview encoder thread."""
        with self._lock:
            self._seq += 1
            self._published += 1
            frame = PreviewFrame(
                jpeg=jpeg,
                seq=self._seq,
                published_at=time.monotonic(),
                fps=fps,
            )
            self._frame = frame
            return frame

    def latest(self) -> Optional[PreviewFrame]:
        """The newest frame, however old. ``None`` before the first publish."""
        with self._lock:
            return self._frame

    def fresh(self) -> Optional[PreviewFrame]:
        """The newest frame, or ``None`` if it is stale (or none exists yet)."""
        frame = self.latest()
        if frame is None:
            return None
        return None if frame.age_s() > self.stale_after_s else frame

    def clear(self) -> None:
        """Drop the current frame, so the feed reads as stopped immediately.

        Called when the preview shuts down: leaving the last drawn frame in
        place would keep the panel looking live after the pipeline stopped
        producing.
        """
        with self._lock:
            self._frame = None

    @property
    def published(self) -> int:
        """Total frames published since start - a liveness counter for logs."""
        with self._lock:
            return self._published

    def stats(self) -> Dict[str, object]:
        with self._lock:
            frame = self._frame
            return {
                "published": self._published,
                "seq": self._seq,
                "has_frame": frame is not None,
                "age_s": None if frame is None else round(frame.age_s(), 3),
                "bytes": None if frame is None else len(frame.jpeg),
            }
