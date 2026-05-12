from __future__ import annotations

import asyncio
import copy
from typing import Any

from backend.core.config import SettingsService
from backend.core.logging import LogService
from backend.hal_client.client import HalClient
from backend.services.hardware_service import HardwareService


class SchmittGripper:
    """Single-side Schmitt-trigger state machine: maps Omega7 gap → open/close command.

    Normalises the measured gap_mm into [0, 1] using the configured min/max range
    (polarity flip is handled by swapping min/max in config). State only changes on
    threshold crossings, so commands are edge-only — never re-sent on every tick.
    """

    def __init__(self, side: str) -> None:
        self.side = side
        self._state: str = "open"
        self._post_close_countdown: int = 0
        self._object_detected: bool = False

    @property
    def state(self) -> str:
        return self._state

    @property
    def object_detected(self) -> bool:
        return self._object_detected

    def reset(self) -> None:
        self._state = "open"
        self._post_close_countdown = 0

    def set_object_detected(self, value: bool) -> None:
        self._object_detected = value

    def update(self, gap_mm: float, cfg: dict[str, Any]) -> str | None:
        side = self.side
        gap_min = float(cfg.get(f"{side}GapMinMm", 0.0))
        gap_max = float(cfg.get(f"{side}GapMaxMm", 50.0))
        open_thr = float(cfg.get("openThreshold", 0.30))
        close_thr = float(cfg.get("closeThreshold", 0.70))

        span = gap_max - gap_min
        if abs(span) < 1e-6:
            return None
        n = max(0.0, min(1.0, (gap_mm - gap_min) / span))

        if self._state == "open" and n >= close_thr:
            self._state = "closed"
            self._post_close_countdown = 30
            self._object_detected = False
            return "close"
        if self._state == "closed" and n <= open_thr:
            self._state = "open"
            self._post_close_countdown = 0
            return "open"
        return None

    def update_button(self, pressed: bool) -> str | None:
        if self._state == "open" and pressed:
            self._state = "closed"
            self._post_close_countdown = 30
            self._object_detected = False
            return "close"
        if self._state == "closed" and not pressed:
            self._state = "open"
            self._post_close_countdown = 0
            return "open"
        return None

    def tick_object_detect(self) -> bool:
        """Decrement post-close countdown. Returns True exactly when it reaches zero."""
        if self._post_close_countdown > 0:
            self._post_close_countdown -= 1
            return self._post_close_countdown == 0
        return False


