from __future__ import annotations

import asyncio
import copy
import time
from typing import Any

from backend.core.config import SettingsService
from backend.core.gripper_protection import protected_gripper_target_mm_from_values
from backend.core.logging import LogService
from backend.hal_client.client import HalClient
from backend.services.hardware_service import HardwareService


class FollowGripper:
    """Single-side continuous follower: maps Omega7 gap to a Jodell target."""

    def __init__(self, side: str) -> None:
        self.side = side
        self._last_raw_position: int | None = None
        self._last_command_ms: float = 0.0
        self._target_mm: float | None = None
        self._button_pressed: bool | None = None
        self._observed_gap_min: float | None = None
        self._observed_gap_max: float | None = None

    @property
    def state(self) -> str:
        return "idle" if self._target_mm is None else "follow"

    @property
    def target_mm(self) -> float | None:
        return self._target_mm

    def reset(self) -> None:
        self._last_raw_position = None
        self._last_command_ms = 0.0
        self._target_mm = None
        self._button_pressed = None
        self._observed_gap_min = None
        self._observed_gap_max = None

    def update(
        self,
        gap_mm: float,
        cfg: dict[str, Any],
        stroke_mm: float,
        now_ms: float | None = None,
    ) -> tuple[float, int] | None:
        side = self.side
        gap_min = float(cfg.get(f"{side}GapMinMm", 0.0))
        gap_max = float(cfg.get(f"{side}GapMaxMm", 25.0))
        gap_min, gap_max = self._effective_gap_range(gap_mm, gap_min, gap_max, cfg)
        span = gap_max - gap_min
        if abs(span) < 1e-6:
            return None
        open_ratio = max(0.0, min(1.0, (gap_mm - gap_min) / span))
        if bool(cfg.get(f"{side}GapInvert", False)):
            open_ratio = 1.0 - open_ratio
        target_mm = protected_gripper_target_mm_from_values(
            open_ratio * stroke_mm,
            stroke_mm,
            bool(cfg.get("icfTargetProtectionEnabled", False)),
            float(cfg.get("icfTargetMinGapMm", 0.0)),
        )
        raw_position = round((1.0 - (target_mm / stroke_mm)) * 255) if stroke_mm > 0 else 255
        return self._maybe_command(target_mm, raw_position, cfg, now_ms)

    def update_button(
        self,
        pressed: bool,
        cfg: dict[str, Any],
        stroke_mm: float,
        now_ms: float | None = None,
    ) -> tuple[float, int] | None:
        if self._button_pressed is pressed:
            return None
        self._button_pressed = pressed
        target_mm = protected_gripper_target_mm_from_values(
            0.0 if pressed else stroke_mm,
            stroke_mm,
            bool(cfg.get("icfTargetProtectionEnabled", False)),
            float(cfg.get("icfTargetMinGapMm", 0.0)),
        )
        raw_position = round((1.0 - (target_mm / stroke_mm)) * 255) if stroke_mm > 0 else 255
        return self._maybe_command(target_mm, raw_position, cfg, now_ms, force=True)

    def _effective_gap_range(
        self,
        gap_mm: float,
        configured_min: float,
        configured_max: float,
        cfg: dict[str, Any],
    ) -> tuple[float, float]:
        if not bool(cfg.get("autoGapCalibration", True)):
            return configured_min, configured_max
        self._observed_gap_min = gap_mm if self._observed_gap_min is None else min(self._observed_gap_min, gap_mm)
        self._observed_gap_max = gap_mm if self._observed_gap_max is None else max(self._observed_gap_max, gap_mm)
        observed_min = self._observed_gap_min
        observed_max = self._observed_gap_max
        if observed_min is None or observed_max is None:
            return configured_min, configured_max
        min_span = max(0.1, float(cfg.get("autoGapMinSpanMm", 2.0)))
        margin = max(0.0, float(cfg.get("autoGapMarginMm", 1.0)))
        observed_span = observed_max - observed_min
        outside_configured_range = (
            observed_min < configured_min - margin
            or observed_max > configured_max + margin
        )
        if outside_configured_range and observed_span >= min_span:
            return observed_min, observed_max
        return configured_min, configured_max

    def _maybe_command(
        self,
        target_mm: float,
        raw_position: int,
        cfg: dict[str, Any],
        now_ms: float | None,
        *,
        force: bool = False,
    ) -> tuple[float, int] | None:
        now = time.monotonic() * 1000.0 if now_ms is None else now_ms
        raw_position = min(max(int(raw_position), 0), 255)
        deadband = max(0, int(cfg.get("positionDeadbandCounts", 1)))
        min_interval_ms = max(0.0, float(cfg.get("minCommandIntervalMs", 20)))
        changed = self._last_raw_position is None or abs(raw_position - self._last_raw_position) >= deadband
        interval_elapsed = (
            self._last_raw_position is not None
            and raw_position != self._last_raw_position
            and now - self._last_command_ms >= min_interval_ms
        )
        self._target_mm = target_mm
        if force or changed or interval_elapsed:
            self._last_raw_position = raw_position
            self._last_command_ms = now
            return target_mm, raw_position
        return None


