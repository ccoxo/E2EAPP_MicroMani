from __future__ import annotations

import asyncio
import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from backend.core.logging import LogService


@dataclass
class HalHealth:
    ltdmc_ok: bool
    omega7_ok: bool
    version: str
    uptime_s: float
    connected: bool = True
    mode: str = "real"
    message: str | None = None


class HalClient:
    async def health(self) -> HalHealth:
        raise NotImplementedError

    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        raise NotImplementedError

    async def motion_state(self) -> dict[str, Any]:
        raise NotImplementedError

    async def omega_state(self) -> dict[str, Any]:
        raise NotImplementedError


class TestHalClient(HalClient):
    def __init__(self, logs: LogService) -> None:
        self.logs = logs
        self.uptime_s = 0.0
        self.motion_enabled = {"left": False, "right": False}

    async def health(self) -> HalHealth:
        # 测试 HAL 保持接口可用，但明确标记硬件 SDK 未加载。
        self.uptime_s += 0.02
        return HalHealth(
            ltdmc_ok=False,
            omega7_ok=False,
            version="test-hal/0.1",
            uptime_s=self.uptime_s,
            connected=True,
            mode="test",
            message="hardware SDKs are not loaded in test fixture mode",
        )

    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        # 本地演示模式只记录命令，不把任何动作发往真实硬件。
        if name == "motion.enable_side":
            side = str((payload or {}).get("side", ""))
            if side in self.motion_enabled:
                self.motion_enabled[side] = True
        if name == "motion.disable_side":
            side = str((payload or {}).get("side", ""))
            if side in self.motion_enabled:
                self.motion_enabled[side] = False
        self.logs.info("[HAL]", f"{name} accepted by TestHalClient")
        return {"mode": "test", "command": name, "payload": payload or {}}

    async def motion_state(self) -> dict[str, Any]:
        now_unix_ms = int(time.time() * 1000)
        now_monotonic_ms = int(time.monotonic() * 1000)
        return {
            "timestamp_ms": now_unix_ms,
            "received_timestamp_ms": now_unix_ms,
            "received_monotonic_ms": now_monotonic_ms,
            "estop_active": False,
            "positions": [0.0] * 12,
            "pulses": [0.0] * 12,
            "enabled": [self.motion_enabled["left"]] * 6 + [self.motion_enabled["right"]] * 6,
        }

    async def omega_state(self) -> dict[str, Any]:
        now_unix_ms = int(time.time() * 1000)
        now_monotonic_ms = int(time.monotonic() * 1000)
        return {
            "timestamp_ms": now_unix_ms,
            "received_timestamp_ms": now_unix_ms,
            "received_monotonic_ms": now_monotonic_ms,
            "hands": [
                {
                    "side": "left",
                    "connected": False,
                    "calibrated": False,
                    "openId": 0,
                    "deviceId": -1,
                    "serial": "",
                    "systemName": "test Omega.7",
                    "leftHanded": None,
                    "pose": [0.0] * 6,
                    "clutchPressed": False,
                    "gripperPressed": False,
                    "gripperGapMm": None,
                    "lastReadOk": False,
                    "message": "test fixture",
                },
                {
                    "side": "right",
                    "connected": False,
                    "calibrated": False,
                    "openId": 1,
                    "deviceId": -1,
                    "serial": "",
                    "systemName": "test Omega.7",
                    "leftHanded": None,
                    "pose": [0.0] * 6,
                    "clutchPressed": False,
                    "gripperPressed": False,
                    "gripperGapMm": None,
                    "lastReadOk": False,
                    "message": "test fixture",
                },
            ]
        }


