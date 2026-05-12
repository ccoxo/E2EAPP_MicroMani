from __future__ import annotations

import ctypes
import os
import queue
import time
from multiprocessing.queues import Queue
from typing import Any

from backend.drivers.gripper_rs485 import Rs485GripperDriver


def run_gripper_worker(
    side: str,
    config: dict[str, Any],
    command_queue: Queue,
    status_queue: Queue,
    response_queue: Queue,
    parent_pid: int | None = None,
) -> None:
    worker = _GripperWorker(side, config, command_queue, status_queue, response_queue, parent_pid)
    worker.run()


class _GripperWorker:
    def __init__(
        self,
        side: str,
        config: dict[str, Any],
        command_queue: Queue,
        status_queue: Queue,
        response_queue: Queue,
        parent_pid: int | None = None,
    ) -> None:
        self.side = side
        self.config = config
        self.command_queue = command_queue
        self.status_queue = status_queue
        self.response_queue = response_queue
        self.parent_pid = parent_pid
        self.driver = Rs485GripperDriver()
        gripper = config["gripper"]
        self.port = self.driver._port_number(str(gripper["leftPort" if side == "left" else "rightPort"]))
        self.slave = int(gripper["leftSlaveId" if side == "left" else "rightSlaveId"])
        self.baudrate = int(gripper.get("baudrate", 115200))
        self.stroke_mm = float(gripper.get("strokeMm", 26))
        self.sample_hz = max(1.0, float(gripper.get("sampleHz", 30)))
        self.enable_on_negative = bool(gripper.get("sampleEnableOnNegative", True))
        self.enabled = bool(gripper.get(f"{side}Enabled", False))
        self.dll: Any | None = None
        self.last_sample_perf = 0.0
        self.actual_hz = 0.0

    def run(self) -> None:
        interval = 1.0 / self.sample_hz
        next_sample = time.perf_counter()
        running = True
        self._publish_status(ok=False, message="worker starting")
        try:
            while running:
                if self._parent_exited():
                    self._publish_status(ok=False, message="parent process exited; worker stopping")
                    break
                running = self._drain_commands()
                now = time.perf_counter()
                if now >= next_sample:
                    self._sample_once()
                    next_sample = max(next_sample + interval, now + interval)
                else:
                    time.sleep(min(0.002, next_sample - now))
        finally:
            self._close_port()

    def _drain_commands(self) -> bool:
        running = True
        while True:
            try:
                message = self.command_queue.get_nowait()
            except queue.Empty:
                break
            if not isinstance(message, dict):
                continue
            if message.get("type") == "stop":
                self._send_response(message, True, "worker stopped")
                running = False
                continue
            if message.get("type") != "command":
                self._send_response(message, False, f"unsupported worker message: {message.get('type')}")
                continue
            self._handle_command(message)
        return running

    def _handle_command(self, message: dict[str, Any]) -> None:
        command = str(message.get("command", ""))
        target_mm = message.get("targetMm")
        start = time.perf_counter()
        try:
            if not self._ensure_open():
                self._send_response(message, False, f"COM{self.port} open failed")
                return
            ok, detail, position_mm = self._execute_command(command, target_mm)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            self._send_response(message, ok, detail, position_mm=position_mm, elapsedMs=elapsed_ms)
            self._publish_status(ok=ok, message=detail, position_mm=position_mm)
        except Exception as exc:  # noqa: BLE001 - isolate vendor DLL failures inside the worker.
            self._send_response(message, False, f"COM{self.port} command error: {exc}")
            self._publish_status(ok=False, message=str(exc))

    def _execute_command(self, command: str, target_mm: Any) -> tuple[bool, str, float | None]:
        assert self.dll is not None
        if command not in {"enable", "disable", "stop"} and not self.enabled:
            return False, f"{self.side} gripper is disabled; enable it before motion commands", None
        if command == "enable":
            ret = int(self.dll.clawEnable(self.slave, True))
            if ret in {0, 1}:
                self.enabled = True
            return ret in {0, 1}, f"enable COM{self.port}, slave={self.slave}, ret={ret}", None
        if command == "disable":
            ret = int(self.dll.clawEnable(self.slave, False))
            if ret in {0, 1}:
                self.enabled = False
            return ret in {0, 1}, f"disable COM{self.port}, slave={self.slave}, ret={ret}", None
        if command == "home":
            ret = int(self.dll.clawEncoderZero(self.slave))
            return ret in {0, 1}, f"encoder zero COM{self.port}, slave={self.slave}, ret={ret}", 0.0
        if command == "stop":
            ret = int(self.dll.runWithoutParam(self.slave, 0))
            return ret in {0, 1}, f"stop COM{self.port}, slave={self.slave}, ret={ret}", None
        ret_enable = int(self.dll.clawEnable(self.slave, True))
        if ret_enable not in {0, 1}:
            return False, f"motion prepare COM{self.port}, slave={self.slave}, enable={ret_enable}", None
        self.enabled = True
        pos, speed, torque, position_mm = self.driver._motion_params(
            command,
            float(target_mm) if target_mm is not None else None,
            self.stroke_mm,
            int(self.config["gripper"].get("commandSpeed", 10)),
            int(self.config["gripper"].get("commandTorque", 1)),
        )
        ret = int(self.dll.runWithParam(self.slave, pos, speed, torque))
        return (
            ret in {0, 1},
            f"runWithParam COM{self.port}, slave={self.slave}, pos={pos}, speed={speed}, torque={torque}, ret={ret}",
            position_mm,
        )

    def _sample_once(self) -> None:
        start = time.perf_counter()
        try:
            if not self._ensure_open():
                self._publish_status(ok=False, message=f"COM{self.port} open failed")
                return
            assert self.dll is not None
            raw = int(self.dll.getClawCurrentLocation(self.slave))
            enable_ret: int | None = None
            if raw < 0 and self.enable_on_negative and self.enabled:
                enable_ret = int(self.dll.clawEnable(self.slave, True))
                time.sleep(0.05)
                raw = int(self.dll.getClawCurrentLocation(self.slave))
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            if raw < 0:
                detail = f"getClawCurrentLocation slave={self.slave}, ret={raw}"
                if enable_ret is not None:
                    detail += f", enable={enable_ret}"
                self._publish_status(ok=False, message=detail, raw=raw, read_ms=elapsed_ms)
                return
            position_mm = self.stroke_mm * (1.0 - min(raw, 255) / 255.0)
            now = time.perf_counter()
            if self.last_sample_perf > 0:
                instant_hz = 1.0 / max(now - self.last_sample_perf, 0.001)
                self.actual_hz = instant_hz if self.actual_hz <= 0 else self.actual_hz * 0.8 + instant_hz * 0.2
            self.last_sample_perf = now
            self._publish_status(
                ok=True,
                message=f"getClawCurrentLocation slave={self.slave}, raw={raw}",
                raw=raw,
                position_mm=position_mm,
                read_ms=elapsed_ms,
            )
        except Exception as exc:  # noqa: BLE001 - keep the worker alive across transient serial failures.
            self._publish_status(ok=False, message=f"COM{self.port} sample error: {exc}")

    def _ensure_open(self) -> bool:
        if self.dll is None:
            self.dll = self.driver._load_dll(self.config)
        ret = int(self.driver._select_port(self.dll, self.port, self.baudrate))
        return ret in {0, 1}

    def _close_port(self) -> None:
        if self.dll is not None and self.driver._active_port is not None and self.driver._active_baudrate is not None:
            self.driver._close_port(self.dll, self.driver._active_port, self.driver._active_baudrate)

    def _parent_exited(self) -> bool:
        if self.parent_pid is None:
            return False
        if os.name == "nt":
            synchronize = 0x00100000
            wait_object_0 = 0x00000000
            handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, int(self.parent_pid))
            if not handle:
                return False
            try:
                result = ctypes.windll.kernel32.WaitForSingleObject(handle, 0)
                return result == wait_object_0
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        try:
            os.kill(int(self.parent_pid), 0)
        except OSError:
            return True
        return False

    def _publish_status(
        self,
        *,
        ok: bool,
        message: str,
        raw: int | None = None,
        position_mm: float | None = None,
        read_ms: float | None = None,
    ) -> None:
        payload = {
            "type": "sample",
            "side": self.side,
            "ok": ok,
            "message": message,
            "raw": raw,
            "positionMm": position_mm,
            "readMs": read_ms,
            "sampleHz": self.actual_hz,
            "tsMs": int(time.time() * 1000),
            "monotonicMs": int(time.monotonic() * 1000),
        }
        try:
            self.status_queue.put_nowait(payload)
        except queue.Full:
            try:
                self.status_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.status_queue.put_nowait(payload)
            except queue.Full:
                pass

    def _send_response(
        self,
        request: dict[str, Any],
        ok: bool,
        message: str,
        *,
        position_mm: float | None = None,
        elapsedMs: float | None = None,
    ) -> None:
        self.response_queue.put(
            {
                "type": "response",
                "id": request.get("id"),
                "side": self.side,
                "ok": ok,
                "message": message,
                "positionMm": position_mm,
                "elapsedMs": elapsedMs,
                "tsMs": int(time.time() * 1000),
            }
        )
