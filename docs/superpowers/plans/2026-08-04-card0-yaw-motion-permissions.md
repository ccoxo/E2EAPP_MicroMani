# Card 0 Yaw Motion Permissions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore Card 0 Yaw across configuration, enable/home/manual/teleop control, HAL/driver execution, and frontend controls while preserving the existing Yaw soft limit and safety chain.

**Architecture:** Remove the special-case “Card 0 Yaw is permanently disabled” rule at every layer. Persisted legacy masks are normalized back to enabled, while ordinary enabled-axis masks, servo checks, work-origin validation, soft limits, jog caps, and emergency-stop behavior remain the standard gates.

**Tech Stack:** Python 3/FastAPI/Pydantic/pytest, TypeScript/React/Vitest, C++17/CMake/LTDMC HAL.

---

## Execution constraints

The active worktree contains pre-existing user changes, including changes in several files touched by this plan. Work directly in the active worktree so the implementation is tested against that integration state. Do not stage or commit implementation files automatically; staging whole dirty files could include unrelated user work. Use focused diffs after every task and leave the final implementation unstaged for user review.

## File map

- `backend/core/defaults.py`: authoritative backend teleop defaults.
- `backend/core/config.py`: persisted-config normalization and work-origin validity checks.
- `scripts/import_icf_teleop_config.py`: imports external ICF configuration through the same normalization rule.
- `backend/services/teleop_mapping.py`: produces HAL-native enabled-axis masks.
- `backend/services/command_service.py`: backend enable, home, return-origin, and manual-jog policy.
- `hal/src/HalCommandDispatcher.cpp`: HAL command parsing and dispatch.
- `hal/src/LTDMCDriver.cpp`: physical axis enable, home, return-origin, manual jog, teleop update, stop, and enabled-state behavior.
- `frontend/src/data.ts`: frontend fallback defaults.
- `frontend/src/manualAxisRules.ts`: obsolete Card 0 Yaw-only UI rule; delete after its callers are removed.
- `frontend/src/views/SettingsView.tsx`: motion-card labels and manual controls.
- `backend/tests/test_app.py`, `backend/tests/test_hardware_defaults.py`, `backend/tests/test_import_icf_teleop_config.py`, `backend/tests/test_teleop_mapping.py`, `backend/tests/test_hal_source_contracts.py`, `frontend/src/App.test.tsx`: regression coverage.

### Task 1: Restore configuration and native teleop masks

**Files:**
- Modify: `backend/tests/test_hardware_defaults.py`
- Modify: `backend/tests/test_app.py`
- Modify: `backend/tests/test_import_icf_teleop_config.py`
- Modify: `backend/tests/test_teleop_mapping.py`
- Modify: `backend/core/defaults.py`
- Modify: `backend/core/config.py`
- Modify: `scripts/import_icf_teleop_config.py`
- Modify: `backend/services/teleop_mapping.py`

- [ ] **Step 1: Write failing configuration and payload expectations**

Change the default, settings normalization, importer, migration, and native-payload assertions to require Yaw enabled on both sides. The focused settings regression must use an old disabled value on the side currently assigned to Card 0:

```python
def test_settings_restores_yaw_permission_on_card0_side(tmp_path: Path) -> None:
    logs = LogService(log_file_path=tmp_path / "logs" / "test.log", emit_startup=False)
    settings = SettingsService(tmp_path, logs)
    config = default_config()
    config["motion"]["leftCardNo"] = 0
    config["motion"]["rightCardNo"] = 1
    config["teleop"]["leftEnabledAxes"] = [True, True, True, True, True, False]
    config["teleop"]["rightEnabledAxes"] = [True, True, True, True, True, True]
    settings.save_config(config, emit_log=False)

    normalized = settings.get_config()

    assert normalized["teleop"]["leftEnabledAxes"] == [True] * 6
    assert normalized["teleop"]["rightEnabledAxes"] == [True] * 6
```

Rename the native payload test and assert no card-specific mask is injected:

