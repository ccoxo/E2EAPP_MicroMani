# HKVL-36A Hardware-Side Port Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind `force.left` to COM15/Card 1 and `force.right` to COM14/Card 0 without adding force-axis or sign transforms.

**Architecture:** Preserve the existing hardware-side force contract across HAL, DDS, backend, datasets, and compliance. Correct only the serial-port values and every fixture/default that defines them; keep operator-facing conversion in the frontend.

**Tech Stack:** C++17 HAL, PowerShell launch/diagnostic scripts, Python/FastAPI backend, React/TypeScript frontend, pytest, Vitest

---

### Task 1: Add failing port-binding contract tests

**Files:**
- Modify: `backend/tests/test_hardware_defaults.py`
- Modify: `backend/tests/test_force_config.py`
- Modify: `backend/tests/test_dataset_recorder.py`
- Modify: `backend/tests/test_stack_scripts.py`
- Modify: `frontend/src/data.test.ts`
- Modify: `frontend/src/App.test.tsx`
- Modify: `hal/tests/ForceCoreTests.cpp`

- [ ] Change expected hardware-left port to COM15 and hardware-right port to COM14.
- [ ] Assert both compliance matrices remain `[1, 0, 0, 1]`, disabled, and unconfirmed.
- [ ] Run the focused backend, frontend, and HAL tests and confirm failures report the old COM14/COM15 binding.

### Task 2: Correct all binding sources

**Files:**
- Modify: `backend/core/defaults.py`
- Modify: `backend/runtime/config.json`
- Modify: `frontend/src/data.ts`
- Modify: `hal/include/HkvlForceDriver.h`
- Modify: `scripts/start-hal.ps1`
- Modify: `scripts/capture-hkvl-force.ps1`

- [ ] Set every hardware-side default to `leftPort=COM15`, `rightPort=COM14`.
- [ ] Keep sensor channel order, compliance matrices, mapping-confirmed flags, and gains unchanged.
- [ ] Run focused tests and confirm they pass.

### Task 3: Audit consumers and document stale references

**Files:**
- Inspect: `hal/src/HkvlForceDriver.cpp`
- Inspect: `hal/src/ForceControlRuntime.cpp`
- Inspect: `hal/src/TeleopHardwareTargetExecutor.cpp`
- Inspect: `backend/services/telemetry_hub.py`
- Inspect: `backend/services/command_service.py`
- Inspect: `backend/services/dataset_recorder.py`
- Inspect: `frontend/src/views/SettingsView.tsx`
- Inspect: `C:/Users/Administrator/Desktop/dual_serial_left_right_arm_capture.md`

- [ ] Confirm side indices are passed through unchanged below the configuration layer.
- [ ] Confirm Tare and chart selection use hardware side exactly once.
- [ ] Confirm compliance uses sensor Fx/Fz directly and motion signs only at the motion layer.
- [ ] Report the desktop capture note as stale; do not edit it without separate authorization.

### Task 4: Verify and safely apply

**Files:**
- Verify the files above without unrelated edits.

- [ ] Run `cmd /c hal\build_hal.cmd`.
- [ ] Run focused backend tests, then `backend\.venv\Scripts\python.exe -m pytest backend/tests`.
- [ ] Run `npm test`, `npm run typecheck`, and `npm run build` from `frontend`.
- [ ] POST `/api/settings/apply`; allow the HAL guard to reject unsafe runtime state.
- [ ] Verify force state reports hardware-left COM15 and hardware-right COM14, both streams remain healthy, compliance remains disabled, and safety remains latched until unloaded Tare and manual acknowledgement.
