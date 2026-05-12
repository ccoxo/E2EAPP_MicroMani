from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any


@dataclass
class GripperResult:
    ok: bool
    message: str
    position_mm: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


class Rs485GripperDriver:
    """Talks to the Jodell EPG006 grippers over RS485.

    The vendor DLL keeps the active serial port internally. All device commands
    only receive a slave id, so every operation must explicitly select the COM
    port before talking to the gripper. Keeping multiple ports cached as "open"
    is unsafe because a later command can silently go to whichever port the DLL
    selected most recently.
    """

    def __init__(self) -> None:
        self._dll: ctypes.WinDLL | None = None
        self._dll_path: str | None = None
        self._io_lock = Lock()
        self._active_port: int | None = None
        self._active_baudrate: int | None = None

    def probe(self, config: dict[str, Any]) -> GripperResult:
        try:
            dll = self._load_dll(config)
        except Exception as exc:
            return GripperResult(False, f"jodellTool.dll load failed: {exc}")

        errors: list[str] = []
        gripper = config["gripper"]
        baudrate = int(gripper.get("baudrate", 115200))
        with self._io_lock:
            for side, port_key in [
                ("left", "leftPort"),
                ("right", "rightPort"),
            ]:
                port = self._port_number(str(gripper.get(port_key)))
                ret = self._select_port(dll, port, baudrate)
                if ret not in {0, 1}:
                    errors.append(f"{side} COM{port}: serialOperation open ret={ret}")
        if errors:
            return GripperResult(False, "; ".join(errors))
        return GripperResult(True, "jodell RS485 gripper ports open")

    def command(self, config: dict[str, Any], side: str, command: str, target_mm: float | None) -> GripperResult:
        gripper = config["gripper"]
        if command not in {"enable", "disable", "stop"} and not bool(gripper.get(f"{side}Enabled", False)):
            return GripperResult(False, f"{side} gripper is disabled; enable it before motion commands", target_mm)
        try:
            dll = self._load_dll(config)
        except Exception as exc:
            return GripperResult(False, f"jodellTool.dll load failed: {exc}", target_mm)

        port = self._port_number(str(gripper["leftPort" if side == "left" else "rightPort"]))
        slave = int(gripper["leftSlaveId" if side == "left" else "rightSlaveId"])
        baudrate = int(gripper.get("baudrate", 115200))

        with self._io_lock:
            ret_open = self._select_port(dll, port, baudrate)
            if ret_open not in {0, 1}:
                return GripperResult(
                    False,
                    f"motion prepare COM{port}, slave={slave}, open={ret_open}",
                    target_mm,
                )
            close_after = False
            try:
                if command == "enable":
                    ret_enable = int(dll.clawEnable(slave, True))
                    return GripperResult(
                        ret_enable in {0, 1},
                        f"enable COM{port}, slave={slave}, enable={ret_enable}",
                    )
                if command == "disable":
                    ret_disable = int(dll.clawEnable(slave, False))
                    close_after = True
                    return GripperResult(
                        ret_disable in {0, 1},
                        f"disable COM{port}, slave={slave}, disable={ret_disable}",
                    )
                if command == "home":
                    ret = int(dll.clawEncoderZero(slave))
                    return GripperResult(
                        ret in {0, 1},
                        f"encoder zero COM{port}, slave={slave}, ret={ret}",
                        0.0,
                    )
                if command == "stop":
                    ret = int(dll.runWithoutParam(slave, 0))
                    return GripperResult(
                        ret in {0, 1},
                        f"stop COM{port}, slave={slave}, ret={ret}",
                        target_mm,
                    )

                ret_enable = int(dll.clawEnable(slave, True))
                if ret_enable not in {0, 1}:
                    return GripperResult(
                        False,
                        f"motion prepare COM{port}, slave={slave}, enable={ret_enable}",
                        target_mm,
                    )
                pos, speed, torque, position_mm = self._motion_params(
                    command,
                    target_mm,
                    float(gripper.get("strokeMm", 26)),
                    int(gripper.get("commandSpeed", 10)),
                    int(gripper.get("commandTorque", 1)),
                )
                ret = int(dll.runWithParam(slave, pos, speed, torque))
                return GripperResult(
                    ret in {0, 1},
                    (
                        f"runWithParam COM{port}, slave={slave}, pos={pos}, "
                        f"speed={speed}, torque={torque}, ret={ret}"
                    ),
                    position_mm,
                )
            except OSError as exc:
                close_after = True
                return GripperResult(False, f"COM{port} bus error: {exc}", target_mm)
            finally:
                if close_after:
                    self._close_port(dll, port, baudrate)

    def position(self, config: dict[str, Any], side: str) -> GripperResult:
        try:
            dll = self._load_dll(config)
        except Exception as exc:
            return GripperResult(False, f"jodellTool.dll load failed: {exc}")

        gripper = config["gripper"]
        port = self._port_number(str(gripper["leftPort" if side == "left" else "rightPort"]))
        slave = int(gripper["leftSlaveId" if side == "left" else "rightSlaveId"])
        baudrate = int(gripper.get("baudrate", 115200))
        allow_sample_enable = bool(gripper.get(f"{side}Enabled", False)) and bool(
            gripper.get("sampleEnableOnNegative", True)
        )

        with self._io_lock:
            ret_open = self._select_port(dll, port, baudrate)
            if ret_open not in {0, 1}:
                return GripperResult(False, f"position prepare COM{port}, slave={slave}, open={ret_open}")
            try:
                raw = int(dll.getClawCurrentLocation(slave))
                if raw < 0:
                    if not allow_sample_enable:
                        return GripperResult(False, f"getClawCurrentLocation slave={slave}, ret={raw}")
                    enable_ret = int(dll.clawEnable(slave, True))
                    time.sleep(0.05)
                    raw = int(dll.getClawCurrentLocation(slave))
                    if raw < 0:
                        return GripperResult(
                            False,
                            f"getClawCurrentLocation slave={slave}, ret={raw}, enable={enable_ret}",
                        )
                stroke_mm = float(gripper.get("strokeMm", 26))
                position_mm = stroke_mm * (1.0 - min(raw, 255) / 255.0)
                return GripperResult(True, f"getClawCurrentLocation slave={slave}, raw={raw}", position_mm)
            except OSError as exc:
                return GripperResult(False, f"COM{port} bus error: {exc}")

    def diagnose(self, config: dict[str, Any], side: str) -> GripperResult:
        try:
            dll = self._load_dll(config)
        except Exception as exc:
            return GripperResult(False, f"jodellTool.dll load failed: {exc}", details={"stage": "load_dll"})

        gripper = config["gripper"]
        port = self._port_number(str(gripper["leftPort" if side == "left" else "rightPort"]))
        slave = int(gripper["leftSlaveId" if side == "left" else "rightSlaveId"])
        baudrate = int(gripper.get("baudrate", 115200))
        details: dict[str, Any] = {"side": side, "port": f"COM{port}", "slave": slave, "baudrate": baudrate}

        with self._io_lock:
            try:
                ret_open = self._select_port(dll, port, baudrate)
                details["openRet"] = ret_open
                if ret_open not in {0, 1}:
                    return GripperResult(False, f"diagnose COM{port}, slave={slave}, open={ret_open}", details=details)
                ret_enable = int(dll.clawEnable(slave, True))
                details["enableRet"] = ret_enable
                raw = int(dll.getClawCurrentLocation(slave)) if ret_enable in {0, 1} else -1
                details["positionRaw"] = raw
                stroke_mm = float(gripper.get("strokeMm", 26))
                position_mm = stroke_mm * (1.0 - min(max(raw, 0), 255) / 255.0) if raw >= 0 else None
                if raw < 0:
                    return GripperResult(
                        False,
                        f"diagnose COM{port}, slave={slave}, enable={ret_enable}, position={raw}",
                        position_mm,
                        details,
                    )
                return GripperResult(
                    ret_enable in {0, 1},
                    f"diagnose COM{port}, slave={slave}, open={ret_open}, enable={ret_enable}, raw={raw}",
                    position_mm,
                    details,
                )
            except OSError as exc:
                self._close_port(dll, port, baudrate)
                details["error"] = str(exc)
                return GripperResult(False, f"COM{port} bus error: {exc}", details=details)

    def _load_dll(self, config: dict[str, Any]) -> ctypes.WinDLL:
        configured = str(config["gripper"].get("jodellDllPath", "")).strip()
        candidates = []
        if configured:
            candidates.append(Path(configured))
        candidates.extend(
            [
                Path("jodellTool.dll"),
                Path("backend") / "vendor" / "jodell" / "jodellTool.dll",
                Path("hal") / "vendor" / "jodell" / "jodellTool.dll",
            ]
        )
        for candidate in candidates:
            if candidate.exists():
                path = str(candidate.resolve())
                if self._dll is None or self._dll_path != path:
                    self._dll = ctypes.WinDLL(path)
                    self._configure_symbols(self._dll)
                    self._dll_path = path
                return self._dll
        raise FileNotFoundError("jodellTool.dll not found; set gripper.jodellDllPath")

    def _configure_symbols(self, dll: ctypes.WinDLL) -> None:
        dll_any: Any = dll
        dll_any.serialOperation = self._dll_function(dll, "serialOperation", "?serialOperation@@YAHHH_N@Z")
        dll_any.serialOperation.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.BOOL]
        dll_any.serialOperation.restype = ctypes.c_int
        dll_any.clawEnable = self._dll_function(dll, "clawEnable", "?clawEnable@@YAHH_N@Z")
        dll_any.clawEnable.argtypes = [ctypes.c_int, wintypes.BOOL]
        dll_any.clawEnable.restype = ctypes.c_int
        dll_any.runWithParam = self._dll_function(dll, "runWithParam", "?runWithParam@@YAHHHHH@Z")
        dll_any.runWithParam.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        dll_any.runWithParam.restype = ctypes.c_int
        dll_any.runWithoutParam = self._dll_function(dll, "runWithoutParam", "?runWithoutParam@@YAHHH@Z")
        dll_any.runWithoutParam.argtypes = [ctypes.c_int, ctypes.c_int]
        dll_any.runWithoutParam.restype = ctypes.c_int
        dll_any.clawEncoderZero = self._dll_function(dll, "clawEncoderZero", "?clawEncoderZero@@YAHH@Z")
        dll_any.clawEncoderZero.argtypes = [ctypes.c_int]
        dll_any.clawEncoderZero.restype = ctypes.c_int
        dll_any.getClawCurrentLocation = self._dll_function(
            dll, "getClawCurrentLocation", "?getClawCurrentLocation@@YAHH@Z"
        )
        dll_any.getClawCurrentLocation.argtypes = [ctypes.c_int]
        dll_any.getClawCurrentLocation.restype = ctypes.c_int

    def _dll_function(self, dll: ctypes.WinDLL, public_name: str, decorated_name: str) -> Any:
        for name in (public_name, decorated_name):
            try:
                return getattr(dll, name)
            except AttributeError:
                continue
        raise AttributeError(f"{public_name} / {decorated_name} not found")

    def _select_port(self, dll: Any, port: int, baudrate: int) -> int:
        if self._active_port == port and self._active_baudrate == baudrate:
            return 0
        if self._active_port is not None and self._active_baudrate is not None:
            self._close_port(dll, self._active_port, self._active_baudrate)
        return self._open_port(dll, port, baudrate)

    def _open_port(self, dll: Any, port: int, baudrate: int) -> int:
        last_ret = -999
        for attempt in range(2):
            last_ret = int(dll.serialOperation(port, baudrate, True))
            if last_ret in {0, 1}:
                self._active_port = port
                self._active_baudrate = baudrate
                return last_ret
            try:
                dll.serialOperation(port, baudrate, False)
            except Exception:
                pass
            time.sleep(0.05 * (attempt + 1))
        return last_ret

    def _close_port(self, dll: Any, port: int, baudrate: int) -> None:
        try:
            dll.serialOperation(port, baudrate, False)
        except Exception:
            pass
        if self._active_port == port and self._active_baudrate == baudrate:
            self._active_port = None
            self._active_baudrate = None

    def _port_number(self, value: str) -> int:
        normalized = value.strip().upper()
        if normalized.startswith("COM"):
            normalized = normalized[3:]
        return int(normalized)

    def _motion_params(
        self,
        command: str,
        target_mm: float | None,
        stroke_mm: float,
        speed: int = 10,
        torque: int = 1,
    ) -> tuple[int, int, int, float]:
        safe_speed = min(max(int(speed), 1), 255)
        safe_torque = min(max(int(torque), 1), 255)
        if command == "open":
            return 0, safe_speed, safe_torque, stroke_mm
        if command == "close":
            return 255, safe_speed, safe_torque, 0.0
        if command == "target":
            bounded = min(max(float(target_mm if target_mm is not None else 0.0), 0.0), stroke_mm)
            pos = round((stroke_mm - bounded) / stroke_mm * 255)
            return pos, safe_speed, safe_torque, bounded
        raise ValueError(f"unsupported gripper command: {command}")
