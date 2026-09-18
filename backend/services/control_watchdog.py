from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from contextvars import ContextVar
from typing import Any

request_control_session: ContextVar[str | None] = ContextVar("request_control_session", default=None)


@dataclass
class BrowserLease:
    session_id: str
    send: Callable[[dict[str, Any]], Awaitable[None]]
    connected_at: float
    challenge_id: str = ""
    challenge_deadline: float = 0.0
    last_challenge_at: float = float("-inf")
    last_response_at: float | None = None
    answered_challenge: str = ""
    leased_challenge: str = ""
    expired: bool = False
    sending: asyncio.Task[None] | None = None
    pending_messages: dict[str, dict[str, Any]] = field(default_factory=dict)


class ControlLeaseUnavailable(RuntimeError):
    pass


class ControlWatchdog:
    """主线程应答驱动的执行租约；不依赖遥测组帧或默认线程池。"""

    def __init__(
        self,
        hal: Any,
        logs: Any,
        invalidate: Callable[[], None],
        emergency_stop: Callable[[], Awaitable[Any]],
        *,
        clock: Callable[[], float] = time.monotonic,
        challenge_interval_s: float = 0.5,
        browser_timeout_s: float = 2.0,
        hal_timeout_ms: int = 2500,
    ) -> None:
        self.hal = hal
        self.logs = logs
        self.invalidate = invalidate
        self.emergency_stop = emergency_stop
        self.clock = clock
        self.challenge_interval_s = challenge_interval_s
        self.browser_timeout_s = browser_timeout_s
        self.hal_timeout_ms = hal_timeout_ms
        self.session_id = uuid.uuid4().hex
        self.clients: dict[str, BrowserLease] = {}
        self.last_browser_session: str | None = None
        self._sequence = 0
        self._generation = 0
        self._next_renew_at = 0.0
        self._task: asyncio.Task[None] | None = None
        self._stop_task: asyncio.Task[None] | None = None
        self._stop_confirmed = True
        self._tripped = False
        self._closed = False
        self._confirmed_until = 0.0
        self._confirmed_generation = -1
        self._send_tasks: set[asyncio.Task[None]] = set()

    def require_ready(self) -> None:
        now = self.clock()
        session = request_control_session.get()
        if session is not None and session not in self.clients:
            raise ControlLeaseUnavailable("request does not own the current browser control session")
        if (
            self._closed or self._tripped or not self._healthy(now)
            or self._confirmed_until <= now or self._confirmed_generation != self._generation
        ):
            raise ControlLeaseUnavailable("fresh browser heartbeat and confirmed HAL control lease are required")

    def require_ack_ready(self) -> None:
        self.require_ready()
        if not self._stop_confirmed or (self._stop_task is not None and not self._stop_task.done()):
            raise ControlLeaseUnavailable("fail-safe stop is still pending")

    def confirm_stop(self) -> None:
        self._stop_confirmed = True

    def register(self, send: Callable[[dict[str, Any]], Awaitable[None]]) -> str:
        if self._closed:
            raise ControlLeaseUnavailable("control watchdog is closed")
        if self.clients:
            raise ControlLeaseUnavailable("another browser already owns control; use observer mode")
        session_id = uuid.uuid4().hex
        self.last_browser_session = session_id
        self.clients[session_id] = BrowserLease(session_id, send, self.clock())
        self._generation += 1
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="control-watchdog")
        return session_id

    def remove(self, session_id: str) -> None:
        client = self.clients.pop(session_id, None)
        if client is None:
            return
        if client.sending is not None:
            client.sending.cancel()
        if not client.expired:
            self.trip("browser WebSocket disconnected")

    def respond(self, session_id: str, payload: dict[str, Any]) -> bool:
        client = self.clients.get(session_id)
        if client is None or client.expired:
            return False
        if not client.challenge_id or payload.get("sessionId") != session_id or payload.get("challengeId") != client.challenge_id:
            return False
        now = self.clock()
        last_alive = client.last_response_at if client.last_response_at is not None else client.connected_at
        if (
            not client.challenge_id or now >= client.challenge_deadline
            or now - last_alive >= self.browser_timeout_s
        ):
            self.trip("browser challenge response expired")
            return False
        client.last_response_at = now
        client.answered_challenge = client.challenge_id
        client.challenge_id = ""
        return True

    def trip(self, reason: str) -> None:
        self._generation += 1
        self._confirmed_until = 0.0
        self._next_renew_at = 0.0
        for client in self.clients.values():
            client.expired = True
            self._send_status(client, "expired")
        # 旧会话不再参与健康判定，迟到断开/应答不能影响新会话。
        self.clients.clear()
        if self._tripped:
            return
        self._tripped = True
        self._stop_confirmed = False
        # 同步失效旧操作和策略队列，之后才异步发送硬件急停。
        self.invalidate()
        self.logs.error("[SAFETY]", f"control lease lost: {reason}")
        if self._stop_task is None or self._stop_task.done():
            self._stop_task = asyncio.create_task(self._stop_hardware(), name="control-watchdog-emergency")

    async def _stop_hardware(self) -> None:
        for attempt in range(2):
            try:
                await self.emergency_stop()
                self.confirm_stop()
                return
            except Exception as exc:
                self.logs.error("[SAFETY]", f"watchdog emergency stop unconfirmed; HAL lease must expire: {exc}")
                # 原生调用被隔离时不能释放占用或另起线程；仅重试已返回的幂等停止。
                if bool(getattr(self.hal, "_control_transport_failed", False)) or attempt == 1:
                    return
                await asyncio.sleep(0.1)

    def _healthy(self, now: float) -> bool:
        return bool(self.clients) and all(
            not client.expired and client.last_response_at is not None
            and now - client.last_response_at < self.browser_timeout_s
            for client in self.clients.values()
        )

    def _send(self, client: BrowserLease, message: dict[str, Any]) -> None:
        client.pending_messages[message["type"]] = message
        if client.sending is not None and not client.sending.done():
            return  # 最多合并一条挑战和一条状态，禁止慢客户端积累无界队列。

        async def send() -> None:
            try:
                while client.pending_messages:
                    key = next(iter(client.pending_messages))
                    current = client.pending_messages.pop(key)
                    await asyncio.wait_for(client.send(current), 0.5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                client.pending_messages.clear()
                if self.clients.get(client.session_id) is client and not client.expired:
                    self.trip(f"browser heartbeat send failed: {exc}")

        client.sending = asyncio.create_task(send(), name="control-watchdog-send")
        self._send_tasks.add(client.sending)
        client.sending.add_done_callback(self._send_tasks.discard)

    def _send_status(self, client: BrowserLease, status: str, challenge_id: str | None = None) -> None:
        self._send(client, {"type": "control_lease", "data": {
            "sessionId": client.session_id,
            "challengeId": client.answered_challenge if challenge_id is None else challenge_id,
            "status": status, "ttlMs": self.hal_timeout_ms,
        }})

    async def cycle(self) -> None:
        now = self.clock()
        for client in list(self.clients.values()):
            last_alive = client.last_response_at if client.last_response_at is not None else client.connected_at
            if not client.expired and now - last_alive >= self.browser_timeout_s:
                self.trip("browser main thread heartbeat timed out")
                break
            if client.expired:
                continue
            if not client.challenge_id and now - client.last_challenge_at >= self.challenge_interval_s:
                client.challenge_id = uuid.uuid4().hex
                client.last_challenge_at = now
                client.challenge_deadline = now + self.browser_timeout_s
                self._send(client, {"type": "safety_challenge", "data": {
                    "sessionId": client.session_id, "challengeId": client.challenge_id,
                    "ttlMs": int(self.browser_timeout_s * 1000),
                }})
        if not self._healthy(now) or now < self._next_renew_at:
            return
        if any(client.answered_challenge == client.leased_challenge for client in self.clients.values()):
            # 每次 HAL 续租必须消费新的主线程应答，不能靠旧的仍未过期应答续命。
            return
        generation = self._generation
        renewing_challenges = {key: client.answered_challenge for key, client in self.clients.items()}
        self._sequence += 1
        try:
            result = await asyncio.wait_for(self.hal.command("control.lease", {
                "sessionId": self.session_id,
                "sequence": self._sequence,
                "timeoutMs": self.hal_timeout_ms,
                "issuedAtUnixMs": int(time.time() * 1000),
            }), 0.75)
            response = result.get("response", {})
            if response.get("ok") is not True or response.get("leaseFresh") is not True:
                raise RuntimeError("HAL did not confirm a fresh control lease")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.trip(f"HAL control lease renewal failed: {exc}")
            return
        if generation != self._generation or not self._healthy(self.clock()):
            return
        self._next_renew_at = self.clock() + self.challenge_interval_s
        self._confirmed_until = now + self.hal_timeout_ms / 1000.0
        self._confirmed_generation = generation
        self._tripped = False
        for client in self.clients.values():
            client.leased_challenge = renewing_challenges[client.session_id]
            self._send_status(client, "active", client.leased_challenge)

    async def _run(self) -> None:
        try:
            while not self._closed:
                await self.cycle()
                await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.trip(f"control watchdog failed: {exc}")

    async def close(self) -> None:
        self._closed = True
        if self.clients:
            self.trip("backend control watchdog shutdown")
        for client in self.clients.values():
            if client.sending is not None:
                client.sending.cancel()
        self.clients.clear()
        sending = tuple(self._send_tasks)
        for task in sending:
            task.cancel()
        if sending:
            await asyncio.wait(sending, timeout=0.5)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._stop_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(self._stop_task), 1.0)
            except TimeoutError:
                self.logs.error("[SAFETY]", "shutdown stop remains unconfirmed; HAL lease renewal is disabled")