```python
def test_hal_native_payload_allows_yaw_on_card0_side() -> None:
    config = start_config()
    config["motion"]["leftCardNo"] = 0
    config["motion"]["rightCardNo"] = 1
    config["teleop"]["leftEnabledAxes"] = [True] * 6
    config["teleop"]["rightEnabledAxes"] = [True] * 6
    mapper = TeleopMappingService(settings=FakeSettings(config), hal=FakeHal(), logs=LogService())

    payload = mapper._native_payload(config)

    assert payload["leftEnabledAxes"] == [True] * 6
    assert payload["rightEnabledAxes"] == [True] * 6
```

Update every directly related expectation from a final `False` to `[True] * 6`, including the default-config, ICF importer, home pre-start payload, and migration assertions identified by:

```powershell
rg -n "rightEnabledAxes.*False|rightEnabledAxes.*false|EnabledAxes.*False" backend/tests backend/core/defaults.py frontend/src
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_hardware_defaults.py backend/tests/test_app.py::test_settings_restores_yaw_permission_on_card0_side backend/tests/test_import_icf_teleop_config.py::test_import_icf_config_restores_card0_yaw_permission backend/tests/test_teleop_mapping.py::test_hal_native_payload_allows_yaw_on_card0_side -q
```

Expected: FAIL because backend defaults and the current normalizer/native payload still force Card 0 Yaw to `False`.

- [ ] **Step 3: Implement the minimum configuration change**

Set both backend default Yaw masks to enabled:

```python
"leftEnabledAxes": [True, True, True, True, True, True],
"rightEnabledAxes": [True, True, True, True, True, True],
```

Replace `_normalize_card0_yaw_disabled` with a focused legacy-value restoration that preserves every non-Yaw axis:

```python
def _normalize_yaw_enabled(config: dict[str, Any]) -> None:
    teleop = config.get("teleop", {}) if isinstance(config.get("teleop"), dict) else {}
    if not isinstance(teleop, dict):
        return
    for side in ("left", "right"):
        key = f"{side}EnabledAxes"
        raw = teleop.get(key)
        axes = [bool(value) for value in raw[:6]] if isinstance(raw, list) and len(raw) >= 6 else [True] * 6
        axes[5] = True
        teleop[key] = axes
```

Update both call sites in `backend/core/config.py` and the import/call in `scripts/import_icf_teleop_config.py` to `_normalize_yaw_enabled`. Remove `_motion_card_no` from `backend/core/config.py` after confirming it has no remaining callers.

In `backend/services/teleop_mapping.py`, preserve the configured six-axis mask without a Card 0 override:

```python
def _enabled_axes(self, side: SideName, config: dict[str, Any]) -> list[bool]:
    key = f"{side}EnabledAxes"
    raw = config.get("teleop", {}).get(key, DEFAULT_ENABLED_AXES)
    if not isinstance(raw, list) or len(raw) != 6:
        raw = DEFAULT_ENABLED_AXES
    return [bool(value) for value in raw]
```

Remove `TeleopMappingService._motion_card_no` after confirming it has no remaining caller.

- [ ] **Step 4: Run focused configuration tests and verify GREEN**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_hardware_defaults.py backend/tests/test_import_icf_teleop_config.py backend/tests/test_teleop_mapping.py::test_hal_native_payload_allows_yaw_on_card0_side backend/tests/test_app.py::test_settings_restores_yaw_permission_on_card0_side -q
```

Expected: PASS.

- [ ] **Step 5: Review the task diff without staging**

Run:

```powershell
git diff -- backend/core/defaults.py backend/core/config.py scripts/import_icf_teleop_config.py backend/services/teleop_mapping.py backend/tests/test_hardware_defaults.py backend/tests/test_app.py backend/tests/test_import_icf_teleop_config.py backend/tests/test_teleop_mapping.py
```

Expected: only Card 0 Yaw permission expectations and implementation change in the relevant hunks; unrelated pre-existing hunks remain untouched.

### Task 2: Restore backend manual, enable, home, and return-origin paths

**Files:**
- Modify: `backend/tests/test_app.py`
- Modify: `backend/tests/test_teleop_mapping.py`
- Modify: `backend/services/command_service.py`
- Modify: `backend/core/config.py`

- [ ] **Step 1: Write failing backend control tests**

Add a simulated endpoint regression proving the default Card 0/right hardware side can jog Yaw:

```python
def test_manual_axis_move_allows_card0_yaw(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))

    response = client.post(
        "/api/motion/manual_axis_move",
        json={"side": "right", "axis": "Yaw", "direction": 1, "step": 0.1, "speedMode": "fine"},
    )

    assert response.status_code == 200
    assert response.json()["data"]["applied"] != 0
