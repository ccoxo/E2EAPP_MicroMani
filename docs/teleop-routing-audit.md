# Teleop Routing Audit

Date: 2026-06-17

This note records the current AppStation M0 teleoperation routing after the operator-view side rename.

## Current Routing

| Operator input | Operator target | Hardware target | Gripper source hand | Gripper hardware |
| --- | --- | --- | --- | --- |
| Left Omega.7 | Left arm | Right hardware arm | `PhysicalLeft` | Hardware right gripper, COM9 / slave 9 |
| Right Omega.7 | Right arm | Left hardware arm | `PhysicalRight` | Hardware left gripper, COM8 / slave 10 |

Arm hardware routing is still crossed through `teleop.swapTeleopChannels=true`. The UI and backend operator-facing side names are not crossed.

Gripper source hands now follow the operator hand:

```json
{
  "leftSourceHand": "PhysicalLeft",
  "rightSourceHand": "PhysicalRight"
}
```

Backend command routing maps the operator-side gripper command to the crossed hardware gripper through `backend.core.operator_view.hardware_side_for_operator_side()`.

## Code Anchors

- Operator-to-hardware side mapping:
  - `backend/core/operator_view.py`
  - `backend/services/gripper_tele_service.py`
- Gripper source defaults and migration:
  - `backend/core/defaults.py`
  - `backend/core/config.py`
  - `scripts/import_icf_teleop_config.py`
- HAL-native teleop payload:
  - `backend/services/teleop_mapping.py`
  - `hal/src/NativeTeleopController.cpp`
- Frontend defaults:
  - `frontend/src/data.ts`

## Verification Snapshot

Current checks should show:

- Backend `/api/settings`: `leftSourceHand=PhysicalLeft`, `rightSourceHand=PhysicalRight`, `swapTeleopChannels=true`.
- Backend `/api/health`: HAL and Omega.7 are connected; force/PICO may still report hardware-specific faults.
- Backend `/api/teleop/mapping/status`: teleop is blocked until both motion work origins are captured.
- HAL DDS read-only topics publish with source `hal-cpp`.

Focused regression checks:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_operator_view.py backend\tests\test_import_icf_teleop_config.py backend\tests\test_hardware_defaults.py backend\tests\test_teleop_mapping.py::test_settings_migration_updates_existing_runtime_to_icf_teleop_strategy backend\tests\test_teleop_mapping.py::test_settings_migration_preserves_user_tuned_gripper_sources -q
```

```powershell
npm.cmd test -- --run src/data.test.ts
```

## Remaining Field Check

End-to-end usability still requires a supervised hardware check:

1. Restart HAL through `scripts\start-hal.ps1 -Restart` so any `.next.exe` build is promoted.
2. Confirm DDS command replies after restart.
3. Capture both motion work origins from a safe pose.
4. Connect teleop from the UI and verify left/right operator controls move the expected operator-side arm.
5. Verify left/right gripper following matches the operator hand while commanding the crossed hardware gripper.
