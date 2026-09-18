# HKVL-36A Motion-Aligned Six-Axis Force Signs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert all six HKVL channels to the effective positive direction of their bound motion card while retaining manufacturer-native raw diagnostics.

**Architecture:** Derive two six-element sign arrays from `motion.kinematics.*SignedPulsePerUnit`, pass them through the existing flat force configuration payload, and apply them exactly once in `ForceControlRuntime` after native Tare/filtering. All standard consumers then inherit the aligned contract; explicit `sensor*` fields preserve native diagnostics.

**Tech Stack:** C++17 HAL, PowerShell, Python/FastAPI backend, React/TypeScript, pytest, HAL native tests, Vitest

---

### Task 1: Add failing configuration contract tests

**Files:**
- Modify: `backend/tests/test_force_config.py`
- Modify: `backend/tests/test_stack_scripts.py`
- Modify: `hal/tests/ForceCoreTests.cpp`

- [x] **Step 1: Assert backend derivation.** Extend `test_hkvl_force_config_payload_matches_hal_flat_contract` with:

```python
assert payload["leftAxisSign"] == [-1.0, 1.0, -1.0, 1.0, -1.0, -1.0]
assert payload["rightAxisSign"] == [-1.0, -1.0, -1.0, 1.0, 1.0, 1.0]
```

- [x] **Step 2: Assert undefined directions are rejected.** Set one `leftSignedPulsePerUnit` item to zero and expect a `ValueError` containing `leftSignedPulsePerUnit`.
- [x] **Step 3: Assert startup injection.** Require `leftAxisSign`, `rightAxisSign`, and both signed-pulse field names in `scripts/start-hal.ps1`.
- [x] **Step 4: Assert HAL parsing and runtime behavior.** Add native tests that expect the confirmed defaults, JSON array parsing, all-six-channel aligned state, and aligned compliance Fx/Fz direction.
- [x] **Step 5: Run RED checks.** Run:

```text
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_force_config.py backend/tests/test_stack_scripts.py -q
cmd /c hal\build_hal.cmd
```

Expected: failures caused by missing axis-sign fields or missing transformation.

### Task 2: Derive and inject force-axis signs

**Files:**
- Modify: `backend/core/force_config.py`
- Modify: `scripts/start-hal.ps1`
- Modify: `hal/include/ForceControlRuntime.h`
- Modify: `hal/src/HalJson.cpp`
- Modify: `hal/src/ForceControlRuntime.cpp`

- [x] **Step 1: Add backend derivation.** Read the two six-element signed-pulse arrays and map each finite non-zero item with `-1.0 if value < 0 else 1.0`.
- [x] **Step 2: Add payload fields.** Emit `leftAxisSign` and `rightAxisSign` in `hal_force_config_payload`.
- [x] **Step 3: Add direct-start derivation.** Give `$forceRuntimeConfig` the confirmed fallback arrays and replace them from `motion.kinematics.leftSignedPulsePerUnit/rightSignedPulsePerUnit` using `-1.0/+1.0`.
- [x] **Step 4: Add HAL configuration.** Store:

```cpp
std::array<std::array<double, 6>, 2> axisSign{{
    {{-1.0, 1.0, -1.0, 1.0, -1.0, -1.0}},
    {{-1.0, -1.0, -1.0, 1.0, 1.0, 1.0}},
}};
```

- [x] **Step 5: Parse and validate.** Parse `leftAxisSign`/`rightAxisSign` with `jsonNumberArray6`; reject every value other than exactly `-1.0` or `1.0`.
- [x] **Step 6: Run focused tests.** Re-run the commands from Task 1. Expected: configuration tests pass; runtime transform tests may remain red until Task 3.

### Task 3: Apply the transform exactly once and expose diagnostics

**Files:**
- Modify: `hal/src/ForceControlRuntime.cpp`
- Modify: `frontend/src/types.ts`

- [x] **Step 1: Align accepted samples.** In `acceptSample`, multiply tared and filtered values by `config_.axisSign[side]`, save the aligned arrays, and pass aligned tared values to `ForceSafetyLatch`.
- [x] **Step 2: Publish native diagnostics.** Add `sensorRawLeft`/`sensorRawRight` from the driver snapshot. Add per-side `axisSign`, aligned `tareBias`, and native `sensorTareBias`.
- [x] **Step 3: Extend optional frontend types.** Add optional `axisSign`, `sensorTareBias`, `sensorRawLeft`, and `sensorRawRight` fields without changing display-side routing.
- [x] **Step 4: Run HAL tests.** Run `cmd /c hal\build_hal.cmd`. Expected: all HAL force tests pass.

### Task 4: Record the coordinate contract in dataset metadata

**Files:**
- Modify: `backend/tests/test_dataset_recorder.py`
- Modify: `backend/services/dataset_recorder.py`

- [x] **Step 1: Add a failing metadata assertion.** Populate per-side `axisSign`, `tareBias`, and `sensorTareBias`; assert metadata stores `axisSign`, aligned `tareBias`, and `sensorTareBias` for both sides.
- [x] **Step 2: Run RED check.** Run:

```text
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_dataset_recorder.py -k hkvl_configuration_and_tare -q
```

Expected: fail because `axisSign` and `sensorTareBias` are absent.
- [x] **Step 3: Implement metadata fields.** Copy the runtime per-side arrays into separate `axisSign`, `tareBias`, and `sensorTareBias` objects.
- [x] **Step 4: Run GREEN check.** Re-run the focused test. Expected: pass.

### Task 5: Audit consumers and verify

**Files:**
- Inspect: `hal/src/HkvlForceDriver.cpp`
- Inspect: `hal/src/ForceSafetyLatch.cpp`
- Inspect: `hal/src/ForceComplianceController.cpp`
- Inspect: `backend/services/telemetry_hub.py`
- Inspect: `backend/services/dataset_recorder.py`
- Inspect: `frontend/src/views/SettingsView.tsx`

- [x] **Step 1: Audit for duplicate signs.** Confirm no consumer multiplies standard force arrays by motion direction again and identity compliance matrices remain unchanged.
- [x] **Step 2: Run backend verification.**

```text
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_force_config.py backend/tests/test_dataset_recorder.py backend/tests/test_stack_scripts.py -q
backend\.venv\Scripts\python.exe -m pytest backend/tests
```

- [x] **Step 3: Run frontend verification.**

```text
npm test
npm run typecheck
npm run build
```

Run these commands from `frontend`.

- [x] **Step 4: Run HAL verification.**

```text
cmd /c hal\build_hal.cmd
```

- [x] **Step 5: Safely restart and inspect.** Restart HAL without Tare, safety acknowledgement, servo enable, or compliance enable. Verify COM15/Card 1 and COM14/Card 0 remain healthy, standard values have the configured signs, `sensorRaw*` retains native signs, and safety remains latched as required.

## Verification note

The force-specific backend tests, HAL build/tests, frontend tests, typecheck, and production build pass. The complete backend suite reports 565 passed and one pre-existing unrelated failure in `test_run_act_jepa_deploy_freezes_uncontrolled_state_by_default`; this plan does not modify the ACT-JEPA deployment script.