class GripperTeleService:
    """Background task that maps Omega7 gripper gap to Jodell target commands."""

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
        self._left = FollowGripper("left")
        self._right = FollowGripper("right")
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._arm_sources: set[str] = set()
        self._teleop_enabled_sides: set[str] = set()

    def start(self, source: str = "manual") -> None:
        self._arm_sources.add(source)
        stopping = self._stop_event is not None and self._stop_event.is_set()
        if self._task is not None and not self._task.done() and not stopping:
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._loop())
        self._logs.info("[GRIPPER]", "gripper teleop started")

    def stop(self, source: str = "manual", *, force: bool = False) -> None:
        if force:
            self._arm_sources.clear()
        else:
            self._arm_sources.discard(source)
        if self._arm_sources:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._left.reset()
        self._right.reset()
        self._teleop_enabled_sides.clear()
        self._logs.info("[GRIPPER]", "gripper teleop stopped")

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def reset_side(self, side: str) -> None:
        if side == "left":
            self._left.reset()
        elif side == "right":
            self._right.reset()

    def get_status(self) -> dict[str, Any]:
        return {
            "running": self.is_running(),
            "sources": sorted(self._arm_sources),
            "leftState": self._left.state,
            "rightState": self._right.state,
            "leftTargetMm": self._left.target_mm,
            "rightTargetMm": self._right.target_mm,
        }

    async def _get_config_async(self) -> dict[str, Any]:
        return await asyncio.to_thread(self._settings.get_config)

    async def _loop(self) -> None:
        stop_event = self._stop_event
        assert stop_event is not None
        while not stop_event.is_set():
            config = await self._get_config_async()
            gt_cfg: dict[str, Any] = config.get("teleop", {}).get("gripperTeleop", {})
            loop_hz = max(1.0, float(gt_cfg.get("loopHz", 100)))
            diag = bool(gt_cfg.get("diagLog", False))
            if not bool(gt_cfg.get("enabled", False)):
                self._left.reset()
                self._right.reset()
                await asyncio.sleep(1.0 / loop_hz)
                continue

            try:
                omega = await self._hal.omega_state()
                if stop_event.is_set():
                    break
                hands = {
                    h["side"]: h
                    for h in omega.get("hands", [])
                    if isinstance(h, dict) and "side" in h
                }
                gripper = config.get("gripper", {}) if isinstance(config.get("gripper"), dict) else {}
                stroke_mm = float(gripper.get("strokeMm", 26))
                follower_cfg = {
                    **gt_cfg,
                    "icfTargetProtectionEnabled": bool(gripper.get("icfTargetProtectionEnabled", True)),
                    "icfTargetMinGapMm": float(gripper.get("icfTargetMinGapMm", 1.02)),
                }

                for side, follower in (("left", self._left), ("right", self._right)):
                    hand = self._select_source_hand(hands, gt_cfg, side)
                    if not hand.get("connected") or not hand.get("lastReadOk", False):
                        follower.reset()
                        continue
                    gap_mm = hand.get("gripperGapMm")
                    if gap_mm is None:
                        if not bool(gt_cfg.get("buttonFallback", True)):
                            continue
                        pressed = bool(hand.get("gripperPressed", False))
                        if diag:
                            self._logs.info(
                                "[GRIPPER]",
                                f"{side} gap unavailable, button={pressed} state={follower.state}",
                            )
                        command = follower.update_button(pressed, follower_cfg, stroke_mm)
                    else:
                        gap_mm = float(gap_mm)
                        if diag:
                            self._logs.info(
                                "[GRIPPER]",
                                f"{side} gap={gap_mm:.2f}mm state={follower.state}",
                            )
                        command = follower.update(gap_mm, follower_cfg, stroke_mm)
                    if command is None:
                        continue
                    target_mm, raw_position = command
                    result = await asyncio.to_thread(
                        self._issue_command,
                        config,
                        side,
                        "target",
                        int(gt_cfg.get("gripSpeed", 255)),
                        int(gt_cfg.get("gripTorque", 1)),
                        target_mm,
                    )
                    self._logs.info(
                        "[GRIPPER]",
                        f"{side} target={target_mm:.2f}mm raw={raw_position} -> {result.message}",
                    )
            except Exception as exc:
                self._logs.warning("[GRIPPER]", f"loop error: {exc}")

            await asyncio.sleep(1.0 / loop_hz)

    def _issue_command(
        self,
        config: dict[str, Any],
        side: str,
        cmd: str,
        speed: int,
        torque: int,
        target_mm: float | None,
    ) -> Any:
        cfg = copy.deepcopy(config)
        cfg.setdefault("gripper", {})[f"{side}Enabled"] = True
        cfg["gripper"]["commandSpeed"] = speed
        cfg["gripper"]["commandTorque"] = torque
        if self._gripper_workers is not None and self._gripper_workers.is_enabled(cfg):
            if side not in self._teleop_enabled_sides:
                enable_result = self._gripper_workers.command(cfg, side, "enable", None)
                if not enable_result.ok:
                    return enable_result
                self._teleop_enabled_sides.add(side)
            return self._gripper_workers.command(cfg, side, cmd, target_mm)
        return self._hardware.gripper.command(cfg, side, cmd, target_mm)

    @staticmethod
    def _select_source_hand(
        hands: dict[str, dict[str, Any]],
        gt_cfg: dict[str, Any],
        side: str,
    ) -> dict[str, Any]:
        source = str(gt_cfg.get(f"{side}SourceHand", f"Physical{side.title()}")).lower()
        source = source.replace("-", "").replace("_", "").replace(" ", "")
        if source in {"physicalleft", "logicalleft", "left"}:
            return hands.get("left", {})
        if source in {"physicalright", "logicalright", "right"}:
            return hands.get("right", {})
        if source in {"lefthanded", "handedleft"}:
            return next((hand for hand in hands.values() if hand.get("leftHanded") is True), {})
        if source in {"righthanded", "handedright"}:
            return next((hand for hand in hands.values() if hand.get("leftHanded") is False), {})
        return hands.get(side, {})