```

Change existing `home_all` and teleop pre-start expectations so `rightEnabledAxes` is `[True] * 6`. Rename `test_return_origin_side_ignores_disabled_right_yaw_soft_limit` to `test_return_origin_side_validates_card0_yaw_soft_limit` and require the invalid legacy target to be rejected without a HAL command:

```python
response = client.post("/api/motion/right/return_origin")

assert response.status_code == 503
assert fake_hal.commands == []
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_app.py::test_manual_axis_move_allows_card0_yaw backend/tests/test_app.py::test_return_origin_side_validates_card0_yaw_soft_limit backend/tests/test_app.py::test_home_all_requires_and_sends_captured_work_origin backend/tests/test_app.py::test_home_all_sends_work_origin_without_auto_enabling_motion_sides backend/tests/test_teleop_mapping.py::test_teleop_start_returns_to_work_origin_before_real_mapper backend/tests/test_teleop_mapping.py::test_teleop_start_returns_requested_side_to_origin_before_real_mapper -q
```

Expected: Card 0 Yaw manual jog is rejected by `_validate_manual_axis_policy`, and existing home payloads omit the Yaw axis.

- [ ] **Step 3: Remove the backend permanent-policy gates**

In `backend/services/command_service.py`:

- Delete `CARD0_YAW_DISABLED_AXES`.
- Delete the `_validate_manual_axis_policy` call and method.
- Delete `_motion_card_no` after confirming it has no remaining callers.
- Return the ordinary six-axis mask from `_home_enabled_axes`:

```python
def _home_enabled_axes(self, side: str, config: dict[str, Any] | None = None) -> list[bool]:
    return [True] * 6
```

In `backend/core/config.py`, remove `_origin_validation_axes` and validate all three rotational axes directly:

```python
for axis_index in range(3, 6):
    limit = limits[axis_index]
    target = origin[axis_index]
    if limit.min > limit.max or target < limit.min or target > limit.max:
        return True
```

Do not change soft-limit calculation or invalid-origin handling.

- [ ] **Step 4: Run focused backend tests and verify GREEN**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_app.py::test_manual_axis_move_allows_card0_yaw backend/tests/test_app.py::test_return_origin_side_validates_card0_yaw_soft_limit backend/tests/test_app.py::test_home_all_requires_and_sends_captured_work_origin backend/tests/test_app.py::test_home_all_sends_work_origin_without_auto_enabling_motion_sides backend/tests/test_teleop_mapping.py::test_teleop_start_returns_to_work_origin_before_real_mapper backend/tests/test_teleop_mapping.py::test_teleop_start_returns_requested_side_to_origin_before_real_mapper -q
```

Expected: PASS.

- [ ] **Step 5: Review the task diff without staging**

Run:

```powershell
git diff -- backend/core/config.py backend/services/command_service.py backend/tests/test_app.py backend/tests/test_teleop_mapping.py
```

Expected: the permanent Card 0 policy is gone; step caps, servo checks, origin checks, and soft limits remain.

### Task 3: Restore HAL dispatch and every LTDMC driver motion path

**Files:**
- Modify: `backend/tests/test_hal_source_contracts.py`
- Modify: `hal/src/HalCommandDispatcher.cpp`
- Modify: `hal/src/LTDMCDriver.cpp`

- [ ] **Step 1: Invert the HAL source-contract tests**

Replace the two prohibition tests with positive absence contracts:

