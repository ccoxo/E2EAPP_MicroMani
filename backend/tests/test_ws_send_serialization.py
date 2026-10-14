import asyncio

from starlette.websockets import WebSocketState

from backend.app import create_app


def test_control_lease_waits_for_pending_telemetry_send(tmp_path, monkeypatch):
    monkeypatch.setenv("APPSTATION_HAL_MODE", "test")
    app = create_app(tmp_path)
    app.state.telemetry.hardware = None

    async def exercise():
        telemetry_sending = asyncio.Event()
        release_telemetry = asyncio.Event()
        overlap = []
        lease_messages = []

        class Socket:
            client_state = WebSocketState.CONNECTED
            sending = False

            async def accept(self):
                pass

            async def receive_json(self):
                await asyncio.Event().wait()

            async def send_json(self, message):
                if self.sending:
                    overlap.append(message["type"])
                    raise AssertionError("concurrent socket drain")
                self.sending = True
                try:
                    if message["type"] == "telemetry" and not telemetry_sending.is_set():
                        telemetry_sending.set()
                        await release_telemetry.wait()
                    if message["type"] == "control_lease":
                        lease_messages.append(message["data"]["status"])
                finally:
                    self.sending = False

        endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/ws")
        task = asyncio.create_task(endpoint(Socket()))
        watchdog = app.state.control_watchdog
        try:
            await asyncio.wait_for(telemetry_sending.wait(), 1)
            client = next(iter(watchdog.clients.values()))
            watchdog._send_status(client, "active")
            await asyncio.sleep(.02)
            release_telemetry.set()
            await asyncio.sleep(.05)
            assert overlap == []
            assert "active" in lease_messages
            assert "expired" not in lease_messages
            assert client.session_id in watchdog.clients
            watchdog.require_ready()
        finally:
            release_telemetry.set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            await watchdog.close()
            app.state.telemetry.shutdown()

    asyncio.run(exercise())
