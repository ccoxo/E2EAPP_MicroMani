from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.core.logging import LogService
from backend.hal_client.client import RealHalClient
from backend.hal_client.dds_client import DdsHalClient
from backend.hal_client.dds_types import TOPIC_HAL_HEALTH, JsonEnvelope
from backend.tests.test_hal_dds_client import FakeDdsTransport


@pytest.mark.parametrize("transport_kind", ["http", "dds"])
@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({}, None),
        ({"capabilities": []}, []),
        ({"capabilities": ["force_calibration_state_v1"]}, ["force_calibration_state_v1"]),
        ({"capabilities": "force_calibration_state_v1"}, None),
    ],
)
def test_health_preserves_reported_capabilities_without_inference(
    monkeypatch: pytest.MonkeyPatch,
    transport_kind: str,
    metadata: dict[str, Any],
    expected: list[str] | None,
) -> None:
    # 版本号不能替代能力声明；旧版本缺字段与新版本空列表都不能虚报校准能力。
    payload = {
        "ltdmc_ok": True,
        "omega7_ok": False,
        "version": "hal-real/0.2",
        "uptime_s": 7.5,
        **metadata,
    }
    logs = LogService(emit_startup=False)
    if transport_kind == "http":
        client = RealHalClient("http://127.0.0.1:8091", 500, logs)

        async def request(method: str, path: str) -> dict[str, Any]:
            assert (method, path) == ("GET", "/health")
            return payload

        monkeypatch.setattr(client, "_request", request)
    else:
        monkeypatch.setattr("backend.hal_client.dds_client.now_unix_ms", lambda: 123)
        transport = FakeDdsTransport()
        transport.latest[TOPIC_HAL_HEALTH] = JsonEnvelope(
            stamp_unix_ms=123,
            stamp_monotonic_ms=456,
            source="hal",
            payload_json=json.dumps(payload),
        )
        client = DdsHalClient(logs, transport=transport)

    try:
        health = asyncio.run(client.health())
    finally:
        if isinstance(client, DdsHalClient):
            client.close()

    assert health.connected is True
    assert health.version == "hal-real/0.2"
    assert health.ltdmc_ok is True
    assert health.omega7_ok is False
    assert health.uptime_s == 7.5
    assert health.capabilities == expected
