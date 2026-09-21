# 阅读导航 07｜测试与验证
# 职责：回归验证：共享命令契约、载荷展开以及回原点命令超时策略。
# 先看：test_shared_hal_command_protocol_covers_existing_real_hal_commands → test_hal_command_payload_flattens_teleop_target_deltas → test_hal_command_request_policy_keeps_home_commands_long_running → test_unknown_hal_command_has_clear_error。
# 全局阅读顺序与关联文件：docs/CODE_READING_GUIDE.md；逐文件目录：docs/SOURCE_INDEX.md。

from __future__ import annotations

import pytest

from backend.hal_client.protocol import (
    HAL_COMMANDS,
    command_request_policy,
    command_spec,
    hal_command_payload,
)


def test_shared_hal_command_protocol_covers_existing_real_hal_commands() -> None:
    expected_paths = {
        "control.lease": ("POST", "/control/lease"),
        "hal.reconnect": ("GET", "/health"),
        "motion.emergency_stop": ("POST", "/motion/emergency_stop"),
        "motion.acknowledge_estop": ("POST", "/motion/acknowledge_estop"),
        "motion.home_all": ("POST", "/motion/home_all"),
        "motion.home_origin_side": ("POST", "/motion/home_origin_side"),
        "motion.return_home_reference": ("POST", "/motion/return_home_reference"),
        "motion.enable_side": ("POST", "/motion/enable_side"),
        "motion.disable_side": ("POST", "/motion/disable_side"),
        "motion.home_side": ("POST", "/motion/home_side"),
        "motion.manual_axis_move": ("POST", "/motion/manual_axis_move"),
        "motion.teleop_target_update": ("POST", "/motion/teleop_target_update"),
        "motion.replay_absolute_target": ("POST", "/motion/replay_absolute_target"),
        "motion.teleop_stop_side": ("POST", "/motion/teleop_stop_side"),
        "omega7.gravity_compensation": ("POST", "/omega7/gravity_compensation"),
        "omega7.zero_force_feedback": ("POST", "/omega7/zero_force_feedback"),
        "force.configure": ("POST", "/force/configure"),
        "force.tare": ("POST", "/force/tare"),
        "teleop.native.configure": ("POST", "/teleop/native/configure"),
        "teleop.native.start": ("POST", "/teleop/native/start"),
        "teleop.native.stop": ("POST", "/teleop/native/stop"),
        "teleop.native.status": ("GET", "/teleop/native/status"),
        "teleop.native.gripper_command": ("POST", "/teleop/native/gripper_command"),
        "gripper.command": ("POST", "/gripper/command"),
        "gripper.prepare_replay": ("POST", "/gripper/prepare_replay"),
        "gripper.replay_target": ("POST", "/gripper/replay_target"),
    }

    assert set(HAL_COMMANDS) == set(expected_paths)
    for name, (method, path) in expected_paths.items():
        spec = command_spec(name)
        assert (spec.method, spec.path) == (method, path)


def test_hal_command_payload_flattens_teleop_target_deltas() -> None:
    payload = {
        "side": "left",
        "deltas": {"X": 12.5, "Yaw": -0.2, "ignored": 99},
        "sequence": 42,
    }

    request_payload = hal_command_payload("motion.teleop_target_update", payload)

    assert request_payload == {
        "side": "left",
        "deltas": {"X": 12.5, "Yaw": -0.2, "ignored": 99},
        "sequence": 42,
        "X": 12.5,
        "Yaw": -0.2,
    }
    assert payload == {
        "side": "left",
        "deltas": {"X": 12.5, "Yaw": -0.2, "ignored": 99},
        "sequence": 42,
    }


def test_hal_command_request_policy_keeps_home_commands_long_running() -> None:
    assert command_request_policy("motion.manual_axis_move", 5.0) == (5.0, 1)
    assert command_request_policy("motion.emergency_stop", 5.0) == (5.0, 2)
    assert command_request_policy("motion.home_all", 5.0) == (75.0, 1)
    assert command_request_policy("motion.home_origin_side", 5.0) == (75.0, 1)
    assert command_request_policy("motion.return_home_reference", 5.0) == (75.0, 1)
    assert command_request_policy("motion.home_side", 80.0) == (80.0, 1)


def test_unknown_hal_command_has_clear_error() -> None:
    with pytest.raises(RuntimeError, match="HAL command is not mapped: missing.command"):
        command_spec("missing.command")
