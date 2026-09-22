import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.core.motion_safety import MotionSafetyGate
from backend.services import command_service


@pytest.mark.parametrize("error,moving,stamp,estop,accepted", [
    (0, False, 1000, False, True),
    (69, False, 1000, False, True),
    (-100, False, 1000, False, True),
    (100, False, 1000, False, True),
    (101, False, 1000, False, False),
    (69, True, 1000, False, False),
    (69, False, 0, False, False),
    (69, False, 1000, True, False),
])
def test_work_origin_confirmation(error, moving, stamp, estop, accepted, monkeypatch):
    async def exercise():
        service = object.__new__(command_service.CommandService)
        service.safety = MotionSafetyGate()
        service.hal = SimpleNamespace(motion_state=AsyncMock(return_value={
            "timestamp_ms": stamp, "pulses": [error] * 6 + [0] * 6,
            "moving": [moving] * 12, "estop_active": estop,
        }))
        clock = iter([0.0, 3.0])
        monkeypatch.setattr(command_service, "time", SimpleNamespace(monotonic=lambda: next(clock)))
        monkeypatch.setattr(command_service, "now_ms", lambda: 1000)
        operation = service._confirm_work_origin({"left": [0] * 6}, 900, service.safety.capture())
        if accepted:
            await operation
        else:
            with pytest.raises(RuntimeError, match="unconfirmed"):
                await operation

    asyncio.run(exercise())


def test_work_origin_tolerance_matches_native_driver():
    source = (Path(__file__).resolve().parents[2] / "hal/src/LTDMCDriver.cpp").read_text(encoding="utf-8")
    native = re.search(r"kWorkOriginSettledPulseTolerance\s*=\s*(\d+)", source)
    assert native is not None
    assert command_service.WORK_ORIGIN_SETTLED_PULSE_TOLERANCE == int(native.group(1))
