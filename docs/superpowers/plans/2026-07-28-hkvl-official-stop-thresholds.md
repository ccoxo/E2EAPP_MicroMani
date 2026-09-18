# HKVL-36A Official Stop Thresholds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace provisional force-stop defaults with the HKVL-36A published full-scale ratings while retaining provisional warning values for later commissioning.

**Architecture:** Keep the existing safety configuration schema and HAL trip algorithm. Synchronize the same stop values across backend defaults, frontend defaults and fallbacks, active runtime JSON, tests, and reference documentation; apply them to the running HAL only through its existing stopped-and-disabled safety guard.

**Tech Stack:** Python/FastAPI configuration, React/TypeScript defaults, C++ HAL JSON configuration, pytest, Vitest

---

### Task 1: Specify official stop defaults in tests

**Files:**
- Modify: `backend/tests/test_hardware_defaults.py`
- Modify: `backend/tests/test_force_config.py`
- Modify: `frontend/src/data.test.ts`

- [ ] **Step 1: Change backend expectations**

Assert:

```python
assert config["safety"]["fxyStopN"] == 30
assert config["safety"]["fzStopN"] == 30
assert config["safety"]["momentStopNm"] == 1
```

and assert the flattened HAL payload carries the same values.

- [ ] **Step 2: Add the frontend expectation**

Add:

```ts
expect(defaultConfig.safety.fxyStopN).toBe(30)
expect(defaultConfig.safety.fzStopN).toBe(30)
expect(defaultConfig.safety.momentStopNm).toBe(1)
```

- [ ] **Step 3: Verify the tests fail for the old defaults**

Run:

```text
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_hardware_defaults.py backend/tests/test_force_config.py
cmd /c npm test -- src/data.test.ts
```

Expected: failures report the old `4 / 5 / 0.04` values.

### Task 2: Synchronize configuration and UI fallbacks

**Files:**
- Modify: `backend/core/defaults.py`
- Modify: `backend/runtime/config.json`
- Modify: `frontend/src/data.ts`
- Modify: `frontend/src/views/SettingsView.tsx`

- [ ] **Step 1: Update stop values**

Set:

```text
fxyStopN = 30
fzStopN = 30
momentStopNm = 1
```

Retain `fxyWarnN=2`, `fzWarnN=3`, `momentWarnNm=0.02`, and `watchdogMs=50`.

- [ ] **Step 2: Update input null fallbacks**

Use `30`, `30`, and `1` as the three stop-field fallbacks in `SafetyCard`.

- [ ] **Step 3: Verify focused tests pass**

Run the same commands from Task 1.

Expected: all focused tests pass.

### Task 3: Update reference documentation

**Files:**
- Modify: `CONTROL_TELEOP_REFERENCE_VERIFIED.md`
- Modify: `AppStation_后端开发指南_for_AI_Agent_v1.1.md`

- [ ] **Step 1: Record the distinction**

Document stop values as temporary HKVL-36A full-scale ceilings and warning values as provisional engineering settings awaiting commissioning. Do not describe the stop values as manufacturer-recommended robot safety thresholds.

### Task 4: Verify and safely apply

- [ ] **Step 1: Run backend tests**

Run:

```text
backend\.venv\Scripts\python.exe -m pytest backend/tests
```

Expected: no new failure.

- [ ] **Step 2: Run frontend verification**

Run:

```text
cmd /c npm test
cmd /c npm run typecheck
cmd /c npm run build
```

Expected: all commands pass.

- [ ] **Step 3: Apply through the HAL guard**

Read live motion state first. Only call `/api/settings/apply` when native teleoperation is stopped and every servo is disabled. If the HAL rejects the request, leave the running thresholds unchanged and report the blocking safety condition.
