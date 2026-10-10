import asyncio
from types import SimpleNamespace

import pytest

from backend.core.logging import LogService
from backend.hal_client.dds_client import DdsHalClient
from backend.tests.test_hal_dds_client import FakeDdsTransport


@pytest.mark.parametrize("command", ["control.lease", "motion.emergency_stop", "motion.enable_side"])
def test_late_dispatch_is_never_published_and_reports_elapsed(monkeypatch, command):
    async def exercise():
        clock = [100.0]
        monkeypatch.setattr("backend.hal_client.dds_client.time", SimpleNamespace(monotonic=lambda: clock[0]))
        transport = FakeDdsTransport()
        client = DdsHalClient(LogService(emit_startup=False), transport=transport)
        lane = (client._lease_lane if command == "control.lease" else
                client._emergency_lane if command == "motion.emergency_stop" else client._command_lane)

        async def delayed_run(fn, timeout_s):
            clock[0] += 10
            return fn()

        monkeypatch.setattr(lane, "run", delayed_run)
        try:
            with pytest.raises(RuntimeError, match="DDS request expired before publication") as caught:
                await client.command(command, {})
            detail = str(caught.value)
            assert f"command={command}" in detail
            assert "stage=dispatch" in detail
            assert "elapsed_ms=10000.0" in detail
            assert "request_id=" in detail
            assert transport.requests == []
            assert transport.emergency_requests == []
            assert transport.waits == []
        finally:
            await client.aclose()

    asyncio.run(exercise())
