# MicroMani Runtime And Teleop Fixes Implementation Plan

> Required workflow used: `executing-plans`, `systematic-debugging`, `test-driven-development`, and `verification-before-completion`.

**Goal:** Make items 6-14 measurably closer to true: hardware recognition before operation, clean shutdown, persistent logs, no F12 hotkey, correct work-origin soft-limit handling, corrected Yaw parameters, adjusted manual directions, and more responsive native gripper teleop.

**Architecture:** Keep the fixes in existing boundaries. Backend owns precheck/logging/config payloads, scripts own process cleanup, frontend owns hotkey/text and operator status, and HAL owns pulse constants plus native motion execution.

**Tech Stack:** FastAPI/Python backend, React/Vite frontend, PowerShell launch scripts, C++ HAL, pytest, Vitest, source-contract tests.

---

### Task 1: Persistent Runtime Logs

- [x] Write failing tests that `LogService` writes appended entries to a per-session `.log` file and keeps normal in-memory behavior.
- [x] Implement optional file sink and wire app startup to `backend/runtime/logs`.
- [x] Run `pytest backend/tests/test_diagnostic_logging.py -q`.

### Task 2: Shutdown Stack Cleanup

- [x] Write source-contract tests proving auto shutdown is enabled in the frontend dev environment and stop-stack removes process trees for known service ports.
- [x] Enable `VITE_AUTO_SHUTDOWN_ON_CLOSE=true` in `start-stack.ps1`.
- [x] Broaden `stop-stack.ps1` to kill all listener PIDs on app ports plus their process trees.
- [x] Add backend shutdown cleanup for telemetry hardware futures and camera captures.
- [x] Run targeted backend tests.

### Task 3: Cancel F12 Emergency Hotkey

- [x] Change frontend tests so F12 no longer triggers emergency stop and button labels no longer mention F12.
- [x] Remove the global F12 key listener and visible F12 copy.
- [x] Run `npm test -- --run src/App.test.tsx` from `frontend`.

### Task 4: Work-Origin Soft Limits

- [x] Add tests proving HAL-native payload sends soft-limit offsets and work-origin pulses separately.
- [x] Remove backend-side origin addition in `_soft_limit_arrays`; keep HAL as the single place that converts offsets to absolute limits.
- [x] Add source-contract coverage that native `effectiveSoftLimits` adds origin exactly once.
- [x] Run targeted backend tests.

### Task 5: Yaw Parameters And Direction Signs

- [x] Write/update tests for left Yaw `3333.333333`, right Yaw `666`, and the requested manual direction inversions.
- [x] Update defaults, runtime config, frontend defaults, and HAL constants consistently.
- [x] Adjust manual jog direction signs for right XYZ and left Y.
- [x] Run targeted backend and frontend tests.

### Task 6: Hardware Recognition And Gripper Sync

- [x] Add backend status fields for Omega.7 serials and Jodell COM/slave recognition where available.
- [x] Make precheck require HAL, cameras, Omega.7 physical recognition, and gripper recognition before recording starts.
- [x] Lower native gripper deadband/interval and raise speed only inside existing config fields.
- [x] Run targeted backend and frontend tests.

### Task 7: Final Verification

- [x] Run `backend/tests/test_app.py` end-to-end.
- [x] Run all backend tests outside `test_app.py`.
- [x] Run focused backend tests for logging, runtime, teleop mapping, command diagnostics, HAL source contracts, and dataset recorder.
- [x] Run focused frontend test file.
- [x] Run frontend production build.
- [x] Build HAL with the available compiler/vendor environment.
- [x] Record unresolved hardware-only checks for real-machine validation: right-arm overall following, gripper physical sync, left X motion, and live direction confirmation.
