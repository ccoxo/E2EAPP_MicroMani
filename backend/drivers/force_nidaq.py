from __future__ import annotations

import time
from dataclasses import dataclass, field
from importlib import import_module
from math import pi
from pathlib import Path
from threading import Lock
from typing import Any
from xml.etree import ElementTree

FORCE_AXES: tuple[str, ...] = ("Fx", "Fy", "Fz", "Tx", "Ty", "Tz")

FALLBACK_CALIBRATION_MATRICES: dict[str, list[list[float]]] = {
    # Reference project docs: FT32918 Nano17 SI-12-0.12, units N and N-mm.
    "left": [
        [-0.00458, -0.00979, 0.03928, -1.58147, -0.02651, 1.57113],
        [-0.00440, 2.11251, 0.02948, -0.93552, 0.00504, -0.90799],
        [1.84054, 0.00239, 1.88264, -0.05335, 1.89681, -0.04189],
        [0.04046, 12.81517, 10.73979, -5.99236, -10.65846, -5.25977],
        [-11.68856, -0.00616, 6.09687, 9.46016, 6.41318, -9.68378],
        [-0.02776, 8.47642, -0.06629, 7.73309, -0.17538, 7.63648],
    ],
    # Reference project docs: FT38799 Nano17 SI-12-0.12, units N and N-mm.
    "right": [
        [-0.03644, 0.00548, 0.03009, -1.64617, -0.10005, 1.63483],
        [0.01218, 2.06357, 0.02118, -0.96496, 0.05992, -0.94768],
        [1.87469, -0.15715, 1.88000, -0.04157, 1.93623, 0.00480],
        [-0.08070, 12.50274, 10.66395, -6.06894, -10.51729, -5.71382],
        [-12.04281, 0.87279, 5.90450, 9.85583, 6.59386, -9.89980],
        [0.02289, 8.51089, 0.07304, 7.52682, -0.63884, 7.86752],
    ],
}


@dataclass
class ForceProbeResult:
    ok: bool
    message: str
    left: list[float]
    right: list[float]
    left_window: list[list[float]] = field(default_factory=list)
    right_window: list[list[float]] = field(default_factory=list)
    sample_hz: float = 0.0
    sample_count: int = 0
    calibration: dict[str, Any] = field(default_factory=dict)
    sample_monotonic_s: float = 0.0


class _PersistentTask:
    """Holds a long-lived nidaqmx Task so we don't pay 200-500ms on every sample.

    The previous implementation opened a fresh Task() per side per tick, which
    was the dominant source of latency on the force-feedback display. NI-DAQmx
    is happy to keep a task open and re-read it; we just need to recreate it
    when the channel string or input mode changes.
    """

    def __init__(self) -> None:
        self.task: Any | None = None
        self.signature: tuple[Any, ...] | None = None

    def get(
        self,
        nidaqmx: Any,
        constants: Any,
        channel: str,
        terminal_mode: str,
        v_min: float,
        v_max: float,
        sample_hz: float,
    ) -> Any:
        terminal_config = getattr(constants.TerminalConfiguration, terminal_mode, constants.TerminalConfiguration.DIFF)
        signature = (channel, terminal_mode, v_min, v_max, sample_hz)
        if self.task is not None and self.signature == signature:
            return self.task
        # Channel / range changed: tear down the old task before re-creating.
        self.close()
        task = nidaqmx.Task()
        try:
            task.ai_channels.add_ai_voltage_chan(
                channel,
                terminal_config=terminal_config,
                min_val=float(v_min),
                max_val=float(v_max),
            )
            if sample_hz > 0:
                task.timing.cfg_samp_clk_timing(
                    rate=float(sample_hz),
                    sample_mode=constants.AcquisitionType.CONTINUOUS,
                    samps_per_chan=max(1000, int(sample_hz)),
                )
            task.start()
        except Exception:
            try:
                task.close()
            except Exception:
                pass
            raise
        self.task = task
        self.signature = signature
        return task

    def close(self) -> None:
        if self.task is None:
            return
        try:
            try:
                self.task.stop()
            except Exception:
                pass
            self.task.close()
        finally:
            self.task = None
            self.signature = None


