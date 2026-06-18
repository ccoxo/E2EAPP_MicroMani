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
        "hal.reconnect": ("GET", "/health"),
        "motion.emergency_stop": ("POST", "/motion/emergency_stop"),
        "motion.home_all": ("POST", "/motion/home_all"),
        "motion.home_origin_side": ("POST", "/motion/home_origin_side"),
        "motion.enable_side": ("POST", "/motion/enable_side"),
        "motion.disable_side": ("POST", "/motion/disable_side"),
        "motion.home_side": ("POST", "/motion/home_side"),
        "motion.manual_axis_move": ("POST", "/motion/manual_axis_move"),
        "motion.teleop_target_update": ("POST", "/motion/teleop_target_update"),
        "motion.teleop_stop_side": ("POST", "/motion/teleop_stop_side"),
        "omega7.gravity_compensation": ("POST", "/omega7/gravity_compensation"),
        "omega7.zero_force_feedback": ("POST", "/omega7/zero_force_feedback"),
        "teleop.native.configure": ("POST", "/teleop/native/configure"),
        "teleop.native.start": ("POST", "/teleop/native/start"),
        "teleop.native.stop": ("POST", "/teleop/native/stop"),
        "teleop.native.status": ("GET", "/teleop/native/status"),
        "teleop.native.gripper_command": ("POST", "/teleop/native/gripper_command"),
        "gripper.command": ("POST", "/gripper/command"),
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
    assert command_request_policy("motion.manual_axis_move", 5.0) == (5.0, 2)
    assert command_request_policy("motion.home_all", 5.0) == (75.0, 1)
    assert command_request_policy("motion.home_origin_side", 5.0) == (75.0, 1)
    assert command_request_policy("motion.home_side", 80.0) == (80.0, 1)


def test_unknown_hal_command_has_clear_error() -> None:
    with pytest.raises(RuntimeError, match="HAL command is not mapped: missing.command"):
        command_spec("missing.command")
