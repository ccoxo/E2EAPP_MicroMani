from __future__ import annotations

import asyncio
from types import SimpleNamespace

from backend.services.stability_monitor import StabilityMonitorService


def test_stability_monitor_reads_hkvl_state_without_nidaq() -> None:
    class FakeHal:
        async def force_state(self) -> dict[str, object]:
            return {
                "source": "hkvl_serial",
                "left": [1.0, -2.0, 0.5, 0.0, 0.0, 0.0],
                "right": [3.0, 0.0, -1.0, 0.0, 0.0, 0.0],
                "leftRightSkewMs": 2.0,
                "sides": {
                    "left": {"healthy": True, "sampleHz": 999.0, "crcErrors": 1},
                    "right": {"healthy": True, "sampleHz": 1001.0, "crcErrors": 2},
                },
            }

    class ForbiddenForce:
        def sample_window(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("HKVL stability monitor must not sample NI-DAQ")

    service = object.__new__(StabilityMonitorService)
    service.hal = FakeHal()
    service.hardware = SimpleNamespace(force=ForbiddenForce())
    service._status = service._empty_status()

    asyncio.run(
        service._sample_force(
            {"hal": {"mode": "real"}, "force": {"source": "hkvl_serial"}}
        )
    )

    force = service._status["force"]
    assert force["source"] == "hkvl_serial"
    assert force["okSamples"] == 1
    assert force["maxAbsLeftN"] == 2.0
    assert force["maxAbsRightN"] == 3.0
    assert force["leftSampleHz"] == 999.0
    assert force["rightCrcErrors"] == 2