class RealHalClient(HalClient):
    """带 keep-alive 的 HAL HTTP 客户端：每个工作线程复用自己的 TCP 连接。

    旧实现每次调用都会重建 urllib socket；在 Windows localhost 上会增加 2-5ms
    TCP 建连和服务端关闭连接的等待。WS 遥测循环每 20ms tick 会调用两次 HAL，
    因此这部分开销会显著拖慢事件循环。
    """

    def __init__(self, base_url: str, timeout_ms: int, logs: LogService) -> None:
        parsed = urlparse(base_url.rstrip("/"))
        self.host = parsed.hostname or "127.0.0.1"
        self.port = parsed.port or 8091
        self.base_url = f"http://{self.host}:{self.port}"
        self.timeout_s = max(timeout_ms, 1) / 1000
        self.logs = logs
        self._tls = threading.local()
        self._cmd_lock = threading.Lock()

    async def health(self) -> HalHealth:
        try:
            payload = await self._request("GET", "/health")
        except RuntimeError as exc:
            return HalHealth(
                ltdmc_ok=False,
                omega7_ok=False,
                version="real-hal/unavailable",
                uptime_s=0.0,
                connected=False,
                mode="real",
                message=str(exc),
            )
        return HalHealth(
            ltdmc_ok=bool(payload.get("ltdmc_ok", False)),
            omega7_ok=bool(payload.get("omega7_ok", False)),
            version=str(payload.get("version", "real-hal/unknown")),
            uptime_s=float(payload.get("uptime_s", 0.0)),
            connected=True,
            mode="real",
            message=payload.get("message") if isinstance(payload.get("message"), str) else None,
        )

    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        path_by_command = {
            "hal.reconnect": "/health",
            "motion.emergency_stop": "/motion/emergency_stop",
            "motion.home_all": "/motion/home_all",
            "motion.enable_side": "/motion/enable_side",
            "motion.disable_side": "/motion/disable_side",
            "motion.home_side": "/motion/home_side",
            "motion.manual_axis_move": "/motion/manual_axis_move",
            "motion.teleop_target_update": "/motion/teleop_target_update",
            "motion.teleop_stop_side": "/motion/teleop_stop_side",
        }
        path = path_by_command.get(name)
        if path is None:
            raise RuntimeError(f"Real HAL command is not mapped: {name}")
        method = "GET" if name == "hal.reconnect" else "POST"
        response = await self._request(method, path, payload or {})
        self.logs.info("[HAL]", f"{name} forwarded to real HAL")
        return {"mode": "real", "command": name, "response": response}

    async def motion_state(self) -> dict[str, Any]:
        return self._with_receive_timestamp(await self._request("GET", "/motion/state"))

    async def omega_state(self) -> dict[str, Any]:
        return self._with_receive_timestamp(await self._request("GET", "/omega/state"))

    def _with_receive_timestamp(self, payload: dict[str, Any]) -> dict[str, Any]:
        now_unix_ms = int(time.time() * 1000)
        payload.setdefault("timestamp_ms", now_unix_ms)
        payload["received_timestamp_ms"] = now_unix_ms
        payload["received_monotonic_ms"] = int(time.monotonic() * 1000)
        return payload

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self._request_sync, method, path, body)

    def _get_connection(self) -> http.client.HTTPConnection:
        conn: http.client.HTTPConnection | None = getattr(self._tls, "conn", None)
        if conn is not None:
            return conn
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout_s)
        # 每个工作线程复用自己的连接，避免跨线程共享 socket 带来的竞态。
        # 复用连接并开启 TCP_NODELAY，避免 localhost 上的小 JSON 包被 Nagle 延迟。
        try:
            conn.connect()
            conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            try:
                conn.close()
            except Exception:
                pass
            raise
        self._tls.conn = conn
        return conn

    def _drop_connection(self) -> None:
        conn: http.client.HTTPConnection | None = getattr(self._tls, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._tls.conn = None

    def _request_sync(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json", "Connection": "keep-alive"}

        # 首次失败通常来自服务端关闭了旧 keep-alive 连接；重建一次即可恢复。
        for attempt in range(2):  # 旧连接失效时只重试一次
            try:
                conn = self._get_connection()
                conn.request(method, path, body=data, headers=headers)
                response = conn.getresponse()
                raw = response.read().decode("utf-8")
                status = response.status
                # 服务端要求关闭连接时丢弃缓存，下次调用重新连接。
                if response.headers.get("Connection", "").lower() == "close":
                    self._drop_connection()
                if status >= 400:
                    message = raw
                    try:
                        parsed_error = json.loads(raw)
                        if isinstance(parsed_error, dict) and isinstance(parsed_error.get("message"), str):
                            message = parsed_error["message"]
                    except json.JSONDecodeError:
                        pass
                    raise RuntimeError(
                        f"HAL request failed: {self.base_url}{path}: HTTP {status}: {message}"
                    )
                if not raw:
                    return {}
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise RuntimeError("HAL response must be a JSON object")
                return parsed
            except (http.client.HTTPException, ConnectionError, OSError, TimeoutError) as exc:
                # 连接已失效，丢弃后用新 socket 重试一次。
                self._drop_connection()
                if attempt >= 1:
                    raise RuntimeError(f"HAL request failed: {self.base_url}{path}: {exc}") from exc
                time.sleep(0.005)
            except json.JSONDecodeError as exc:
                self._drop_connection()
                raise RuntimeError(f"HAL response not JSON: {self.base_url}{path}") from exc

        raise RuntimeError(f"HAL request failed: {self.base_url}{path}: exhausted retries")
