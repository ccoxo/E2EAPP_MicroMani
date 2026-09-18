from __future__ import annotations

import time

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.hal_client.client import HalHealth


class FakeHal:
    def __init__(self, *, expire_health: bool = False, fail_motion: bool = False) -> None:
        self.expire_health = expire_health
        self.fail_motion = fail_motion
        self.health_reads = 0
        self.motion_reads = 0

    async def health(self) -> HalHealth:
        self.health_reads += 1
        connected = not self.expire_health or self.health_reads == 1
        return HalHealth(
            ltdmc_ok=connected, omega7_ok=False, version="fake-hal", uptime_s=1.0,
            connected=connected, mode="real",
            source_valid_until_ms=int(time.time() * 1000) + 100 if connected and self.expire_health else None,
        )

    async def motion_state(self) -> dict:
        self.motion_reads += 1
        if self.fail_motion and self.motion_reads > 1:
            raise RuntimeError("DDS topic source timestamp is stale")
        return {"positions": [0.0] * 12, "pulses": [0.0] * 12,
                "enabled": [False] * 12, "estop_active": False}

    async def force_state(self) -> dict:
        return {"source": "hkvl_serial", "left": [0.0] * 6, "right": [0.0] * 6,
                "sides": {"left": {"healthy": True}, "right": {"healthy": True}}}

    async def command(self, name: str, payload: dict | None = None) -> dict:
        return {"response": {"running": False}}


def next_frame(ws) -> dict:
    for _ in range(100):
        message = ws.receive_json()
        if message["type"] == "telemetry":
            return message["data"]
    raise AssertionError("WebSocket did not publish telemetry")


def make_client(tmp_path, monkeypatch, hal: FakeHal) -> TestClient:
    # 使用模拟硬件服务和替身 HAL，不运行连接设备的生命周期。
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_HEALTH_PERIOD_SEC", "60")
    monkeypatch.setenv("APPSTATION_HAL_STATE_PERIOD_SEC", "0.001")
    monkeypatch.setattr("backend.app.make_hal_client", lambda _config, _logs: hal)
    return TestClient(create_app(tmp_path))


def test_ws_health_cache_cannot_extend_dds_source_validity(tmp_path, monkeypatch) -> None:
    hal = FakeHal(expire_health=True)
    client = make_client(tmp_path, monkeypatch, hal)
    try:
        with client.websocket_connect("/ws?mode=observe") as ws:
            assert next_frame(ws)["halOk"] is True
            for _ in range(30):
                frame = next_frame(ws)
                if not frame["halOk"]:
                    break
            assert frame["halOk"] is False
            assert frame["processStatus"][0]["status"] == "error"
            assert hal.health_reads >= 2
    finally:
        client.app.state.telemetry.shutdown()


def test_ws_fresh_health_does_not_hide_stale_motion_state(tmp_path, monkeypatch) -> None:
    hal = FakeHal(fail_motion=True)
    client = make_client(tmp_path, monkeypatch, hal)
    try:
        with client.websocket_connect("/ws?mode=observe") as ws:
            assert next_frame(ws)["halOk"] is True
            assert next_frame(ws)["halOk"] is False
            assert hal.health_reads == 1
            assert hal.motion_reads >= 2
    finally:
        client.app.state.telemetry.shutdown()
