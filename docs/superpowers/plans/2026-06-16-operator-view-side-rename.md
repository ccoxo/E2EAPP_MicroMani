# Operator View Side Rename Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename platform-facing left/right semantics to the operator viewpoint while preserving the existing hardware wiring and HAL motor/gripper channels.

**Architecture:** Add one small backend side-mapping unit that translates operator side to hardware side. Keep HAL hardware side names unchanged. Route backend motion/gripper commands and frontend labels through the operator-view mapping so the user sees `left Omega.7 -> left arm` and `right Omega.7 -> right arm`, while the hardware still receives current crossed channels.

**Tech Stack:** Python FastAPI backend, pytest, TypeScript React frontend, Vitest, C++ HAL status payloads.

---

### Task 1: Backend Operator Side Mapping Unit

**Files:**
- Create: `backend/core/operator_view.py`
- Test: `backend/tests/test_operator_view.py`

- [ ] **Step 1: Write the failing test**

```python
from backend.core.operator_view import (
    hardware_side_for_operator_side,
    operator_gripper_source_for_side,
    operator_side_for_hardware_side,
)


def test_operator_view_maps_left_to_existing_right_hardware() -> None:
    assert hardware_side_for_operator_side("left") == "right"
    assert hardware_side_for_operator_side("right") == "left"
    assert operator_side_for_hardware_side("right") == "left"
    assert operator_side_for_hardware_side("left") == "right"


def test_operator_view_gripper_sources_follow_same_named_operator_hand() -> None:
    assert operator_gripper_source_for_side("left") == "PhysicalLeft"
    assert operator_gripper_source_for_side("right") == "PhysicalRight"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_operator_view.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.core.operator_view'`.

- [ ] **Step 3: Write minimal implementation**

```python
from __future__ import annotations

from typing import Literal

SideName = Literal["left", "right"]


def hardware_side_for_operator_side(side: SideName) -> SideName:
    return "right" if side == "left" else "left"


def operator_side_for_hardware_side(side: SideName) -> SideName:
    return "right" if side == "left" else "left"


def operator_gripper_source_for_side(side: SideName) -> str:
    return "PhysicalLeft" if side == "left" else "PhysicalRight"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_operator_view.py -q`
Expected: PASS.

### Task 2: Backend Config Defaults And Migration

**Files:**
- Modify: `backend/core/defaults.py`
- Modify: `backend/core/config.py`
- Test: `backend/tests/test_hardware_defaults.py`
- Test: `backend/tests/test_teleop_mapping.py`

- [ ] **Step 1: Write failing expectations**

Change default and migration assertions so operator-view gripper teleop expects:

```python
assert teleop["gripperTeleop"]["leftSourceHand"] == "PhysicalLeft"
assert teleop["gripperTeleop"]["rightSourceHand"] == "PhysicalRight"
```

Add a migration assertion:

```python
def test_settings_migration_updates_crossed_gripper_sources_to_operator_view(tmp_path: Any) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    config = default_config()
    config["teleop"]["gripperTeleop"]["leftSourceHand"] = "PhysicalRight"
    config["teleop"]["gripperTeleop"]["rightSourceHand"] = "PhysicalLeft"
    (runtime_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")

    migrated = SettingsService(runtime_dir, LogService()).get_config()

    assert migrated["teleop"]["gripperTeleop"]["leftSourceHand"] == "PhysicalLeft"
    assert migrated["teleop"]["gripperTeleop"]["rightSourceHand"] == "PhysicalRight"
```

