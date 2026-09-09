"""Tests for scripts/export.py, focused on the INT8 calibration contract.

INT8 is the one export option that can quietly produce a *worse* model:
TensorRT needs real activations to pick per-tensor scales, and Ultralytics
falls back to downloading COCO8 when no dataset is given — calibrating a
fall-posture detector on generic COCO images. So ``--int8`` without
``--data`` must fail rather than succeed with wrong scales.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "kp_export", Path(__file__).resolve().parent.parent / "scripts" / "export.py"
)
export = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(export)


def _argv(monkeypatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["export.py", *args])


# --------------------------------------------------------------------------- #
# Format parsing
# --------------------------------------------------------------------------- #


def test_split_formats_accepts_a_comma_list() -> None:
    assert export.split_formats("onnx,engine") == ["onnx", "engine"]
    assert export.split_formats(" ONNX , engine ") == ["onnx", "engine"]


def test_split_formats_rejects_an_unknown_format() -> None:
    with pytest.raises(SystemExit, match="unsupported export formats"):
        export.split_formats("onnx,jetson-magic")


# --------------------------------------------------------------------------- #
# INT8 calibration contract
# --------------------------------------------------------------------------- #


def test_int8_without_data_is_refused(monkeypatch, capsys, tmp_path) -> None:
    weights = tmp_path / "best.pt"
    weights.write_bytes(b"stub")
    _argv(monkeypatch, "--weights", str(weights), "--format", "engine", "--int8")
    monkeypatch.setattr(export, "tensorrt_available", lambda: True)

    assert export.main() == 2
    assert "--int8 requires --data" in capsys.readouterr().err


def test_int8_with_a_missing_data_file_is_refused(monkeypatch, capsys, tmp_path) -> None:
    weights = tmp_path / "best.pt"
    weights.write_bytes(b"stub")
    _argv(
        monkeypatch, "--weights", str(weights), "--format", "engine",
        "--int8", "--data", str(tmp_path / "nope.yaml"),
    )
    monkeypatch.setattr(export, "tensorrt_available", lambda: True)

    assert export.main() == 2
    assert "calibration dataset not found" in capsys.readouterr().err


def test_int8_passes_the_calibration_dataset_through(monkeypatch, tmp_path) -> None:
    weights = tmp_path / "best.pt"
    weights.write_bytes(b"stub")
    data = tmp_path / "calib.yaml"
    data.write_text("path: .\ntrain: images\nval: images\n")
    _argv(
        monkeypatch, "--weights", str(weights), "--format", "engine",
        "--int8", "--data", str(data), "--fraction", "0.5", "--device", "0",
    )
    monkeypatch.setattr(export, "tensorrt_available", lambda: True)

    captured: dict = {}

    class _FakeYOLO:
        def __init__(self, _weights: str) -> None:
            pass

        def export(self, **kwargs):
            captured.update(kwargs)
            return str(tmp_path / "best.engine")

    import ultralytics

    monkeypatch.setattr(ultralytics, "YOLO", _FakeYOLO)

    assert export.main() == 0
    assert captured["int8"] is True
    assert captured["data"] == str(data)
    assert captured["fraction"] == 0.5
    assert "half" not in captured, "INT8 and FP16 are separate paths"


def test_fp16_export_needs_no_calibration_data(monkeypatch, tmp_path) -> None:
    """--half is data-free; only INT8 needs calibration."""
    weights = tmp_path / "best.pt"
    weights.write_bytes(b"stub")
    _argv(
        monkeypatch, "--weights", str(weights), "--format", "engine",
        "--half", "--device", "0",
    )
    monkeypatch.setattr(export, "tensorrt_available", lambda: True)

    captured: dict = {}

    class _FakeYOLO:
        def __init__(self, _weights: str) -> None:
            pass

        def export(self, **kwargs):
            captured.update(kwargs)
            return str(tmp_path / "best.engine")

    import ultralytics

    monkeypatch.setattr(ultralytics, "YOLO", _FakeYOLO)

    assert export.main() == 0
    assert captured["half"] is True
    assert "data" not in captured


def test_missing_weights_is_reported(monkeypatch, capsys, tmp_path) -> None:
    _argv(monkeypatch, "--weights", str(tmp_path / "absent.pt"), "--format", "onnx")
    assert export.main() == 2
    assert "weights not found" in capsys.readouterr().err
