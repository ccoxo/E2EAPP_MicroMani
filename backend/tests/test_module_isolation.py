from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.websockets import WebSocketDisconnect, WebSocketState

from backend.app import create_app
from backend.hal_client.client import HalHealth


class CollectingSocket:
    def __init__(self, frame_limit: int = 3) -> None:
        self.client_state = WebSocketState.CONNECTED
        self.messages = []
        self.frame_limit = frame_limit

    async def accept(self) -> None:
        return None

    async def receive_json(self):
        await asyncio.Event().wait()

    async def send_json(self, message) -> None:
        self.messages.append(message)
        if len(self.frames) >= self.frame_limit:
            raise WebSocketDisconnect()

    @property
    def frames(self):
        return [message["data"] for message in self.messages if message["type"] == "telemetry"]


def isolated_app(tmp_path, monkeypatch):
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    monkeypatch.setenv("APPSTATION_WS_PERIOD_SEC", "0.001")
    monkeypatch.setenv("APPSTATION_HEALTH_PERIOD_SEC", "0")
    app = create_app(tmp_path)
    # 仅执行单个路由协程，不进入应用启动和硬件采样生命周期。
    app.state.telemetry.hardware = None
    return app


def websocket_route(app):
    return next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/ws")


@pytest.mark.parametrize("recovers", [False, True])
def test_gripper_status_failure_does_not_starve_other_telemetry_or_logs(tmp_path, monkeypatch, recovers) -> None:
    app = isolated_app(tmp_path, monkeypatch)
    config = app.state.settings.get_config()
    config["force"]["source"] = "hkvl_serial"
    app.state.settings.get_config = lambda: config
    monkeypatch.setenv("APPSTATION_HAL_MODE", "real")
    hal = app.state.commands.hal
    hal.health = AsyncMock(return_value=HalHealth(True, False, "offline-fixture", 0))
    hal.motion_state = AsyncMock(return_value={"positions": [2.0] * 12, "estop_active": True})
    hal.force_state = AsyncMock(return_value={
        "left": [1.0] * 6, "right": [2.0] * 6, "dangerIndex": 1.1,
        "sides": {"left": {"healthy": True}, "right": {"healthy": True}},
        "safety": {"latched": True, "reason": "force_trip"},
    })
    failed = RuntimeError("gripper status decode failed")
    recovered = {"ok": True, "positionMm": {"left": 2.0, "right": 3.0}}
    app.state.gripper_router.status = AsyncMock(side_effect=[failed, recovered, recovered] if recovers else failed)
    app.state.logs.info("[BACKEND]", "other modules remain observable")

    async def exercise() -> None:
        ws = CollectingSocket()
        try:
            await asyncio.wait_for(websocket_route(app)(ws), 1)
            assert len(ws.frames) == 3
            assert all(frame["halOk"] for frame in ws.frames)
            assert [frame["gripperStatus"]["ok"] for frame in ws.frames] == (
                [False, True, True] if recovers else [False, False, False]
            )
            assert all(frame["jointPositions"] == [2.0] * 12 for frame in ws.frames)
            assert all(frame["forceStatus"]["safety"]["latched"] is True for frame in ws.frames)
            assert all(frame["dangerIndex"] == 1.1 for frame in ws.frames)
            assert any(message["type"] == "log" for message in ws.messages)
            assert any(
                message["type"] == "log" and "gripper status decode failed" in message["data"]["msg"]
                for message in ws.messages
            )
            assert not app.state.ws_clients
        finally:
            app.state.telemetry.shutdown()

    asyncio.run(exercise())


@pytest.mark.parametrize("first_read_fails", [True, False])
def test_health_read_failure_keeps_socket_observable_without_stale_health(
    tmp_path, monkeypatch, first_read_fails: bool
) -> None:
    app = isolated_app(tmp_path, monkeypatch)
    healthy = HalHealth(False, False, "test", 0, connected=True, mode="test")
    results = [RuntimeError("health sample unavailable"), healthy, healthy] if first_read_fails else [
        healthy, RuntimeError("health sample unavailable"), healthy,
    ]
    app.state.commands.hal.health = AsyncMock(side_effect=results)

    async def exercise() -> None:
        ws = CollectingSocket()
        try:
            await asyncio.wait_for(websocket_route(app)(ws), 1)
            assert len(ws.frames) == 3
            expected = [False, True, True] if first_read_fails else [True, False, True]
            assert [frame["halOk"] for frame in ws.frames] == expected
            assert not app.state.ws_clients
        finally:
            app.state.telemetry.shutdown()
        assert not app.state.ws_clients

    asyncio.run(exercise())


@pytest.mark.parametrize("failed_component", ["background_task", "camera"])
def test_shutdown_failure_does_not_skip_remaining_resource_cleanup(tmp_path, monkeypatch, failed_component) -> None:
    app = isolated_app(tmp_path, monkeypatch)
    camera_close = Mock()
    telemetry_shutdown = Mock()
    app.state.hardware.cameras.close_all = camera_close
    original_shutdown = app.state.telemetry.shutdown
    app.state.telemetry.shutdown = telemetry_shutdown
    app.state.recorder.status = Mock(return_value={"active": False, "recording": False})

    async def exercise() -> None:
        if failed_component == "background_task":
            entered = asyncio.Event()

            async def failed_cancel_cleanup():
                try:
                    entered.set()
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    raise RuntimeError("background task cleanup failed") from None

            task = asyncio.create_task(failed_cancel_cleanup())
            app.state.teleop_background_tasks.add(task)
            await entered.wait()
        else:
            camera_close.side_effect = RuntimeError("camera release failed")
        try:
            await app.router.on_shutdown[0]()
            camera_close.assert_called_once()
            telemetry_shutdown.assert_called_once()
            assert not app.state.teleop_background_tasks
            assert any(
                entry.level == "ERROR" and "shutdown" in entry.msg and "failed" in entry.msg
                for entry in app.state.logs.list_entries()
            )
        finally:
            original_shutdown()

    asyncio.run(exercise())