- [ ] **Step 2: Run tests to verify failures**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_hardware_defaults.py::test_omega7_teleop_defaults_match_icf_strategy backend\tests\test_hardware_defaults.py::test_gripper_teleop_defaults_match_omega7_gap_range backend\tests\test_teleop_mapping.py::test_settings_migration_updates_crossed_gripper_sources_to_operator_view -q`
Expected: FAIL because defaults and migration still use `PhysicalRight/PhysicalLeft`.

- [ ] **Step 3: Implement minimal defaults and migration**

In `backend/core/defaults.py`, change nested `gripperTeleop` defaults to:

```python
"leftSourceHand": "PhysicalLeft",
"rightSourceHand": "PhysicalRight",
```

In `backend/core/config.py`, migrate old crossed gripper source values to operator-view values and set defaults to operator-view values.

- [ ] **Step 4: Run tests to verify pass**

Run the same pytest command from Step 2.
Expected: PASS.

### Task 3: Backend Motion And Gripper Command Routing

**Files:**
- Modify: `backend/app.py`
- Modify: `backend/services/gripper_tele_service.py`
- Test: `backend/tests/test_app.py`
- Test: `backend/tests/test_gripper_tele_service.py`

- [ ] **Step 1: Write failing route tests**

Add or update tests so `/api/teleop/left/connect` reports `mappedSide="left"` to the UI while the HAL/native hardware payload still uses the existing crossed hardware side. Add a gripper teleop test that operator-left gripper follows `PhysicalLeft` but commands hardware/right gripper config (`COM9/slave 9`) through the worker/router boundary.

- [ ] **Step 2: Run tests to verify failures**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_app.py::test_teleop_disconnect_schedules_native_refresh_and_reports_mapped_stop backend\tests\test_gripper_tele_service.py -q`
Expected: FAIL on the old UI-facing mapped side and old source hand expectations.

- [ ] **Step 3: Implement minimal routing**

Use `hardware_side_for_operator_side()` only at hardware command boundaries. Keep UI/API `side`, `mappedSide`, and telemetry labels in operator side. In gripper teleop, select Omega.7 source by operator side, then translate to hardware side before issuing gripper command.

- [ ] **Step 4: Run tests to verify pass**

Run the same pytest command from Step 2.
Expected: PASS.

### Task 4: Frontend Operator-View Labels And Defaults

**Files:**
- Modify: `frontend/src/data.ts`
- Modify: `frontend/src/views/SettingsView.tsx`
- Test: `frontend/src/App.test.tsx`

- [ ] **Step 1: Write failing frontend assertions**

Update defaults tests to expect operator-view gripper sources:

```typescript
expect(defaultConfig.teleop.gripperTeleop.leftSourceHand).toBe('PhysicalLeft')
expect(defaultConfig.teleop.gripperTeleop.rightSourceHand).toBe('PhysicalRight')
```

Add a UI expectation that the left Omega.7 card displays target arm `左臂` under operator-view semantics.

- [ ] **Step 2: Run tests to verify failures**

Run: `cd frontend; npm.cmd test -- --run src/App.test.tsx`
Expected: FAIL because frontend still displays crossed target labels.

- [ ] **Step 3: Implement minimal frontend change**

Change frontend default `gripperTeleop` sources. Change target arm display to operator side labels while retaining a small diagnostic hint for hardware side if needed.

- [ ] **Step 4: Run tests to verify pass**

Run: `cd frontend; npm.cmd test -- --run src/App.test.tsx`
Expected: PASS.

### Task 5: Focused Regression Verification

**Files:**
- No production edits unless previous tasks reveal a narrow issue.

- [ ] **Step 1: Run backend focused suite**

Run: `backend\.venv\Scripts\python.exe -m pytest backend\tests\test_operator_view.py backend\tests\test_hardware_defaults.py backend\tests\test_teleop_mapping.py backend\tests\test_gripper_tele_service.py backend\tests\test_app.py::test_teleop_disconnect_schedules_native_refresh_and_reports_mapped_stop -q`
Expected: PASS.

- [ ] **Step 2: Run frontend focused suite**

Run: `cd frontend; npm.cmd test -- --run src/App.test.tsx`
Expected: PASS.

- [ ] **Step 3: Report operator-view hardware mapping**

Report:

```text
Operator left: Omega7 left -> operator left arm -> hardware right arm -> COM9/slave 9.
Operator right: Omega7 right -> operator right arm -> hardware left arm -> COM8/slave 10.
```
