# Teleop Routing Audit

Date: 2026-05-27

This note records the current AppStation M0 teleoperation routing so future debugging can check the intended behavior without reconstructing it from logs.

## Current Routing

| Operator input | Arm target | Gripper target |
| --- | --- | --- |
| Left Omega.7 | Right arm | Right gripper, COM9 / slave 9 |
| Right Omega.7 | Left arm | Left gripper, COM8 / slave 10 |

The arm routing is intentionally crossed through `teleop.swapTeleopChannels=true`.
The gripper routing follows the crossed arm target through:

```json
{
  "leftSourceHand": "PhysicalRight",
  "rightSourceHand": "PhysicalLeft"
}
```

## Code Anchors

- Arm channel swap:
  - `backend/app.py`: `teleop_target_side_for_source()`
  - `hal/src/NativeTeleopController.cpp`: `targetSide = swapTeleopChannels ? sideFromIndex(1 - sourceIndex) : sourceSide`
- Gripper source mapping:
  - `backend/core/defaults.py`: default `leftSourceHand/rightSourceHand`
  - `backend/core/config.py`: runtime migration from old same-side gripper mapping to crossed target-arm mapping
  - `backend/services/teleop_mapping.py`: HAL-native payload fallback values
  - `hal/include/NativeTeleopController.h`: HAL-native default gripper source hands
  - `frontend/src/data.ts`: frontend default config

## Stability Guards

- `backend/app.py` stops stale HAL-native teleop on backend startup when no logical hand is connected.
- `backend/app.py` starts `manual-gripper` before manual native gripper commands.
- `backend/services/teleop_mapping.py` serializes native transitions, skips duplicate native starts only when the payload is unchanged, and clears local state even when native stop fails.
- `hal/src/NativeTeleopController.cpp` clears `logicalConnected_` on stop and exposes logical connection state in `/teleop/native/status`.
- `hal/src/JodellGripperDriver.cpp` treats the Jodell DLL as single-active-COM-port and closes the other active port before switching.
- `hal/src/LTDMCDriver.cpp` treats work-origin deltas within 10 pulse as already settled before calling `dmc_pmove`, avoiding startup home failures on near-zero hardware jitter such as `deltaPulse=4`.

## Verification Snapshot

Latest live checks on 2026-05-27 returned:

- Backend `/api/settings`: `leftSourceHand=PhysicalRight`, `rightSourceHand=PhysicalLeft`, `swapTeleopChannels=true`.
- HAL `/health`: `ltdmc_ok=true`, `omega7_ok=true`.
- HAL `/teleop/native/status`: `running=false`, `logicalConnected=[false,false]`, `lastError=""` after startup cleanup.
- Backend `/api/teleop/gripper/status`: `nativeManaged=true`, `running=false`, `requestedRunning=false`.

Regression checks:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_hardware_defaults.py::test_omega7_teleop_defaults_match_icf_strategy backend\tests\test_hardware_defaults.py::test_gripper_teleop_defaults_match_omega7_gap_range backend\tests\test_teleop_mapping.py::test_settings_migration_updates_existing_runtime_to_icf_teleop_strategy backend\tests\test_gripper_tele_service.py backend\tests\test_teleop_mapping.py::test_native_teleop_running_update_uses_start_without_extra_configure backend\tests\test_teleop_mapping.py::test_native_teleop_running_duplicate_start_skips_same_payload backend\tests\test_teleop_mapping.py::test_native_gripper_teleop_start_enables_hal_gripper_follow backend\tests\test_teleop_mapping.py::test_native_teleop_stop_failure_still_clears_local_state backend\tests\test_app.py::test_native_gripper_command_starts_manual_source_before_hal_command backend\tests\test_hal_source_contracts.py::test_hal_native_jodell_driver_closes_other_active_port_before_switching backend\tests\test_hal_source_contracts.py::test_hal_native_stop_clears_logical_connection_state -q
```

Current broader result: `75 passed` for the focused backend teleop/gripper suites, plus `3 passed` for the HAL source-contract checks that cover home-origin, native gripper commands, and the no-motion acceptance script.

```powershell
npm.cmd run typecheck
```

Expected result: `tsc -b` exits 0.

## Remaining Field Check

To prove end-to-end usability, perform a supervised hardware check from the UI:

1. Connect both Omega.7 devices.
2. Move the left Omega.7 and confirm the right arm target reacts.
3. Move the right Omega.7 and confirm the left arm target reacts.
4. Pinch/release the left Omega.7 and confirm the right gripper follows.
5. Pinch/release the right Omega.7 and confirm the left gripper follows.
6. Exercise right Omega.7 Yaw and confirm the left arm Yaw reacts.
7. Confirm `/api/teleop/gripper/status` reports no side error after the test.
8. Under supervision, run one return-to-work-origin path and confirm no near-zero `dmc_pmove ret=22` startup/home failure appears in the log.

Do not mark the overall teleop usability goal complete until this field check is observed.
