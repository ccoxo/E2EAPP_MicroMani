"""本地示教片段回放：完整校验、独占执行及失败停机。"""
from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import datetime
from typing import Any

from backend.core.data_contract import validate_data_contract, dataset_to_hardware_state
from backend.core.participation import participation, action_mask, hardware_sides
from backend.core.gripper_protection import icf_target_min_gap_mm
from backend.core.motion_profile import replay_motion_profile
from backend.services.replay_timing import build_replay_timing, channel_label
from backend.core.motion_limits import effective_limit_arrays
from backend.core.operator_view import operator_side_for_hardware_side, hardware_side_for_operator_side
from backend.core.units import motion_pulse_per_unit, pulse_to_lerobot, pulse_to_ui
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
        self._participation = None
        self._feedback_samples: dict[str, Any] = {}
        self._started_at = None
        self._stop = asyncio.Event()
        self._status: dict[str, Any] = {"phase": "idle", "active": False, "frame": 0, "totalFrames": 0, "error": ""}

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()

    def _update_progress(self):
        if self._started_at is not None:
            elapsed = max(0., time.monotonic() - self._started_at)
            self._status['elapsedS'] = elapsed
            completed = self._status.get('completedFrames', 0)
            self._status['effectiveSpeed'] = completed / self._status['fps'] / elapsed if elapsed else 0.

    def status(self) -> dict[str, Any]:
        return {**self._status, "active": self.active}

    async def inspect(self, dataset_id: str, episode_id: str, selected=None, speed=.25) -> dict[str, Any]:
        data = await asyncio.to_thread(self.svc.recorder.load_replay_episode, dataset_id, episode_id)
        await asyncio.to_thread(validate_episode, data)
        config = await asyncio.to_thread(self.svc.settings.get_config)
        await asyncio.to_thread(self._validate_config, data, config, self._resolve_participation(data, selected))
        timing = await asyncio.to_thread(build_replay_timing, data, config, self._resolve_participation(data, selected), speed)
        return {"timing": timing['summary'], "datasetId": dataset_id, "episodeId": episode_id, "frames": len(data["rows"]),
                "fps": data["fps"], "durationS": len(data["rows"]) / data["fps"],
                "initialState": data["rows"][0]["observation.state"],
                "participation": self._resolve_participation(data, selected)}

    def _resolve_participation(self, data, selected):
        recorded = data["episode"].get("participation")
        resolved = participation(recorded if recorded is not None else selected)
        if recorded is not None and selected is not None and resolved != participation(selected):
            raise ValueError("选择的参与侧与 Episode 记录不一致")
        return resolved

    def _validate_config(self, data, config, selected=None):
        selected = selected or self._participation

        episode = data["episode"]
        recorded = episode.get("motionOrigin", {})
        current = self.svc.recorder._episode_motion_origin_snapshot(config)
        for side in hardware_sides(selected):
            if not recorded.get(f"{side}Valid") or not current.get(f"{side}Valid"):
                raise ValueError("录制原点或当前工作原点缺失")
            if recorded.get(f"{side}Pulse") != current.get(f"{side}Pulse"):
                raise ValueError("当前工作原点与录制原点不同，请恢复原点后重新校验")
        calibration = episode.get("motionCalibration", {}).get("kinematics")
        if not calibration or calibration != self.svc.recorder._motion_calibration_snapshot(config)["kinematics"]:
            raise ValueError("运动标定与录制时不一致或缺失")
        for row in data["rows"]:
            for values in (row["action"], row["observation.state"]):
                self._targets(values, config, selected)  # 全量检查目标，不能静默裁剪非法数据。

    def _targets(self, action, config, selected=None):
        selected = selected or self._participation
        hardware = dataset_to_hardware_state(vector14(action))
        coeff = motion_pulse_per_unit(config)
        origin = config["motion"]["origin"]
        targets = {}
        for side, offset, pulse_offset in (("left", 0, 0), ("right", 7, 6)):
            if side not in hardware_sides(selected):
                continue
            low, high = effective_limit_arrays(config, side)
            values = [hardware[offset + i] / (1000. if i >= 3 else 1.)
                      + pulse_to_ui(origin[f"{side}Pulse"][i], pulse_offset + i, coeff[pulse_offset + i])
                      for i in range(6)]
            if any(not math.isfinite(v) or v < low[i] or v > high[i] for i, v in enumerate(values)):
                raise ValueError(f"{side} 回放目标超出当前软限位")
            gap = hardware[offset + 6]
            if side in hardware_sides(selected, "grippers") and (gap + 1e-5 < icf_target_min_gap_mm(config) or gap > float(config["gripper"]["strokeMm"]) + 1e-5):
                raise ValueError("夹爪目标超出行程或小于当前保护开口")
            targets[side] = (values, gap, low, high)
        return targets

    async def start(self, dataset_id: str, episode_id: str, speed: float, selected=None) -> dict[str, Any]:
        if self.active:
            raise RuntimeError("已有回放任务正在执行")
        if not math.isfinite(speed) or not .1 <= speed <= 1.:
            raise ValueError("回放倍率必须在 0.1 到 1 之间")
        token = self.safety.capture()
        self.safety.check(token)
        self._started_at = None
        self._stop = asyncio.Event()
        self._status = {"phase": "loading", "frame": 0, "totalFrames": 0, "error": "",
                        "datasetId": dataset_id, "episodeId": episode_id, "speed": speed}
        self._feedback_samples = {}
        self.task = asyncio.create_task(self._run(dataset_id, episode_id, speed, token, selected), name="dataset-replay")
        return self.status()

    def _check(self, token):
        if self._stop.is_set():
            raise RuntimeError("回放已停止")
        self.safety.check(token)

    async def _observe(self, config):
        self._feedback_samples = {}
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
            if side not in hardware_sides(self._participation):
                raw.extend([0.] * 7)  # 未参与维度仅占位，不用于比较或控制。
                continue
            raw.extend([pulse_to_lerobot(
                float(pulses[offset + i]) - config["motion"]["origin"][f"{side}Pulse"][i],
                offset + i, coeff[offset + i],
            ) for i in range(6)])
            if side not in hardware_sides(self._participation, "grippers"):
                raw.append(0.)
                continue
            detail = native.get("grippers", {}).get(side, {})
            label = "操作者左侧" if operator_side_for_hardware_side(side) == "left" else "操作者右侧"
            gap = detail.get("positionMm")
            if detail.get("positionOk") is not True or detail.get("ok") is False:
                raise RuntimeError(f"{label}夹爪反馈读取失败：未获得成功的位置读取，请检查夹爪连接与通信")
            sample_ts = detail.get("positionSampleTs")
            if isinstance(sample_ts, bool) or not isinstance(sample_ts, (int, float)) or not math.isfinite(sample_ts) or sample_ts <= 0:
                raise RuntimeError(f"{label}夹爪反馈时间戳无效：缺少有效的成功采样时间")
            sample_age = time.time() * 1000 - sample_ts
            if sample_age < 0:
                raise RuntimeError(f"{label}夹爪反馈时间戳无效：采样时间晚于当前时间，请检查系统时钟")
            if sample_age > 1000:
                raise RuntimeError(f"{label}夹爪反馈超时：距最近成功采样 {sample_age / 1000:.2f} 秒，要求不超过 1 秒")
            if isinstance(gap, bool) or not isinstance(gap, (int, float)) or not math.isfinite(gap) or gap < 0:
                raise RuntimeError(f"{label}夹爪反馈位置数值异常：开口必须是有限且不小于 0 的毫米数值")
            raw.append(float(gap))
        self._feedback_samples = {"motion": motion.get("timestamp_ms"), **{
            side: native.get("grippers", {}).get(side, {}).get("positionSampleTs")
            for side in hardware_sides(self._participation, "grippers")
        }}
        return dataset_to_hardware_state(raw)

    async def _send(self, action, config, token, *, include_grippers=True):
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
                **replay_motion_profile(config),
            }
            await self.svc.hal.command("motion.replay_absolute_target", payload)
            self._check(token)
            if include_grippers and side in hardware_sides(self._participation, "grippers"):
                await self.svc.hal.command("gripper.replay_target", native_gripper_payload(config, side, gap))

    async def _settle(self, target, config, token):
        deadline = time.monotonic() + 30.
        final = self._status.get('phase') == 'settling'
        while True:
            observed = await self._observe(config)
            self._check(token)
            self._update_progress()
            mask = action_mask(self._participation)
            errors = [abs(a-b) if enabled else 0. for a, b, enabled in zip(target, observed, mask)]
            self._status["trackingError"] = errors
            if all(error <= tolerance for i, (error, tolerance) in enumerate(zip(errors, TOLERANCE))
                   if not final or i % 7 != 6):
                if final:
                    details = [
                        f'{channel_label(i)}：目标 {target[i]:.4g} mm，实际 {observed[i]:.4g} mm，误差 {errors[i]:.4g} mm'
                        for i in (6, 13) if errors[i] > TOLERANCE[i]
                    ]
                    if details:
                        warning = '末帧夹爪未到位（仅提示，不判定夹取成功或失败）：' + '；'.join(details)
                        self._status['warning'] = warning
                        self.svc.logs.warning('[HAL]', warning)
                return
            if time.monotonic() >= deadline:
                stage = "起点对齐" if self._status.get("phase") == "aligning" else "末帧到位"
                details = [f"目标位置对齐超时（30 秒，{stage}）；以下参与通道未到位："]
                for index, (error, tolerance) in enumerate(zip(errors, TOLERANCE)):
                    if error <= tolerance or (final and index % 7 == 6):
                        continue
                    operator_side = "left" if index < 7 else "right"
                    side_label = "左" if operator_side == "left" else "右"
                    hardware_side = hardware_side_for_operator_side(operator_side)
                    axis = index % 7
                    channel = f"夹爪（硬件{hardware_side}）开口" if axis == 6 else f"臂（硬件{hardware_side}）{AXES[axis]}"
                    scale, unit = (1000., "°") if 3 <= axis <= 5 else (1., "mm" if axis == 6 else "μm")
                    details.append(
                        f"操作者{side_label}{channel}：目标 {target[index]/scale:.6g} {unit}，"
                        f"实际 {observed[index]/scale:.6g} {unit}，误差 {error/scale:.6g} {unit}，"
                        f"允许 {tolerance/scale:.6g} {unit}"
                    )
                raise RuntimeError("\n".join(details))
            await self._send(target, config, token)
            await asyncio.sleep(.05)

    def _check_tracking(self, data, index, previous, observed, previous_sent_at_ms,
                        read_started_at_ms, checked_at_ms, *, reason=None, target_kind=None):
        dataset_id = self._status['datasetId']
        episode_id = self._status['episodeId']
        speed = self._status['speed']
        mask = action_mask(self._participation)
        errors = [abs(a-b) if enabled else 0. for a, b, enabled in zip(previous, observed, mask)]
        self._status["trackingError"] = errors
        if reason or any(error > limit for i, (error, limit) in enumerate(zip(errors, MAX_ERROR)) if i % 7 != 6):
            target_index = max(0, index - 1)
            target_kind = target_kind or ("action" if index else "observation.state")
            fault = {
                "datasetId": dataset_id, "episodeId": episode_id,
                "targetFrameIndex": target_index, "nextFrameIndex": index, "targetKind": target_kind,
                "targetTimestampS": float(data["rows"][target_index]["timestamp"]),
                "speed": speed, "fps": float(data["fps"]),
                "targetCommandCompletedAtMs": previous_sent_at_ms,
                "readStartedAtMs": read_started_at_ms, "checkedAtMs": checked_at_ms,
                "channels": [],
            }
            details = [
                reason or "实际位置偏离回放目标，已停止（跟踪偏差超限）",
                f"片段：{dataset_id} / {episode_id}",
                f"检查时刻：{datetime.fromtimestamp(checked_at_ms / 1000).astimezone().isoformat(timespec='milliseconds')}；"
                f"反馈读取耗时 {checked_at_ms - read_started_at_ms:.3f} ms；倍率 {speed:g}×",
                f"比较目标：第 {target_index + 1} 帧 {target_kind}（录制时间 {fault['targetTimestampS']:.6g} s）；"
                f"准备下发第 {index + 1} 帧" if index < len(data["rows"]) else "等待末帧到位",
            ]
            for channel_index, enabled in enumerate(mask):
                if not enabled or channel_index % 7 == 6:
                    continue
                operator_side = "left" if channel_index < 7 else "right"
                side_label = "左" if operator_side == "left" else "右"
                hardware_side = hardware_side_for_operator_side(operator_side)
                axis = channel_index % 7
                scale, unit = (1000., "°") if 3 <= axis <= 5 else (1., "mm" if axis == 6 else "μm")
                sample_ts = self._feedback_samples.get(hardware_side if axis == 6 else "motion")
                if isinstance(sample_ts, bool) or not isinstance(sample_ts, (int, float)) or not math.isfinite(sample_ts) or sample_ts <= 0:
                    sample_ts = None
                sample_age = checked_at_ms - sample_ts if sample_ts is not None else None
                exceeded = errors[channel_index] > MAX_ERROR[channel_index]
                waiting = bool(reason) and errors[channel_index] > MAX_ERROR[channel_index] / 2
                channel = {
                    "index": channel_index, "operatorSide": operator_side, "hardwareSide": hardware_side,
                    "axis": "gripper" if axis == 6 else AXES[axis], "unit": unit,
                    "target": previous[channel_index] / scale, "observed": observed[channel_index] / scale,
                    "error": errors[channel_index] / scale, "limit": MAX_ERROR[channel_index] / scale,
                    "exceeded": exceeded, "waiting": waiting, "followLimit": MAX_ERROR[channel_index] / scale / 2,
                    "sampleTs": sample_ts, "sampleAgeMs": sample_age,
                    "sampleTimeSource": "gripper.positionSampleTs" if axis == 6 else "hal.motion.timestamp_ms",
                }
                fault["channels"].append(channel)
                label = f"夹爪（硬件{hardware_side}）开口" if axis == 6 else f"臂（硬件{hardware_side}）{AXES[axis]}"
                sample_text = "未提供" if sample_ts is None else f"{sample_ts:.3f} Unix ms（距检查 {sample_age:.3f} ms）"
                details.append(
                    f"操作者{side_label}{label} [{'超限' if exceeded else '等待跟随' if waiting else '正常'}]："
                    f"目标 {channel['target']:.6g} {unit}，实际 {channel['observed']:.6g} {unit}，"
                    f"误差 {channel['error']:.6g} {unit}，允许 {channel['limit']:.6g} {unit}；反馈时间 {sample_text}"
                    + (f"；下一帧放行阈值 {channel['followLimit']:.6g} {unit}" if waiting else "")
                )
            # 在急停和后续反馈改变前保留现场；日志写入放在急停请求之后。
            self._status["trackingFault"] = fault
            raise RuntimeError("\n".join(details))
        return errors

    async def _read_tracking(self, data, index, target, config, token, sent_at_ms, **kwargs):
        self._check(token)
        read_started = time.time() * 1000
        observed = await self._observe(config)
        self._check(token)
        errors = self._check_tracking(data, index, target, observed, sent_at_ms,
                                      read_started, time.time() * 1000, **kwargs)
        return observed, errors

    async def _wait_segment(self, data, index, target, origin, duration, config, token, sent_at_ms):
        deadline = time.monotonic() + duration
        late_limit = max(.25, 2 / data['fps'] / self._status['speed'])
        waiting_since = None
        previous_wait = self._status['feedbackWaitS']
        mask = action_mask(self._participation)
        while True:
            wake = min(deadline, time.monotonic() + .05) if waiting_since is None else time.monotonic() + .05
            await asyncio.sleep(max(0., wake - time.monotonic()))
            self._check(token)
            observation_started = time.monotonic()
            read_started = time.time() * 1000
            observed = await self._observe(config)
            self._check(token)
            observation_finished = time.monotonic()
            checked = time.time() * 1000
            if waiting_since is not None:
                self._status['feedbackWaitS'] = previous_wait + time.monotonic() - waiting_since
            if observation_finished - wake > late_limit:
                raise RuntimeError(
                    '回放执行超时，已停止；未追赶下发后续帧；'
                    f'片段 {self._status["datasetId"]} / {self._status["episodeId"]}，'
                    f'第 {index + 1} 帧，倍率 {self._status["speed"]:g}×；'
                    f'唤醒延迟 {max(0., observation_started - wake) * 1000:.1f} ms，'
                    f'反馈读取 {(observation_finished - observation_started) * 1000:.1f} ms，'
                    f'总延迟 {(observation_finished - wake) * 1000:.1f} ms，'
                    f'阈值 {late_limit * 1000:.1f} ms'
                )
            self._update_progress()
            if time.monotonic() >= deadline:
                errors = self._check_tracking(data, index + 1, target, observed, sent_at_ms, read_started, checked)
                waiting = [i for i, (error, limit) in enumerate(zip(errors, MAX_ERROR)) if i % 7 != 6 and error > limit / 2]
                if not waiting:
                    self._status.update(phase='running', waitingChannels=[])
                    return
                if waiting_since is None:
                    waiting_since = time.monotonic()
                waited = time.monotonic() - waiting_since
                self._status.update(phase='following', waitingChannels=[channel_label(i) for i in waiting])
                if waited >= 2.:
                    self._check_tracking(data, index + 1, target, observed, sent_at_ms, read_started, checked,
                                         reason='回放跟随等待超时（2 秒），已停止')
            else:
                # 段内目标尚未要求到达终点；仍检查是否越出起止位置范围及原有偏差边界。
                boundary = [min(max(value, min(start, end)), max(start, end))
                            for value, start, end in zip(observed, origin, target)]
                self._check_tracking(data, index + 1, boundary, observed, sent_at_ms, read_started, checked,
                                     target_kind='segment.bounds')
                errors = [abs(a-b) if enabled else 0. for a, b, enabled in zip(target, observed, mask)]
            # HAL 会限制单次目标领先量；只续发已经确认成功的绝对运动目标，不重复夹爪命令。
            if any(error > tolerance for i, (error, tolerance) in enumerate(zip(errors, TOLERANCE)) if i % 7 != 6):
                await self._send(target, config, token, include_grippers=False)
            else:
                current = await asyncio.to_thread(self.svc.settings.get_config)
                if any(current.get(key) != config.get(key) for key in ('motion', 'teleop', 'gripper', 'force')):
                    raise RuntimeError('回放期间控制配置发生变化')

    async def _run(self, dataset_id, episode_id, speed, token, selected=None):
        began = False
        try:
            with self.safety.operation():
                self._check(token)
                data = await asyncio.to_thread(self.svc.recorder.load_replay_episode, dataset_id, episode_id)
                self._participation = self._resolve_participation(data, selected)
                self._status["participation"] = self._participation
                await asyncio.to_thread(validate_episode, data)
                config = await asyncio.to_thread(self.svc.settings.get_config)
                await asyncio.to_thread(self._validate_config, data, config)
                plan = await asyncio.to_thread(build_replay_timing, data, config, self._participation, speed)
                self._status.update(timing=plan['summary'], fps=data['fps'], completedFrames=0,
                                    elapsedS=0., effectiveSpeed=0., feedbackWaitS=0., waitingChannels=[])
                self._check(token)
                health = await self.svc.hal.health()
                if "replay_absolute_target_v1" not in (health.capabilities or []):
                    raise RuntimeError("HAL 不支持绝对目标回放，请配套构建并部署 HAL 与夹爪 worker")
                if not all(action_mask(self._participation)) and "record_participation_v1" not in (health.capabilities or []):
                    raise RuntimeError("HAL 不支持参与侧隔离，请部署配套 HAL 后回放")
                record_status = await asyncio.to_thread(self.svc.recorder.status)
                mapper = self.svc.teleop_mapper.status(config)
                if record_status.get("active") or mapper.get("armed") or mapper.get("running") or self.svc.policy.auto_status(config)["running"]:
                    raise RuntimeError("请先停止录制、遥操作及策略执行")
                for side in hardware_sides(self._participation, "grippers"):
                    if not config["gripper"].get(f"{side}Enabled"):
                        label = "操作者左侧" if operator_side_for_hardware_side(side) == "left" else "操作者右侧"
                        raise RuntimeError(f"{label}夹爪未启用：请先启用该夹爪；仅回放机械臂时请选择不参与夹爪的片段或取消可选夹爪")
                self._check(token)
                began = True
                # 停止示教会退出原有采样线程；独立启动只读采样，不下发位置。
                if self._participation["grippers"]:
                    grippers = hardware_sides(self._participation, "grippers")
                    payload = native_gripper_payload(config, grippers[0], 0.)
                    payload.update(leftGripperParticipating="left" in grippers, rightGripperParticipating="right" in grippers)
                    await self.svc.hal.command("gripper.prepare_replay", payload)
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
                    if side not in hardware_sides(self._participation):
                        continue
                    for i, enabled in enumerate(config["teleop"][f"{side}EnabledAxes"]):
                        if not enabled and any(abs(row[key][offset+i] - observed[offset+i]) > TOLERANCE[offset+i]
                            for row in data["rows"] for key in ("action", "observation.state")):
                            raise RuntimeError("回放包含未启用轴的运动")
                self._check(token)
                for side in hardware_sides(self._participation):
                    await self.svc.commands.enable_motion_side(side)
                    self._check(token)
                self._status.update(phase="aligning", totalFrames=len(data["rows"]))
                await self._settle(initial, config, token)
                self._status["phase"] = "running"
                self._started_at = time.monotonic()
                previous = initial
                previous_sent_at_ms = None
                for index, (row, segment) in enumerate(zip(data['rows'], plan['segments'])):
                    observed, _ = await self._read_tracking(data, index, previous, config, token, previous_sent_at_ms)
                    await self._send(row['action'], config, token)
                    previous_sent_at_ms = time.time() * 1000
                    self._status['frame'] = index + 1
                    await self._wait_segment(data, index, row['action'], observed, segment['durationS'],
                                             config, token, previous_sent_at_ms)
                    self._status['completedFrames'] = index + 1
                    self._update_progress()
                    previous = row['action']
                self._status["phase"] = "settling"
                await self._settle(previous, config, token)
                for side in hardware_sides(self._participation):
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
            if self._status.get("trackingFault"):
                self.svc.logs.error("[HAL]", "event=replay_tracking_fault " + json.dumps(self._status["trackingFault"], ensure_ascii=False, allow_nan=False))
        finally:
            self._update_progress()
            self._started_at = None
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
