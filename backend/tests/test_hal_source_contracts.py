from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_hal_home_all_requires_work_origin_payload() -> None:
    source = (REPO_ROOT / "hal" / "src" / "HalServer.cpp").read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "jsonWorkOriginPulse(requestBody(request))" in normalized
    assert "home_all requires leftPulse[6] work origin payload" in source
    assert "home_all requires rightPulse[6] work origin payload" in source


def test_hal_teleop_target_update_uses_absolute_target_mode() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    normalized = " ".join(source.split())

    assert "const auto updateTargetPulse = static_cast<long>(std::llround(targetPulse));" in normalized
    assert "dmcUpdateTargetPosition(card, axisNo, updateTargetPulse, 1)" in normalized