class GripperTeleService:
    """Background asyncio task that maps Omega7 gripper gap → Jodell gripper commands.

    Replicates the Qt JodellGripperTele Schmitt-trigger logic in Python.  The loop
    reads Omega7 state from the HAL HTTP server, runs both SchmittGripper instances,
    and issues open/close commands through the existing Rs485GripperDriver — no new
    serial infrastructure needed.
    """

    def __init__(
        self,
        settings: SettingsService,
        hal: HalClient,
        hardware: HardwareService,
        logs: LogService,
        gripper_workers: Any | None = None,
    ) -> None:
        self._settings = settings
        self._hal = hal
        self._hardware = hardware
        self._logs = logs
        self._gripper_workers = gripper_workers
        self._left = SchmittGripper("left")
        self._right = SchmittGripper("right")
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._loop())
        self._logs.info("[GRIPPER]", "gripper teleop started")

    def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        self._logs.info("[GRIPPER]", "gripper teleop stopped")

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def reset_side(self, side: str) -> None:
        """Reset state machine after a manual gripper command so teleop doesn't immediately override it."""
        if side == "left":
            self._left.reset()
        elif side == "right":
            self._right.reset()

    def get_status(self) -> dict[str, Any]:
        return {
            "running": self.is_running(),
            "leftState": self._left.state,
            "rightState": self._right.state,
            "leftObjectDetected": self._left.object_detected,
            "rightObjectDetected": self._right.object_detected,
        }

    # ------------------------------------------------------------------ loop

    async def _loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            config = self._settings.get_config()
            gt_cfg: dict[str, Any] = config.get("teleop", {}).get("gripperTeleop", {})
            loop_hz = float(gt_cfg.get("loopHz", 100))
            diag = bool(gt_cfg.get("diagLog", False))

            try:
                omega = await self._hal.omega_state()
                hands = {
                    h["side"]: h
                    for h in omega.get("hands", [])
                    if isinstance(h, dict) and "side" in h
                }

                for side, schmitt in (("left", self._left), ("right", self._right)):
                    if not bool(config.get("gripper", {}).get(f"{side}Enabled", False)):
                        schmitt.reset()
                        continue
                    hand = hands.get(side, {})
                    if not hand.get("connected"):
                        continue
                    gap_mm = hand.get("gripperGapMm")
                    if gap_mm is None:
                        if not bool(gt_cfg.get("buttonFallback", True)):
                            continue
                        pressed = bool(hand.get("gripperPressed", False))
                        if diag:
                            self._logs.info(
                                "[GRIPPER]",
                                f"{side} gap unavailable, button={pressed} state={schmitt.state}",
                            )
                        cmd = schmitt.update_button(pressed)
                    else:
                        gap_mm = float(gap_mm)
                        if diag:
                            self._logs.info(
                                "[GRIPPER]",
                                f"{side} gap={gap_mm:.2f}mm state={schmitt.state}",
                            )
                        cmd = schmitt.update(gap_mm, gt_cfg)
                    if cmd is not None:
                        speed, torque = self._motion_params(cmd, gt_cfg)
                        result = await asyncio.to_thread(
                            self._issue_command, config, side, cmd, speed, torque
                        )
                        self._logs.info("[GRIPPER]", f"{side} cmd={cmd} → {result.message}")

                    if schmitt.tick_object_detect():
                        result = await asyncio.to_thread(
                            self._read_position, config, side
                        )
                        if result.ok and result.position_mm is not None:
                            stroke_mm = float(config.get("gripper", {}).get("strokeMm", 26))
                            margin = int(gt_cfg.get("objectDetectMargin", 10))
                            threshold_mm = stroke_mm * margin / 255.0
                            detected = result.position_mm > threshold_mm
                            schmitt.set_object_detected(detected)
                            label = "已夹持物体" if detected else "未检出物体"
                            self._logs.info(
                                "[GRIPPER]",
                                f"{side} 物体检出: {label} pos={result.position_mm:.1f}mm",
                            )

            except Exception as exc:
                self._logs.warning("[GRIPPER]", f"loop error: {exc}")

            await asyncio.sleep(1.0 / loop_hz)

    # ------------------------------------------------------------------ helpers

    def _issue_command(
        self,
        config: dict[str, Any],
        side: str,
        cmd: str,
        speed: int,
        torque: int,
    ) -> Any:
        cfg = copy.deepcopy(config)
        if not bool(cfg.get("gripper", {}).get(f"{side}Enabled", False)):
            raise RuntimeError(f"{side} gripper is disabled; enable it before teleop")
        cfg["gripper"]["commandSpeed"] = speed
        cfg["gripper"]["commandTorque"] = torque
        if self._gripper_workers is not None and self._gripper_workers.is_enabled(cfg):
            return self._gripper_workers.command(cfg, side, cmd, None)
        return self._hardware.gripper.command(cfg, side, cmd, None)

    def _read_position(self, config: dict[str, Any], side: str) -> Any:
        if self._gripper_workers is not None and self._gripper_workers.is_enabled(config):
            return self._gripper_workers.position(config, side)
        return self._hardware.gripper.position(config, side)

    @staticmethod
    def _motion_params(cmd: str, gt_cfg: dict[str, Any]) -> tuple[int, int]:
        if cmd == "close":
            return int(gt_cfg.get("gripSpeed", 128)), int(gt_cfg.get("gripTorque", 192))
        return int(gt_cfg.get("releaseSpeed", 255)), int(gt_cfg.get("releaseTorque", 64))
