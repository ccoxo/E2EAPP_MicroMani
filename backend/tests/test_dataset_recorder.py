from __future__ import annotations

from pathlib import Path


def test_dataset_recorder_declares_and_writes_motion_pulses() -> None:
    source = (Path(__file__).resolve().parents[1] / "services" / "dataset_recorder.py").read_text(encoding="utf-8")

    assert "PULSE_FEATURE_NAMES" in source
    assert '"observation.pulses": motion_pulses' in source
    assert '"observation.pulses": pulses' in source
    assert '"observation.pulses": self._np_float32(frame["observation.pulses"])' in source
    assert '"observation.pulses": {"dtype": "float32", "shape": [12]' in source
    assert '"observation.pulses": {"dtype": "float32", "shape": (12,)' in source
