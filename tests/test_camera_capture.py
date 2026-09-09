"""Regression tests for USB capture-format negotiation.

The bug these guard against is silent: a UVC webcam whose FOURCC is never
set, or set after the frame size, stays in uncompressed YUYV and the driver
caps 1280x720 at ~7-10 FPS. Nothing errors -- the fall detector just runs on
a third of the frames it was tuned for, and the temporal head's 30-frame
clips start spanning 3 s of real time instead of 1 s.
"""

from __future__ import annotations

from pathlib import Path

from kineticpulse.config import CameraConfig, load_config
from kineticpulse.vision.capture import open_device_capture


class FakeCapture:
    """Minimal ``cv2.VideoCapture`` stand-in that records ``set()`` order."""

    def __init__(self, negotiated_fourcc: str = "MJPG") -> None:
        self.calls: list[tuple[str, float]] = []
        self.props = {
            FakeCv2.CAP_PROP_FRAME_WIDTH: 1280.0,
            FakeCv2.CAP_PROP_FRAME_HEIGHT: 720.0,
            FakeCv2.CAP_PROP_FPS: 30.0,
            FakeCv2.CAP_PROP_FOURCC: float(FakeCv2.VideoWriter_fourcc(*negotiated_fourcc)),
            FakeCv2.CAP_PROP_BUFFERSIZE: 4.0,
        }
        self.released = False

    def isOpened(self) -> bool:
        return True

    def set(self, prop: int, value: float) -> bool:
        self.calls.append((FakeCv2.PROP_NAMES[prop], value))
        self.props[prop] = value
        return True

    def get(self, prop: int) -> float:
        return self.props.get(prop, 0.0)

    def read(self):
        return True, object()

    def release(self) -> None:
        self.released = True


class FakeCv2:
    CAP_V4L2 = 200
    CAP_ANY = 0
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5
    CAP_PROP_FOURCC = 6
    CAP_PROP_BUFFERSIZE = 38
    PROP_NAMES = {
        CAP_PROP_FRAME_WIDTH: "width",
        CAP_PROP_FRAME_HEIGHT: "height",
        CAP_PROP_FPS: "fps",
        CAP_PROP_FOURCC: "fourcc",
        CAP_PROP_BUFFERSIZE: "buffersize",
    }

    def __init__(self, negotiated_fourcc: str = "MJPG") -> None:
        self.capture = FakeCapture(negotiated_fourcc)
        self.opened_with: list[tuple[object, int]] = []

    def VideoCapture(self, device, backend):  # noqa: N802 - mirrors the cv2 API
        self.opened_with.append((device, backend))
        return self.capture

    @staticmethod
    def VideoWriter_fourcc(*chars) -> int:  # noqa: N802 - mirrors the cv2 API
        code = 0
        for i, ch in enumerate(chars):
            code |= ord(ch) << (8 * i)
        return code


def test_fourcc_is_set_before_the_frame_size() -> None:
    """V4L2 resolves the pixel format on the first set(); order is the bug."""
    cv2 = FakeCv2()
    cap = open_device_capture(cv2, 0, CameraConfig(width=1280, height=720, fourcc="MJPG"))

    assert cap is cv2.capture
    order = [name for name, _ in cap.calls]
    assert order.index("fourcc") < order.index("width")
    assert order.index("fourcc") < order.index("height")
    assert ("fourcc", float(FakeCv2.VideoWriter_fourcc(*"MJPG"))) in cap.calls


def test_v4l2_backend_is_preferred_and_driver_buffer_is_shallow() -> None:
    cv2 = FakeCv2()
    open_device_capture(cv2, 0, CameraConfig())

    assert cv2.opened_with[0][1] == FakeCv2.CAP_V4L2
    # 2, not 1: a single buffer stops the driver pipelining and halves the
    # frame rate; 2 keeps throughput while halving default staleness.
    assert ("buffersize", 2) in cv2.capture.calls


def test_blank_fourcc_leaves_the_format_to_the_driver() -> None:
    """CSI/RTSP rigs and raw-YUYV setups must be able to opt out."""
    cv2 = FakeCv2(negotiated_fourcc="YUYV")
    open_device_capture(cv2, 0, CameraConfig(fourcc=""))

    assert [name for name, _ in cv2.capture.calls].count("fourcc") == 0


def test_camera_fourcc_round_trips_through_yaml(tmp_path: Path) -> None:
    cfg_path = tmp_path / "cfg.yaml"
    cfg_path.write_text(
        """
camera:
  source: usb
  device: "0"
  width: 1280
  height: 720
  fps: 30
  fourcc: MJPG
""".strip(),
        encoding="utf-8",
    )

    assert load_config(cfg_path).camera.fourcc == "MJPG"


def test_camera_fourcc_defaults_to_mjpg() -> None:
    assert CameraConfig().fourcc == "MJPG"