class NidaqForceDriver:
    def __init__(self) -> None:
        self._sample_lock = Lock()
        self._last_sample_at = 0.0
        self._last_sample = ForceProbeResult(True, "force sample not read yet", [0.0] * 6, [0.0] * 6)
        self._left_task = _PersistentTask()
        self._right_task = _PersistentTask()
        self._tare_bias = {"left": [0.0] * 6, "right": [0.0] * 6}
        self._lpf_state = {"left": [0.0] * 6, "right": [0.0] * 6}
        self._lpf_initialized = {"left": False, "right": False}
        self._lpf_signature: tuple[bool, float, float] | None = None
        self._calibration_cache: dict[tuple[str, str, bool], tuple[list[list[float]], str]] = {}

    def probe(self, config: dict[str, Any]) -> ForceProbeResult:
        try:
            nidaqmx = import_module("nidaqmx")
            constants = import_module("nidaqmx.constants")
        except Exception as exc:
            return ForceProbeResult(
                False,
                f"NI-DAQmx Python import failed: {exc}",
                [0.0] * 6,
                [0.0] * 6,
                sample_monotonic_s=time.monotonic(),
            )

        errors: list[str] = []
        with self._sample_lock:
            for label, persistent, channel in [
                ("left", self._left_task, str(config["force"].get("leftIp", "Dev5/ai0:5"))),
                ("right", self._right_task, str(config["force"].get("rightIp", "Dev3/ai0:5"))),
            ]:
                try:
                    persistent.get(
                        nidaqmx,
                        constants,
                        channel,
                        str(config["force"].get("inputMode", "DIFF")).upper(),
                        float(config["force"].get("voltageMin", -10)),
                        float(config["force"].get("voltageMax", 10)),
                        self._sample_hz(config),
                    )
                except Exception as exc:
                    errors.append(f"{label} {channel}: {exc}")
                    persistent.close()
        if errors:
            return ForceProbeResult(
                False,
                "; ".join(errors),
                [0.0] * 6,
                [0.0] * 6,
                sample_monotonic_s=time.monotonic(),
            )
        calibration = self.calibration_info(config)
        return ForceProbeResult(
            True,
            "NI-DAQmx force channels available",
            [0.0] * 6,
            [0.0] * 6,
            calibration=calibration,
            sample_monotonic_s=time.monotonic(),
        )

    def sample(self, config: dict[str, Any]) -> ForceProbeResult:
        now = time.monotonic()
        with self._sample_lock:
            # UI telemetry only needs a low-rate latest value. Dataset recording
            # uses sample_window() and bypasses this 30Hz cache.
            if now - self._last_sample_at < 0.033:
                return self._last_sample
            try:
                nidaqmx = import_module("nidaqmx")
                constants = import_module("nidaqmx.constants")
            except Exception as exc:
                self._last_sample = ForceProbeResult(
                    False,
                    f"NI-DAQmx import failed: {exc}",
                    [0.0] * 6,
                    [0.0] * 6,
                    sample_monotonic_s=time.monotonic(),
                )
                self._last_sample_at = now
                return self._last_sample
            try:
                left_raw = self._read_channel_window(
                    config,
                    nidaqmx,
                    constants,
                    self._left_task,
                    str(config["force"].get("leftIp", "Dev5/ai0:5")),
                    1,
                )
                right_raw = self._read_channel_window(
                    config,
                    nidaqmx,
                    constants,
                    self._right_task,
                    str(config["force"].get("rightIp", "Dev3/ai0:5")),
                    1,
                )
                left_window, right_window = self._process_raw_windows(config, left_raw, right_raw)
            except Exception as exc:
                # On error, drop the persistent tasks so the next call rebuilds them.
                self._left_task.close()
                self._right_task.close()
                self._last_sample = ForceProbeResult(
                    False,
                    f"NI-DAQmx force sample failed: {exc}",
                    [0.0] * 6,
                    [0.0] * 6,
                    sample_monotonic_s=time.monotonic(),
                )
            else:
                sampled_at = time.monotonic()
                sample_hz = self._sample_hz(config)
                left = left_window[-1]
                right = right_window[-1]
                self._last_sample = ForceProbeResult(
                    True,
                    "NI-DAQmx force sample read and calibrated",
                    left,
                    right,
                    left_window,
                    right_window,
                    sample_hz,
                    1,
                    self.calibration_info(config),
                    sampled_at,
                )
            self._last_sample_at = now
            return self._last_sample

    def sample_window(self, config: dict[str, Any], samples_per_channel: int) -> ForceProbeResult:
        sample_hz = self._sample_hz(config)
        sample_count = max(1, min(int(samples_per_channel), 512))
        now = time.monotonic()
        with self._sample_lock:
            try:
                nidaqmx = import_module("nidaqmx")
                constants = import_module("nidaqmx.constants")
            except Exception as exc:
                self._last_sample = self._window_from_latest_locked(
                    sample_hz,
                    sample_count,
                    f"NI-DAQmx import failed: {exc}",
                )
                self._last_sample_at = now
                return self._last_sample
            try:
                left_raw_window = self._read_channel_window(
                    config,
                    nidaqmx,
                    constants,
                    self._left_task,
                    str(config["force"].get("leftIp", "Dev5/ai0:5")),
                    sample_count,
                )
                right_raw_window = self._read_channel_window(
                    config,
                    nidaqmx,
                    constants,
                    self._right_task,
                    str(config["force"].get("rightIp", "Dev3/ai0:5")),
                    sample_count,
                )
                left_window, right_window = self._process_raw_windows(config, left_raw_window, right_raw_window)
            except Exception as exc:
                self._left_task.close()
                self._right_task.close()
                self._last_sample = self._window_from_latest_locked(
                    sample_hz,
                    sample_count,
                    f"NI-DAQmx force window sample failed: {exc}",
                )
            else:
                left = left_window[-1]
                right = right_window[-1]
                self._last_sample = ForceProbeResult(
                    True,
                    "NI-DAQmx force window read",
                    left,
                    right,
                    left_window,
                    right_window,
                    sample_hz,
                    sample_count,
                    self.calibration_info(config),
                    time.monotonic(),
                )
            self._last_sample_at = now
            return self._last_sample

    def latest_window(self, config: dict[str, Any], samples_per_channel: int) -> ForceProbeResult:
        """返回最近一次力觉窗口，避免录制 tick 被同步采样阻塞。"""
        sample_hz = self._sample_hz(config)
        sample_count = max(1, min(int(samples_per_channel), 512))
        with self._sample_lock:
            return self._window_from_latest_locked(sample_hz, sample_count, "latest force window cache")

    def _window_from_latest_locked(self, sample_hz: float, sample_count: int, message: str) -> ForceProbeResult:
        # NI-DAQmx 失败时复用最近标量，保证当前帧仍有可写入的 6 维力觉值。
        latest = self._last_sample
        left = list(latest.left or [0.0] * 6)
        right = list(latest.right or [0.0] * 6)
        left_window = self._fit_window(latest.left_window, left, sample_count)
        right_window = self._fit_window(latest.right_window, right, sample_count)
        ok = bool(latest.ok and latest.sample_count > 0)
        status = message if ok else f"{message}; using scalar fallback"
        return ForceProbeResult(
            ok,
            status,
            left,
            right,
            left_window,
            right_window,
            sample_hz,
            sample_count,
            latest.calibration,
            latest.sample_monotonic_s,
        )

    def _fit_window(self, window: list[list[float]], latest: list[float], sample_count: int) -> list[list[float]]:
        # 历史窗口不足时用最早样本补齐，保持调用方看到固定长度。
        rows = [([float(value) for value in row] + [0.0] * 6)[:6] for row in window if isinstance(row, list)]
        if not rows:
            row = (list(latest) + [0.0] * 6)[:6]
            return [list(row) for _ in range(sample_count)]
        if len(rows) < sample_count:
            rows = [list(rows[0]) for _ in range(sample_count - len(rows))] + rows
        return rows[-sample_count:]

    def tare(self, config: dict[str, Any], side: str | None = None) -> ForceProbeResult:
        target = side or "both"
        sample_hz = self._sample_hz(config)
        sample_count = self._tare_sample_count(config)
        try:
            nidaqmx = import_module("nidaqmx")
            constants = import_module("nidaqmx.constants")
        except Exception as exc:
            return ForceProbeResult(
                False,
                f"NI-DAQmx import failed: {exc}",
                [0.0] * 6,
                [0.0] * 6,
                sample_monotonic_s=time.monotonic(),
            )
        with self._sample_lock:
            try:
                left_raw = self._read_channel_window(
                    config,
                    nidaqmx,
                    constants,
                    self._left_task,
                    str(config["force"].get("leftIp", "Dev5/ai0:5")),
                    sample_count,
                )
                right_raw = self._read_channel_window(
                    config,
                    nidaqmx,
                    constants,
                    self._right_task,
                    str(config["force"].get("rightIp", "Dev3/ai0:5")),
                    sample_count,
                )
                logical_left, logical_right = self._logical_raw_windows(config, left_raw, right_raw)
                if side in {None, "left"}:
                    self._tare_bias["left"] = self._average_window(logical_left)
                    self._lpf_initialized["left"] = False
                if side in {None, "right"}:
                    self._tare_bias["right"] = self._average_window(logical_right)
                    self._lpf_initialized["right"] = False
                processed_left, processed_right = self._process_raw_windows(config, left_raw, right_raw)
            except Exception as exc:
                self._left_task.close()
                self._right_task.close()
                return ForceProbeResult(
                    False,
                    f"NI-DAQmx tare failed for {target}: {exc}",
                    [0.0] * 6,
                    [0.0] * 6,
                    sample_hz=sample_hz,
                    sample_count=sample_count,
                    calibration=self.calibration_info(config),
                    sample_monotonic_s=time.monotonic(),
                )
        left = processed_left[-1]
        right = processed_right[-1]
        self._last_sample = ForceProbeResult(
            True,
            f"tare updated for {target}",
            left,
            right,
            processed_left,
            processed_right,
            sample_hz,
            sample_count,
            self.calibration_info(config),
            time.monotonic(),
        )
        self._last_sample_at = time.monotonic()
        return self._last_sample

    def _read_channel_window(
        self,
        config: dict[str, Any],
        nidaqmx: Any,
        constants: Any,
        persistent: _PersistentTask,
        channel: str,
        samples_per_channel: int,
    ) -> list[list[float]]:
        mode_name = str(config["force"].get("inputMode", "DIFF")).upper()
        v_min = float(config["force"].get("voltageMin", -10))
        v_max = float(config["force"].get("voltageMax", 10))
        sample_hz = self._sample_hz(config)
        task = persistent.get(nidaqmx, constants, channel, mode_name, v_min, v_max, sample_hz)
        sample_count = max(1, min(int(samples_per_channel), 512))
        timeout = max(0.1, sample_count / max(sample_hz, 1.0) + 0.1)
        raw = task.read(number_of_samples_per_channel=sample_count, timeout=timeout)
        return self._coerce_window(raw, sample_count)

    def calibration_info(self, config: dict[str, Any]) -> dict[str, Any]:
        return {
            side: {
                "source": self._calibration_source(config, side),
                "path": str(config.get("force", {}).get(f"{side}CalibrationPath", "")),
                "tareBiasV": list(self._tare_bias[side]),
            }
            for side in ("left", "right")
        }

    def _coerce_window(self, raw: Any, expected_samples: int) -> list[list[float]]:
        if isinstance(raw, (int, float)):
            return [[float(raw), 0.0, 0.0, 0.0, 0.0, 0.0] for _ in range(expected_samples)]
        if isinstance(raw, list):
            if raw and all(isinstance(item, list) for item in raw):
                channels = [
                    [float(value) for value in item]
                    for item in raw[:6]
                    if isinstance(item, list)
                ]
                sample_count = max(expected_samples, *(len(channel) for channel in channels))
                rows: list[list[float]] = []
                for sample_index in range(sample_count):
                    row = []
                    for channel in range(6):
                        values = channels[channel] if channel < len(channels) else []
                        if sample_index < len(values):
                            row.append(values[sample_index])
                        else:
                            row.append(values[-1] if values else 0.0)
                    rows.append(row)
                return rows[-expected_samples:]
            values = [float(item) for item in raw]
            if len(values) == 6 or expected_samples == 1:
                row = (values + [0.0] * 6)[:6]
                return [row for _ in range(expected_samples)]
            return [[value, 0.0, 0.0, 0.0, 0.0, 0.0] for value in values[-expected_samples:]]
        row = [float(raw), 0.0, 0.0, 0.0, 0.0, 0.0]
        return [row for _ in range(expected_samples)]

    def _process_raw_windows(
        self,
        config: dict[str, Any],
        left_raw: list[list[float]],
        right_raw: list[list[float]],
    ) -> tuple[list[list[float]], list[list[float]]]:
        self._reset_lpf_if_config_changed(config)
        logical_left, logical_right = self._logical_raw_windows(config, left_raw, right_raw)
        return (
            [self._process_raw_sample(config, "left", row) for row in logical_left],
            [self._process_raw_sample(config, "right", row) for row in logical_right],
        )

    def _logical_raw_windows(
        self,
        config: dict[str, Any],
        left_raw: list[list[float]],
        right_raw: list[list[float]],
    ) -> tuple[list[list[float]], list[list[float]]]:
        if bool(config.get("force", {}).get("swapHands", False)):
            return right_raw, left_raw
        return left_raw, right_raw

    def _process_raw_sample(self, config: dict[str, Any], side: str, raw: list[float]) -> list[float]:
        matrix = self._calibration_matrix(config, side)
        bias = self._tare_bias[side]
        corrected = [float(raw[index] if index < len(raw) else 0.0) - bias[index] for index in range(6)]
        values = [
            sum(matrix[row][col] * corrected[col] for col in range(6))
            for row in range(6)
        ]
        if bool(config.get("force", {}).get("calibrationEnabled", True)):
            values[3:6] = [value / 1000.0 for value in values[3:6]]
        return self._apply_lowpass(config, side, values)

    def _apply_lowpass(self, config: dict[str, Any], side: str, values: list[float]) -> list[float]:
        force_config = config.get("force", {})
        enabled = bool(force_config.get("lowpassEnabled", True))
        cutoff_hz = self._lowpass_cutoff_hz(config)
        if not enabled or cutoff_hz <= 0:
            self._lpf_state[side] = list(values)
            self._lpf_initialized[side] = True
            return list(values)
        sample_hz = self._sample_hz(config)
        dt = 1.0 / max(sample_hz, 1.0)
        rc = 1.0 / (2.0 * pi * cutoff_hz)
        alpha = min(1.0, max(0.0, dt / (rc + dt)))
        if not self._lpf_initialized[side]:
            self._lpf_state[side] = list(values)
            self._lpf_initialized[side] = True
            return list(values)
        state = self._lpf_state[side]
        for index, value in enumerate(values):
            state[index] = state[index] + alpha * (value - state[index])
        return list(state)

    def _reset_lpf_if_config_changed(self, config: dict[str, Any]) -> None:
        signature = (
            bool(config.get("force", {}).get("lowpassEnabled", True)),
            self._lowpass_cutoff_hz(config),
            self._sample_hz(config),
        )
        if signature == self._lpf_signature:
            return
        self._lpf_signature = signature
        self._lpf_initialized = {"left": False, "right": False}

    def _calibration_matrix(self, config: dict[str, Any], side: str) -> list[list[float]]:
        enabled = bool(config.get("force", {}).get("calibrationEnabled", True))
        path = str(config.get("force", {}).get(f"{side}CalibrationPath", "")).strip()
        key = (side, path, enabled)
        cached = self._calibration_cache.get(key)
        if cached is not None:
            return cached[0]
        if not enabled:
            matrix = self._identity_matrix()
            source = "identity-disabled"
        else:
            matrix, source = self._load_calibration_matrix(path, side)
        self._calibration_cache[key] = (matrix, source)
        return matrix

    def _calibration_source(self, config: dict[str, Any], side: str) -> str:
        enabled = bool(config.get("force", {}).get("calibrationEnabled", True))
        path = str(config.get("force", {}).get(f"{side}CalibrationPath", "")).strip()
        key = (side, path, enabled)
        cached = self._calibration_cache.get(key)
        if cached is None:
            self._calibration_matrix(config, side)
            cached = self._calibration_cache.get(key)
        return cached[1] if cached is not None else "unknown"

    def _load_calibration_matrix(self, path: str, side: str) -> tuple[list[list[float]], str]:
        if path:
            candidate = Path(path)
            if candidate.exists():
                try:
                    return self._parse_ati_calibration(candidate), f"file:{candidate}"
                except Exception:
                    pass
        fallback = FALLBACK_CALIBRATION_MATRICES[side]
        return ([list(row) for row in fallback], f"embedded-reference:{side}")

    def _parse_ati_calibration(self, path: Path) -> list[list[float]]:
        root = ElementTree.parse(path).getroot()
        rows: dict[str, list[float]] = {}
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag != "UserAxis":
                continue
            name = str(element.attrib.get("Name", "")).strip()
            values = str(element.attrib.get("values", "")).strip()
            if name not in FORCE_AXES or not values:
                continue
            parts = values.split()
            if len(parts) != 6:
                raise ValueError(f"invalid calibration row {name} in {path}")
            rows[name] = [float(part) for part in parts]
        if any(axis not in rows for axis in FORCE_AXES):
            raise ValueError(f"incomplete ATI calibration matrix in {path}")
        return [rows[axis] for axis in FORCE_AXES]

    def _identity_matrix(self) -> list[list[float]]:
        return [[1.0 if row == col else 0.0 for col in range(6)] for row in range(6)]

    def _average_window(self, window: list[list[float]]) -> list[float]:
        if not window:
            return [0.0] * 6
        totals = [0.0] * 6
        for row in window:
            for index in range(6):
                totals[index] += float(row[index] if index < len(row) else 0.0)
        return [value / len(window) for value in totals]

    def _tare_sample_count(self, config: dict[str, Any]) -> int:
        try:
            configured = int(config.get("force", {}).get("tareSamples", 0))
        except (TypeError, ValueError):
            configured = 0
        if configured > 0:
            return min(max(configured, 1), 512)
        return min(max(int(round(self._sample_hz(config) * 0.2)), 10), 512)

    def _lowpass_cutoff_hz(self, config: dict[str, Any]) -> float:
        try:
            raw = float(config.get("force", {}).get("lowpassCutoffHz", 10))
        except (TypeError, ValueError):
            raw = 10.0
        return min(max(raw, 0.0), self._sample_hz(config) / 2.0)

    def _sample_hz(self, config: dict[str, Any]) -> float:
        try:
            raw = float(config["force"].get("sampleHz", 200))
        except (TypeError, ValueError):
            raw = 200.0
        return min(max(raw, 1.0), 10000.0)