```python
def test_hal_manual_axis_move_dispatches_card0_yaw_without_special_rejection() -> None:
    source = (REPO_ROOT / "hal" / "src" / "HalCommandDispatcher.cpp").read_text(encoding="utf-8")
    branch = source.split('if (name == "motion.manual_axis_move") {', 1)[1].split(
        'if (name == "motion.teleop_target_update") {', 1
    )[0]
    assert "Card 0 Yaw motion axis is disabled by safety policy" not in branch
    assert "motion_.moveRelativeUi(" in branch


def test_ltdmc_driver_has_no_permanent_card0_yaw_gate() -> None:
    source = (REPO_ROOT / "hal" / "src" / "LTDMCDriver.cpp").read_text(encoding="utf-8")
    assert "axisMotionPermanentlyDisabled" not in source
    assert "Card 0 Yaw motion axis is disabled by safety policy" not in source
```

- [ ] **Step 2: Run the source-contract tests and verify RED**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_hal_source_contracts.py::test_hal_manual_axis_move_dispatches_card0_yaw_without_special_rejection backend/tests/test_hal_source_contracts.py::test_ltdmc_driver_has_no_permanent_card0_yaw_gate -q
```

Expected: FAIL because both special-case guards still exist.

- [ ] **Step 3: Remove only the permanent disable condition**

In `hal/src/HalCommandDispatcher.cpp`, remove the local `cardForSide` helper and the Card 0 Yaw rejection block. Keep parsing and dispatch unchanged:

```cpp
if (name == "motion.manual_axis_move") {
  const auto side = parseSide(jsonStringValue(bodyText, "side"));
  const auto axis = parseAxis(jsonStringValue(bodyText, "axis"));
  const auto direction = jsonNumberValue(bodyText, "direction", 0);
```

In `hal/src/LTDMCDriver.cpp`, delete `axisMotionPermanentlyDisabled` and remove only its operands/branches from:

- `enableSide`
- `homeSide`
- `homeAll`
- `homeOriginSide`
- `moveRelativeUi`
- `updateTeleopTargetUi`
- `stopTeleopSide`
- `axisMotionEnabled`

The resulting enabled checks must be ordinary mask/cache checks, for example:

```cpp
const auto axisEnabled = enabled && enabledAxes[axisIndex];
```

```cpp
if (!enabledAxes[axisIndex]) {
  continue;
}
```

```cpp
bool LTDMCDriver::axisMotionEnabled(Side side, SemanticAxis axis) const {
  return enabled_[stateIndex(side, axis)];
}
```

Do not change axis mapping, profile values, `throwIfEstopActive`, rotation step caps, or soft-limit inputs.

- [ ] **Step 4: Run source contracts and compile HAL**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_hal_source_contracts.py -q
cmd /c hal\build_hal.cmd
```

Expected: source-contract suite PASS and HAL build exits 0.

- [ ] **Step 5: Review the HAL diff without staging**

Run:

```powershell
git diff -- hal/src/HalCommandDispatcher.cpp hal/src/LTDMCDriver.cpp backend/tests/test_hal_source_contracts.py
```

Expected: only Card 0 Yaw prohibition code/tests are removed or inverted.

### Task 4: Restore frontend defaults, labels, selectors, and jog buttons

**Files:**
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/data.ts`
- Delete: `frontend/src/manualAxisRules.ts`
- Modify: `frontend/src/views/SettingsView.tsx`

- [ ] **Step 1: Write failing frontend expectations**

Update the default-config expectation:

```typescript
expect(defaultConfig.teleop.rightEnabledAxes).toEqual([true, true, true, true, true, true])
```

Delete the obsolete `isManualAxisDisabled` unit test/import and extend the rendered motion-card assertion so both cards show an enabled Yaw window:

```typescript
expect(screen.getAllByText(/Yaw ±7°/).length).toBeGreaterThanOrEqual(2)
expect(screen.queryByText(/Yaw disabled/)).not.toBeInTheDocument()
expect(screen.queryByText('Card 0 Yaw disabled')).not.toBeInTheDocument()
```

- [ ] **Step 2: Run the frontend test and verify RED**

Run from `frontend`:

```powershell
npm.cmd test -- --run src/App.test.tsx
```

Expected: FAIL because the fallback default, Card 0 label, and manual selector still disable Yaw.

- [ ] **Step 3: Remove the UI-only prohibition**

In `frontend/src/data.ts`, set `rightEnabledAxes` to six `true` values.

In `frontend/src/views/SettingsView.tsx`:

- Remove the `manualAxisRules` import.
- Use enabled Yaw text for both card rotation labels while preserving each card’s Roll window.
- Remove `motionCardNo`, `card0YawDisabled`, and the Card 0-specific blocked text from `ManualArmCard`.
- Set `manualAxisBlocked` solely from `displayLimits.blocked`.
- Render every axis selector without a Card 0 disabled prop.

The relevant shapes are:

```typescript
const rotationWindowLabel = configCardNo === 0
  ? `Roll -95~5\u00b0 / Pitch \u00b130\u00b0 \u00b7 Yaw \u00b17\u00b0`
  : `Roll -5~95\u00b0 / Pitch \u00b130\u00b0 \u00b7 Yaw \u00b17\u00b0`
```

```typescript
const manualAxisBlocked = displayLimits.blocked
const manualAxisBlockedText = 'work_origin_missing'
```

```tsx
{manualAxisOrder.map((axis) => {
  const active = manualControl.selectedSide === hardwareSide && manualControl.selectedAxis === axis
  return (
    <Button key={axis} type={active ? 'primary' : 'default'} onClick={() => selectManualAxis(hardwareSide, axis)}>
      {axis}
    </Button>
  )
})}
```

Delete `frontend/src/manualAxisRules.ts` after `rg` confirms no remaining import or caller.

- [ ] **Step 4: Run frontend test and typecheck**

Run from `frontend`:

```powershell
npm.cmd test -- --run src/App.test.tsx
npm.cmd run typecheck
```

Expected: PASS.

- [ ] **Step 5: Review the frontend diff without staging**

Run:

```powershell
git diff -- frontend/src/data.ts frontend/src/manualAxisRules.ts frontend/src/views/SettingsView.tsx frontend/src/App.test.tsx
```

Expected: only Yaw permission defaults, labels, and disabling behavior change.

### Task 5: Full regression and completion audit

**Files:**
- Verify only; no planned production edits.

- [ ] **Step 1: Scan for stale permanent-disable code and text**

Run:

```powershell
rg -n "CARD0_YAW_DISABLED_AXES|_normalize_card0_yaw_disabled|axisMotionPermanentlyDisabled|Card 0 Yaw motion axis is disabled|Card 0 Yaw disabled|Yaw disabled" backend frontend hal scripts
```

Expected: no matches.

- [ ] **Step 2: Run backend regression suites**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_app.py backend/tests/test_hardware_defaults.py backend/tests/test_import_icf_teleop_config.py backend/tests/test_teleop_mapping.py backend/tests/test_hal_source_contracts.py -q
```

Expected: PASS.

- [ ] **Step 3: Run frontend regression and typecheck**

Run from `frontend`:

```powershell
npm.cmd test -- --run src/App.test.tsx
npm.cmd run typecheck
```

Expected: PASS.

- [ ] **Step 4: Rebuild HAL**

Run:

```powershell
cmd /c hal\build_hal.cmd
```

Expected: exit code 0 and a successfully produced HAL binary.

- [ ] **Step 5: Audit the final diff and report hardware verification boundary**

Run:

```powershell
git diff --check
git status --short
git diff -- backend/core/defaults.py backend/core/config.py backend/services/command_service.py backend/services/teleop_mapping.py scripts/import_icf_teleop_config.py hal/src/HalCommandDispatcher.cpp hal/src/LTDMCDriver.cpp frontend/src/data.ts frontend/src/manualAxisRules.ts frontend/src/views/SettingsView.tsx backend/tests/test_app.py backend/tests/test_hardware_defaults.py backend/tests/test_import_icf_teleop_config.py backend/tests/test_teleop_mapping.py backend/tests/test_hal_source_contracts.py frontend/src/App.test.tsx
```

Expected: no whitespace errors; every new changed line traces to Card 0 Yaw permission restoration. Report automated results explicitly and note that supervised physical motion verification is still required before unrestricted operation on real hardware.
