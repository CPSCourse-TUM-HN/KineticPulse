"""Tests for the live annotated preview.

The preview is cosmetic, but it sits inside the vision hot loop, so the
property that matters is that it *cannot* break the pipeline: every failure
path - no display, no GUI backend, a bad snapshot path, a GUI that dies
mid-run - has to degrade to "no preview" and let monitoring carry on.

Overlay drawing is verified by rendering to a file and checking the frame
came back annotated, which is also how the snapshot output is exercised
without needing a window.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from kineticpulse.vision.detector import Detection, PostureClass
from kineticpulse.vision.features import PoseFeatures
from kineticpulse.vision.pose import PoseResult
from kineticpulse.vision.preview import PreviewStats, PreviewWindow


def _frame(h: int = 240, w: int = 320) -> np.ndarray:
    return np.full((h, w, 3), 90, np.uint8)


def _pose() -> PoseResult:
    kpts = np.zeros((17, 3), np.float32)
    for i in range(17):
        kpts[i] = (40 + (i % 5) * 30, 40 + (i // 5) * 40, 0.9)
    return PoseResult(keypoints=kpts, bbox_xyxy=None, score=0.9, timestamp_ms=1)


def _snapshot():
    from kineticpulse.fusion.engine import FusionSnapshot
    from kineticpulse.fusion.rules import AccelSignature, HrSignature, PoseSignature
    from kineticpulse.fusion.tiers import EmergencyTier, TierDecision

    return FusionSnapshot(
        pose=PoseSignature.PRONE,
        accel=AccelSignature.IMPACT_TREMOR,
        hr=HrSignature.SEIZURE_SPIKE,
        decision=TierDecision(
            tier=EmergencyTier.TIER_2_SEIZURE,
            scenario="B",
            reason="Impact with rhythmic tremor and an extreme heart-rate spike.",
        ),
        latest_hr_bpm=170,
        latest_accel_g=4.6,
        detector_class="fallen",
        detector_conf=0.88,
        action_class="fall",
        action_conf=0.8,
        timestamp_ms=1,
    )


def _snapshot_preview(tmp_path, **kwargs) -> PreviewWindow:
    """A preview that writes to a file and never tries to open a window."""
    path = os.path.join(str(tmp_path), "live.png")
    preview = PreviewWindow(
        window=False, snapshot_path=path, snapshot_every=1, **kwargs
    )
    assert preview.open(), preview.disabled_reason
    return preview


# --------------------------------------------------------------------------- #
# Frame timing
# --------------------------------------------------------------------------- #


def test_stats_needs_two_frames_then_reports_fps() -> None:
    stats = PreviewStats()
    assert stats.tick(0) is None
    assert stats.tick(1000) == pytest.approx(1.0)
    assert stats.tick(1033) == pytest.approx(1.94, abs=0.05)


def test_stats_ignores_non_advancing_timestamps() -> None:
    """A repeated or backwards timestamp must not divide by zero."""
    stats = PreviewStats()
    stats.tick(5_000)
    assert stats.tick(5_000) is None
    assert stats.tick(4_000) is None


# --------------------------------------------------------------------------- #
# Snapshot output
# --------------------------------------------------------------------------- #


def test_snapshot_only_preview_annotates_and_writes(tmp_path) -> None:
    preview = _snapshot_preview(tmp_path)
    assert preview.active is True
    assert preview.window_open is False

    plain = _frame()
    assert preview.render(
        plain,
        timestamp_ms=0,
        detection=Detection(
            bbox_xyxy=(40.0, 30.0, 240.0, 210.0),
            cls=PostureClass.FALLEN,
            confidence=0.88,
            timestamp_ms=1,
        ),
        pose=_pose(),
        features=PoseFeatures(
            torso_angle_deg=81.0, aspect_ratio=1.9,
            centroid_vel_pps=1.2, stillness=0.05, timestamp_ms=1,
        ),
        snapshot=_snapshot(),
    ) is True

    assert preview.flush_snapshot(), "snapshot encode did not finish"
    written = os.path.join(str(tmp_path), "live.png")
    assert os.path.exists(written)

    import cv2

    out = cv2.imread(written)
    assert out is not None
    assert out.shape == plain.shape
    # The source frame is a flat grey; anything else proves the overlay drew.
    assert out.std() > 5.0
    # The temp file used for the atomic replace must not be left behind.
    assert not os.path.exists(os.path.join(str(tmp_path), "live.partial.png"))


def test_snapshot_honours_the_refresh_interval(tmp_path) -> None:
    path = os.path.join(str(tmp_path), "live.png")
    preview = PreviewWindow(window=False, snapshot_path=path, snapshot_every=3)
    assert preview.open()

    preview.render(_frame(), timestamp_ms=0)
    preview.render(_frame(), timestamp_ms=33)
    assert preview.flush_snapshot()
    assert not os.path.exists(path), "wrote before the interval elapsed"
    preview.render(_frame(), timestamp_ms=66)
    assert preview.flush_snapshot()
    assert os.path.exists(path)


def test_snapshot_path_without_an_extension_is_refused_not_raised(tmp_path) -> None:
    """imwrite picks its codec from the suffix, so a bare name cannot work.

    The pipeline must keep running and say why once, not crash per frame.
    """
    path = os.path.join(str(tmp_path), "live")
    preview = PreviewWindow(window=False, snapshot_path=path, snapshot_every=1)
    assert preview.open()
    assert preview.render(_frame(), timestamp_ms=0) is True
    preview.flush_snapshot()
    assert not os.path.exists(path)
    # Latched off, so a broken path cannot log once per frame forever.
    assert preview._snapshot_failed is True
    assert preview.render(_frame(), timestamp_ms=33) is True


def test_snapshot_accepts_jpg(tmp_path) -> None:
    path = os.path.join(str(tmp_path), "live.jpg")
    preview = PreviewWindow(window=False, snapshot_path=path, snapshot_every=1)
    assert preview.open()
    preview.render(_frame(), timestamp_ms=0)
    assert preview.flush_snapshot()
    assert os.path.exists(path)


def test_scale_shrinks_the_output(tmp_path) -> None:
    path = os.path.join(str(tmp_path), "live.png")
    preview = PreviewWindow(
        window=False, snapshot_path=path, snapshot_every=1, scale=0.5
    )
    assert preview.open()
    preview.render(_frame(h=240, w=320), timestamp_ms=0)
    assert preview.flush_snapshot()

    import cv2

    out = cv2.imread(path)
    assert out.shape[:2] == (120, 160)


# --------------------------------------------------------------------------- #
# Degradation - none of these may reach the caller as an exception
# --------------------------------------------------------------------------- #


def test_missing_inputs_render_without_raising(tmp_path) -> None:
    """Early frames have no detection, no pose, no fusion snapshot yet."""
    preview = _snapshot_preview(tmp_path)
    assert preview.render(_frame(), timestamp_ms=0) is True
    assert preview.render(
        _frame(), timestamp_ms=33, detection=None, pose=None,
        features=None, snapshot=None, simulation=None,
    ) is True


def test_partial_pose_and_detection_are_tolerated(tmp_path) -> None:
    preview = _snapshot_preview(tmp_path)
    short = PoseResult(
        keypoints=np.zeros((5, 3), np.float32), bbox_xyxy=None,
        score=0.5, timestamp_ms=1,
    )
    assert preview.render(_frame(), timestamp_ms=0, pose=short) is True

    class _BareDetection:
        bbox_xyxy = None

    assert preview.render(_frame(), timestamp_ms=33, detection=_BareDetection()) is True


def test_a_detection_at_the_frame_edge_keeps_its_label_inside(tmp_path) -> None:
    """A box in the corner would otherwise push its own caption out of frame."""
    preview = _snapshot_preview(tmp_path)
    edge = Detection(
        bbox_xyxy=(300.0, 0.0, 320.0, 240.0),      # hard against the right edge
        cls=PostureClass.FALLEN, confidence=0.9, timestamp_ms=1,
    )
    assert preview.render(_frame(), timestamp_ms=0, detection=edge) is True
    assert preview.flush_snapshot()

    import cv2

    out = cv2.imread(os.path.join(str(tmp_path), "live.png"))
    # The caption is white-on-colour; require ink in the last few columns'
    # neighbourhood rather than nothing at all.
    assert out[:, -60:, :].std() > 5.0


def test_no_display_and_no_snapshot_disables_the_preview(monkeypatch) -> None:
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    preview = PreviewWindow()
    assert preview.open() is False
    assert preview.active is False
    assert "DISPLAY" in (preview.disabled_reason or "")


def test_no_display_still_writes_snapshots(monkeypatch, tmp_path) -> None:
    """The Jetson is normally driven over SSH; the file must still appear."""
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    path = os.path.join(str(tmp_path), "live.png")
    preview = PreviewWindow(snapshot_path=path, snapshot_every=1)
    assert preview.open() is True
    assert preview.window_open is False
    preview.render(_frame(), timestamp_ms=0)
    assert preview.flush_snapshot()
    assert os.path.exists(path)


def test_requesting_neither_output_is_reported(tmp_path) -> None:
    preview = PreviewWindow(window=False)
    assert preview.open() is False
    assert preview.disabled_reason is not None


def test_a_render_failure_disables_the_preview_without_raising(tmp_path) -> None:
    preview = _snapshot_preview(tmp_path)

    class _Exploding:
        def __getattr__(self, name):
            raise RuntimeError("GUI went away")

    preview._cv2 = _Exploding()
    assert preview.render(_frame(), timestamp_ms=0) is True
    assert preview.active is False
    assert "render failed" in (preview.disabled_reason or "")


def test_close_is_safe_without_a_window(tmp_path) -> None:
    preview = _snapshot_preview(tmp_path)
    preview.close()
    assert preview.active is False
    PreviewWindow(window=False).close()      # never opened at all


def test_quit_key_stops_the_pipeline(tmp_path) -> None:
    """Pressing q in the window must ask the caller to shut down."""
    preview = _snapshot_preview(tmp_path)
    import cv2

    class _QuitOnKey:
        def __getattr__(self, name):
            return getattr(cv2, name)

        def imshow(self, *_args):
            return None

        def waitKey(self, _delay):
            return ord("q")

    preview._cv2 = _QuitOnKey()
    preview._window_open = True
    assert preview.render(_frame(), timestamp_ms=0) is False


# --------------------------------------------------------------------------- #
# Provenance overlay
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "simulation",
    [
        {"drill": True, "sensor_source": "mock", "ppg_source": "hardware",
         "scenario": "demo_trip_fall"},
        {"drill": False, "sensor_source": "mock", "ppg_source": "hardware",
         "scenario": "resting"},
        {"drill": False, "sensor_source": "hardware", "ppg_source": "simulated",
         "scenario": None},
        {"drill": False, "sensor_source": "hardware", "ppg_source": "hardware",
         "scenario": None},
    ],
)
def test_provenance_variants_render(tmp_path, simulation) -> None:
    """The camera can be real while the wristband numbers next to it are not,
    and the preview is where that would otherwise be invisible."""
    preview = _snapshot_preview(tmp_path)
    assert preview.render(
        _frame(), timestamp_ms=0, snapshot=_snapshot(), simulation=simulation
    ) is True


def test_a_clean_run_gets_no_provenance_band(tmp_path) -> None:
    """Real sensors, no drill: nothing to warn about, so nothing is drawn.

    Asserted on ``_draw_provenance`` directly rather than on two rendered
    frames, because the header's FPS readout legitimately differs between
    consecutive frames.
    """
    preview = _snapshot_preview(tmp_path)
    clean = {"drill": False, "sensor_source": "hardware",
             "ppg_source": "hardware", "scenario": None}

    for simulation in (None, {}, clean):
        canvas = _frame()
        before = canvas.copy()
        preview._draw_provenance(canvas, simulation)
        assert np.array_equal(before, canvas), simulation

    # ...and a drill does draw one.
    canvas = _frame()
    before = canvas.copy()
    preview._draw_provenance(canvas, {**clean, "drill": True,
                                      "scenario": "demo_trip_fall"})
    assert not np.array_equal(before, canvas)


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #


def test_build_preview_from_flags() -> None:
    import argparse

    from kineticpulse.main import _build_preview

    parsed = argparse.Namespace(
        preview=True, preview_scale=0.5, preview_snapshot="/tmp/x.png",
        preview_snapshot_every=7, no_preview_window=True,
    )
    preview = _build_preview(parsed)
    assert preview is not None
    assert preview.scale == 0.5
    assert preview.want_window is False
    assert preview.snapshot_path == "/tmp/x.png"
    assert preview.snapshot_every == 7

    # --preview-snapshot alone implies --preview.
    implied = _build_preview(argparse.Namespace(preview=False, preview_snapshot="/tmp/y.png"))
    assert implied is not None
    assert implied.want_window is True


def test_build_preview_tolerates_a_namespace_without_the_flags() -> None:
    """run() is also called programmatically with a hand-built Namespace.

    Such a caller has not opted into the preview and must not have to know
    the flags exist - see tests/test_pipeline_smoke.py.
    """
    import argparse

    from kineticpulse.main import _build_preview

    assert _build_preview(argparse.Namespace()) is None
    assert _build_preview(argparse.Namespace(no_camera=True, mock_ble=True)) is None


# --------------------------------------------------------------------------- #
# Accelerator guard
# --------------------------------------------------------------------------- #


def test_accelerator_warning_when_cuda_is_unavailable(monkeypatch, caplog) -> None:
    """The CPU fallback is silent and 7x slower - it must be shouted about.

    A generic PyPI torch wheel imports fine on a Jetson and reports
    ``cuda.is_available() == False`` in a buried UserWarning, so without this
    the only symptom is a pipeline that quietly misses falls.
    """
    import logging
    import sys
    import types

    from kineticpulse import main as kp_main

    fake = types.SimpleNamespace(
        __version__="2.12.0+cu130",
        __file__="/home/x/.local/lib/python3.10/site-packages/torch/__init__.py",
        version=types.SimpleNamespace(cuda="13.0"),
        cuda=types.SimpleNamespace(is_available=lambda: False),
    )
    monkeypatch.setitem(sys.modules, "torch", fake)

    with caplog.at_level(logging.WARNING, logger="kineticpulse.main"):
        kp_main._log_accelerator_status(no_camera=False)

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "CUDA IS UNAVAILABLE" in text
    assert "venv" in text                     # points at the actual cause
    assert "2.12.0+cu130" in text             # and at the offending wheel


def test_accelerator_reports_the_gpu_when_available(monkeypatch, caplog) -> None:
    import logging
    import sys
    import types

    from kineticpulse import main as kp_main

    fake = types.SimpleNamespace(
        __version__="2.9.1",
        version=types.SimpleNamespace(cuda="12.6"),
        cuda=types.SimpleNamespace(
            is_available=lambda: True,
            get_device_name=lambda _i: "Orin",
        ),
    )
    monkeypatch.setitem(sys.modules, "torch", fake)

    with caplog.at_level(logging.INFO, logger="kineticpulse.main"):
        kp_main._log_accelerator_status(no_camera=False)

    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "Orin" in text
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)


def test_accelerator_check_is_skipped_without_a_camera(monkeypatch, caplog) -> None:
    """--no-camera loads no vision models, so the warning would be noise."""
    import logging
    import sys
    import types

    from kineticpulse import main as kp_main

    monkeypatch.setitem(
        sys.modules, "torch",
        types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: False)),
    )
    with caplog.at_level(logging.INFO, logger="kineticpulse.main"):
        kp_main._log_accelerator_status(no_camera=True)
    assert caplog.records == []


def test_snapshot_encoding_does_not_block_the_render_path(tmp_path) -> None:
    """A full-res PNG encode is ~55 ms on an Orin Nano — longer than a frame
    budget. Inline it cost 6.4 FPS of a 17.4 FPS pipeline, so render() must
    hand the encode off and return immediately."""
    import threading
    import time

    preview = _snapshot_preview(tmp_path)
    release = threading.Event()
    real_cv2 = preview._cv2

    class _SlowWrite:
        def __getattr__(self, name):
            return getattr(real_cv2, name)

        def imwrite(self, path, image, *args):
            release.wait(5.0)
            return real_cv2.imwrite(path, image, *args)

    preview._cv2 = _SlowWrite()

    t0 = time.perf_counter()
    preview.render(_frame(), timestamp_ms=0)
    elapsed = time.perf_counter() - t0
    release.set()
    assert elapsed < 1.0, f"render blocked for {elapsed:.2f}s on the encode"
    assert preview.flush_snapshot()


def test_a_snapshot_is_dropped_rather_than_queued_behind_an_encode(tmp_path) -> None:
    """The file is a 'latest frame' view: stale beats stalling the pipeline."""
    import threading

    preview = _snapshot_preview(tmp_path)
    release = threading.Event()
    real_cv2 = preview._cv2

    class _SlowWrite:
        def __getattr__(self, name):
            return getattr(real_cv2, name)

        def imwrite(self, path, image, *args):
            release.wait(5.0)
            return real_cv2.imwrite(path, image, *args)

    preview._cv2 = _SlowWrite()

    preview.render(_frame(), timestamp_ms=0)        # starts the slow encode
    for i in range(1, 6):                           # these must not queue up
        preview.render(_frame(), timestamp_ms=33 * i)
    assert preview._snapshot_dropped == 5

    release.set()
    assert preview.flush_snapshot()


def test_close_waits_for_the_last_snapshot_and_releases_the_pool(tmp_path) -> None:
    preview = _snapshot_preview(tmp_path)
    preview.render(_frame(), timestamp_ms=0)
    preview.close()
    assert os.path.exists(os.path.join(str(tmp_path), "live.png"))
    assert preview._snapshot_pool is None
    # A render after close must not resurrect the pool or raise.
    assert preview.render(_frame(), timestamp_ms=33) is True
