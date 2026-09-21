"""本地示教片段回放：完整校验、独占执行及失败停机。"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from backend.core.data_contract import validate_data_contract, dataset_to_hardware_state
from backend.core.gripper_protection import icf_target_min_gap_mm
from backend.core.motion_limits import effective_limit_arrays
from backend.core.units import motion_pulse_per_unit
from backend.services.gripper_backend import native_gripper_payload

AXES = ("X", "Y", "Z", "Roll", "Pitch", "Yaw")
# 单位为数据集 um / mdeg / mm；用于到位判定和跟踪偏差拒绝。
TOLERANCE = [10., 10., 10., 50., 50., 50., .2] * 2
MAX_ERROR = [500., 500., 500., 1000., 1000., 1000., 2.] * 2


def vector14(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 14:
        raise ValueError("state/action 必须是完整的 14 维向量")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) for item in value):
        raise ValueError("state/action 包含非数值或非有限值")
    return [float(item) for item in value]


def validate_episode(data: dict[str, Any]) -> None:
    validate_data_contract(data.get("dataContract"))
    fps = float(data["fps"])
    if not math.isfinite(fps) or not 1 <= fps <= 120:
        raise ValueError("无效的录制帧率")
    rows = data["rows"]
    if not rows:
        raise ValueError("片段没有动作帧")
    previous = -1.
    for index, row in enumerate(rows):
        stamp = float(row["timestamp"])
        if row["frame_index"] != index or not math.isfinite(stamp) or stamp < 0 or stamp <= previous:
            raise ValueError("帧序号或时间戳不连续")
        if abs(stamp - index / fps) > max(.01, .1 / fps):
            raise ValueError("时间戳与录制帧率不一致")
        vector14(row["action"])
        vector14(row["observation.state"])
        previous = stamp


class DatasetReplayService:
    def __init__(self, services):
        self.svc = services
        self.safety = services.commands.safety
        self.task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._status: dict[str, Any] = {"phase": "idle", "active": False, "frame": 0, "totalFrames": 0, "error": ""}

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()

    def status(self) -> dict[str, Any]:
        return {**self._status, "active": self.active}

    async def inspect(self, dataset_id: str, episode_id: str) -> dict[str, Any]:
        data = await asyncio.to_thread(self.svc.recorder.load_replay_episode, dataset_id, episode_id)
        await asyncio.to_thread(validate_episode, data)
        config = await asyncio.to_thread(self.svc.settings.get_config)
        await asyncio.to_thread(self._validate_config, data, config)
        return {"datasetId": dataset_id, "episodeId": episode_id, "frames": len(data["rows"]),
                "fps": data["fps"], "durationS": len(data["rows"]) / data["fps"],
                "initialState": data["rows"][0]["observation.state"]}

    def _validate_config(self, data, config):
        episode = data["episode"]
        recorded = episode.get("motionOrigin", {})
        current = self.svc.recorder._episode_motion_origin_snapshot(config)
        for side in ("left", "right"):
            if not recorded.get(f"{side}Valid") or not current.get(f"{side}Valid"):
                raise ValueError("录制原点或当前工作原点缺失")
            if recorded.get(f"{side}Pulse") != current.get(f"{side}Pulse"):
                raise ValueError("当前工作原点与录制原点不同，请恢复原点后重新校验")
        calibration = episode.get("motionCalibration", {}).get("kinematics")
        if not calibration or calibration != self.svc.recorder._motion_calibration_snapshot(config)["kinematics"]:
            raise ValueError("运动标定与录制时不一致或缺失")
        for row in data["rows"]:
            for values in (row["action"], row["observation.state"]):
                self._targets(values, config)  # 全量检查目标，不能静默裁剪非法数据。

    def _targets(self, action, config):
        hardware = dataset_to_hardware_state(vector14(action))
        coeff = motion_pulse_per_unit(config)
        origin = config["motion"]["origin"]
        targets = {}
        for side, offset, pulse_offset in (("left", 0, 0), ("right", 7, 6)):
            low, high = effective_limit_arrays(config, side)
            values = [hardware[offset + i] / (1000. if i >= 3 else 1.)
                      + origin[f"{side}Pulse"][i] / coeff[pulse_offset + i] for i in range(6)]
            if any(not math.isfinite(v) or v < low[i] or v > high[i] for i, v in enumerate(values)):
                raise ValueError(f"{side} 回放目标超出当前软限位")
            gap = hardware[offset + 6]
            if gap + 1e-5 < icf_target_min_gap_mm(config) or gap > float(config["gripper"]["strokeMm"]) + 1e-5:
                raise ValueError("夹爪目标超出行程或小于当前保护开口")
            targets[side] = (values, gap, low, high)
        return targets

    async def start(self, dataset_id: str, episode_id: str, speed: float) -> dict[str, Any]:
        if self.active:
            raise RuntimeError("已有回放任务正在执行")
        if not math.isfinite(speed) or not .1 <= speed <= 1.:
            raise ValueError("回放倍率必须在 0.1 到 1 之间")
        token = self.safety.capture()
        self.safety.check(token)
        self._stop = asyncio.Event()
        self._status = {"phase": "loading", "frame": 0, "totalFrames": 0, "error": "",
                        "datasetId": dataset_id, "episodeId": episode_id, "speed": speed}
        self.task = asyncio.create_task(self._run(dataset_id, episode_id, speed, token), name="dataset-replay")
        return self.status()

    def _check(self, token):
        if self._stop.is_set():
            raise RuntimeError("回放已停止")
        self.safety.check(token)

    async def _observe(self, config):
        motion = await self.svc.hal.motion_state()
        pulses = motion.get("pulses")
        if not isinstance(pulses, list) or len(pulses) != 12 or any(not math.isfinite(float(v)) for v in pulses):
            raise RuntimeError("运动反馈缺失")
        native = (await self.svc.hal.command("teleop.native.status", {})).get("response", {})
        if native.get("running"):
            raise RuntimeError("请停止示教遥操作后再回放")
        coeff = motion_pulse_per_unit(config)
        raw = []
        for side, offset in (("left", 0), ("right", 6)):
            raw.extend([(float(pulses[offset + i]) - config["motion"]["origin"][f"{side}Pulse"][i])
                        / coeff[offset + i] * (1000 if i >= 3 else 1) for i in range(6)])
            detail = native.get("grippers", {}).get(side, {})
            gap = detail.get("positionMm")
            sample_age = time.time() * 1000 - float(detail.get("positionSampleTs", 0))
            if detail.get("positionOk") is not True or not 0 <= sample_age <= 1000:
                raise RuntimeError(f"{side} 夹爪反馈已过期")
            if detail.get("ok") is False or gap is None or not math.isfinite(float(gap)) or float(gap) < 0:
                raise RuntimeError(f"{side} 夹爪反馈不可用")
            raw.append(float(gap))
        return dataset_to_hardware_state(raw)

    async def _send(self, action, config, token):
        self._check(token)
        current_config = await asyncio.to_thread(self.svc.settings.get_config)
        if any(current_config.get(key) != config.get(key) for key in ("motion", "teleop", "gripper", "force")):
            raise RuntimeError("回放期间控制配置发生变化")
        self._check(token)
        targets = self._targets(action, config)
        teleop = config["teleop"]
        for side, (values, gap, low, high) in targets.items():
            self._check(token)
            payload = {
                "side": side, "deltas": dict(zip(AXES, values)),
                "enabledAxes": teleop[f"{side}EnabledAxes"], "syncZeroDeltaTarget": True,
                "softLimitMin": low, "softLimitMax": high,
                "translationStepLimitPulse": teleop.get("translationStepLimitPulse", 4000.),
                "rotationStepLimitPulse": teleop.get("rotationStepLimitPulse", 1250.),
                "translationVelocityUiPerSec": min(float(teleop.get("translationMaxVelocityUmS", 4000.)), 1000.),
                "rotationVelocityUiPerSec": min(float(teleop.get("rotationMaxVelocityDegS", 6.)), 1.),
                "accTimeSec": teleop.get("motionProfileAccSec", .05),
                "decTimeSec": teleop.get("motionProfileDecSec", .05),
            }
            await self.svc.hal.command("motion.replay_absolute_target", payload)
            self._check(token)
            await self.svc.hal.command("gripper.replay_target", native_gripper_payload(config, side, gap))

    async def _settle(self, target, config, token):
        deadline = time.monotonic() + 30.
        while True:
            observed = await self._observe(config)
            self._check(token)
            if all(abs(a-b) <= tolerance for a, b, tolerance in zip(target, observed, TOLERANCE)):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError("目标位置对齐超时")
            await self._send(target, config, token)
            await asyncio.sleep(.05)

    async def _run(self, dataset_id, episode_id, speed, token):
        began = False
        try:
            with self.safety.operation():
                self._check(token)
                data = await asyncio.to_thread(self.svc.recorder.load_replay_episode, dataset_id, episode_id)
                await asyncio.to_thread(validate_episode, data)
                config = await asyncio.to_thread(self.svc.settings.get_config)
                await asyncio.to_thread(self._validate_config, data, config)
                self._check(token)
                health = await self.svc.hal.health()
                if "replay_absolute_target_v1" not in (health.capabilities or []):
                    raise RuntimeError("HAL 不支持绝对目标回放，请配套构建并部署 HAL 与夹爪 worker")
                record_status = await asyncio.to_thread(self.svc.recorder.status)
                mapper = self.svc.teleop_mapper.status(config)
                if record_status.get("active") or mapper.get("armed") or mapper.get("running") or self.svc.policy.auto_status(config)["running"]:
                    raise RuntimeError("请先停止录制、遥操作及策略执行")
                if not all(config["gripper"].get(f"{side}Enabled") for side in ("left", "right")):
                    raise RuntimeError("双侧夹爪必须已启用且反馈有效")
                self._check(token)
                began = True
                # 停止示教会退出原有采样线程；独立启动只读采样，不下发位置。
                await self.svc.hal.command("gripper.prepare_replay", native_gripper_payload(config, "left", 0.))
                feedback_deadline = time.monotonic() + 2.
                while True:
                    self._check(token)
                    try:
                        observed = await self._observe(config)
                        break
                    except RuntimeError as exc:
                        if "夹爪反馈" not in str(exc) or time.monotonic() >= feedback_deadline:
                            raise
                        await asyncio.sleep(.05)
                initial = data["rows"][0]["observation.state"]
                for side, offset in (("right", 0), ("left", 7)):
                    for i, enabled in enumerate(config["teleop"][f"{side}EnabledAxes"]):
                        if not enabled and any(abs(row[key][offset+i] - observed[offset+i]) > TOLERANCE[offset+i]
                            for row in data["rows"] for key in ("action", "observation.state")):
                            raise RuntimeError("回放包含未启用轴的运动")
                self._check(token)
                for side in ("left", "right"):
                    await self.svc.commands.enable_motion_side(side)
                    self._check(token)
                self._status.update(phase="aligning", totalFrames=len(data["rows"]))
                await self._settle(initial, config, token)
                self._status["phase"] = "running"
                start = time.monotonic()
                previous = initial
                for index, row in enumerate(data["rows"]):
                    deadline = start + index / float(data["fps"]) / speed
                    await asyncio.sleep(max(0., deadline - time.monotonic()))
                    self._check(token)
                    if time.monotonic() - deadline > max(.25, 2 / float(data["fps"]) / speed):
                        raise RuntimeError("回放执行超时，已停止；请降低倍率")
                    current_config = await asyncio.to_thread(self.svc.settings.get_config)
                    if any(current_config.get(key) != config.get(key) for key in ("motion", "teleop", "gripper", "force")):
                        raise RuntimeError("回放期间控制配置发生变化")
                    observed = await self._observe(config)
                    errors = [abs(a-b) for a, b in zip(previous, observed)]
                    if any(error > limit for error, limit in zip(errors, MAX_ERROR)):
                        raise RuntimeError("实际位置偏离回放目标，已停止")
                    await self._send(row["action"], config, token)
                    self._status.update(frame=index+1, trackingError=errors)
                    previous = row["action"]
                self._status["phase"] = "settling"
                await self._settle(previous, config, token)
                for side in ("left", "right"):
                    await self.svc.commands.stop_motion_side(side)
                await self.svc.hal.command("teleop.native.stop", {})
                if self._stop.is_set() or self.safety.latched:
                    raise RuntimeError("回放被停止")
                self._status["phase"] = "completed"
        except (Exception, asyncio.CancelledError) as exc:
            self._status.update(phase="stopped" if self._stop.is_set() else "failed", error=str(exc))
            if began:
                try:
                    await self.svc.commands.emergency_stop()
                except Exception as stop_error:
                    self._status["error"] += f"; 急停未确认: {stop_error}"
            self.svc.logs.error("[HAL]", f"回放停止: {exc}")
        finally:
            self._status["active"] = False

    async def stop(self):
        if self.active:
            self._stop.set()
            self.safety.interrupt()
            try:
                await self.svc.commands.emergency_stop()
            finally:
                await self.task
        return self.status()
