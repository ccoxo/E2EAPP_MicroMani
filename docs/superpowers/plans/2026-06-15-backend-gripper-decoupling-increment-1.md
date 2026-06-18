# Backend Gripper Decoupling Increment 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace repeated gripper backend selection in `backend/app.py` and `backend/services/command_service.py` with one tested router, while preparing service wiring for a later route split.

**Architecture:** Introduce a small async gripper backend layer that owns backend selection and blocking-driver dispatch. Keep command target calculation, config persistence, and structured command logging in `CommandService`, because those are business rules rather than backend selection. Do not split all routes in this increment; add `AppServices` only after the gripper router is stable.

**Tech Stack:** FastAPI, Python 3.12, pytest, existing `GripperResult`, existing HAL client, existing worker and RS485 driver services.

---

## Feasibility Review Of The Existing Plan

The desktop coupling-fix plan is directionally executable, but it should be narrowed before implementation.

- Phase 1 is feasible and should be done first. Current tests already cover worker/direct/native command routing, native status, and event-loop blocking, so the change can be verified safely.
- The router must be async. Worker and direct RS485 operations are blocking and currently run through `asyncio.to_thread`; native HAL dispatch is already async.
- Worker backend must keep priority over HAL-native. Existing behavior and tests expect `gripper.sampleMode == "dual_worker"` to own grippers even when `teleop.engine == "hal_native"`.
- `CommandService.gripper_command()` should not be swallowed wholesale by a router. It computes safe targets, writes config state, handles test-mode fallback, and logs `event=gripper_command`; moving all of that into adapters would increase coupling.
- Full `app.py` route extraction is too large for the same increment. A safer Phase 2 starts with `AppServices` and one or two route groups, then repeats the pattern.

## File Structure For This Increment

- Create `backend/services/gripper_backend.py`: protocol, backend helpers, and three async adapters.
- Create `backend/services/gripper_router.py`: backend selection and facade methods.
- Create `backend/tests/test_gripper_router.py`: router selection, status formatting, native status, direct/worker dispatch tests.
- Modify `backend/services/command_service.py`: accept `gripper_router`, use it for real-hardware gripper dispatch, keep local business rules.
- Modify `backend/app.py`: instantiate the router and use it in health/status/position/diagnose/websocket/precheck gripper branches.
- Modify `backend/tests/test_gripper_worker_service.py`: adapt fakes only where `CommandService` construction now needs a router or where existing tests can assert router behavior.
- Modify `backend/tests/test_app.py`: keep existing behavior assertions, add `app.state.gripper_router` compatibility checks.
- Create `backend/app_factory.py` only after router tests pass: `AppServices` dataclass and service wiring helper.

## Task 1: Add Gripper Router Tests First

**Files:**
- Create: `backend/tests/test_gripper_router.py`

- [ ] **Step 1: Add fake dependencies**

Create `backend/tests/test_gripper_router.py` with fakes that mirror current tests:

```python
from __future__ import annotations

import asyncio
from typing import Any

from backend.core.defaults import default_config
from backend.drivers.gripper_rs485 import GripperResult
from backend.services.gripper_backend import DirectGripperAdapter, NativeGripperAdapter, WorkerGripperAdapter
from backend.services.gripper_router import GripperRouter


class FakeHal:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, Any]]] = []

    async def command(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        self.commands.append((name, body))
        return {"command": name, "payload": body, "message": "hal accepted"}


class FakeTeleopMapper:
    def __init__(self) -> None:
        self.native_status: dict[str, Any] = {}
        self.sources: list[str] = []

    def status(self, _config: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"running": bool(self.sources), "sources": list(self.sources), "nativeStatus": dict(self.native_status)}


class FakeWorkers:
    def __init__(self) -> None:
        self.command_calls: list[tuple[str, str, float | None]] = []

    def is_enabled(self, config: dict[str, Any] | None = None) -> bool:
        active = config or default_config()
        return active["gripper"].get("sampleMode") == "dual_worker"

    def status(self, _config: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "message": "dual gripper workers", "sides": {}}

    def position(self, _config: dict[str, Any], side: str) -> GripperResult:
        return GripperResult(True, f"{side} worker position", 3.0)

    def command(self, _config: dict[str, Any], side: str, command: str, target: float | None) -> GripperResult:
        self.command_calls.append((side, command, target))
        return GripperResult(True, "worker command", target)

    def stop_all(self) -> None:
        return None


class FakeDirectGripper:
    def __init__(self) -> None:
        self.command_calls: list[tuple[str, str, float | None]] = []

    def probe(self, _config: dict[str, Any]) -> GripperResult:
        return GripperResult(True, "jodell RS485 gripper ports open", details={"ports": [{"side": "left"}]})

    def position(self, _config: dict[str, Any], side: str) -> GripperResult:
        return GripperResult(True, f"{side} direct position", 4.0)

    def diagnose(self, _config: dict[str, Any], side: str) -> GripperResult:
        return GripperResult(True, f"{side} direct diagnose", 5.0)

    def command(self, _config: dict[str, Any], side: str, command: str, target: float | None) -> GripperResult:
        self.command_calls.append((side, command, target))
        return GripperResult(True, "direct command", target)


class FakeHardware:
    def __init__(self) -> None:
        self.gripper = FakeDirectGripper()


def build_router() -> tuple[GripperRouter, FakeHal, FakeTeleopMapper, FakeWorkers, FakeHardware]:
    hal = FakeHal()
    teleop = FakeTeleopMapper()
    workers = FakeWorkers()
    hardware = FakeHardware()
    router = GripperRouter(
        native=NativeGripperAdapter(hal, teleop),
        worker=WorkerGripperAdapter(workers),
        direct=DirectGripperAdapter(hardware),
    )
    return router, hal, teleop, workers, hardware
```

- [ ] **Step 2: Add failing backend selection tests**

Append:

```python
def test_router_selects_worker_before_native() -> None:
    router, _hal, _teleop, _workers, _hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "dual_worker"

    assert router.backend_name(config) == "dual_worker"
    assert router.is_native(config) is False


def test_router_selects_native_only_when_workers_disabled() -> None:
    router, _hal, _teleop, _workers, _hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "direct"

    assert router.backend_name(config) == "hal_native"
    assert router.is_native(config) is True


def test_router_falls_back_to_direct() -> None:
    router, _hal, _teleop, _workers, _hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "python_mapper"
    config["gripper"]["sampleMode"] = "direct"

    assert router.backend_name(config) == "python_rs485"
    assert router.is_native(config) is False
```

- [ ] **Step 3: Add failing status and dispatch tests**

Append:

```python
def test_router_status_formats_direct_probe() -> None:
    router, _hal, _teleop, _workers, _hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "python_mapper"
    config["gripper"]["sampleMode"] = "direct"

    status = asyncio.run(router.status(config))

    assert status["ok"] is True
    assert status["message"] == "jodell RS485 gripper ports open"
    assert status["ports"] == [{"side": "left"}]


def test_router_native_status_uses_cached_teleop_mapper_payload() -> None:
    router, _hal, teleop, _workers, _hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "direct"
    teleop.sources = ["manual-gripper"]
    teleop.native_status = {
        "running": True,
        "gripperTargets": [8.0, 9.0],
        "grippers": {
            "left": {"ok": True, "positionMm": 8.0, "targetMm": 8.0, "message": "", "lastCommandTs": 1},
            "right": {"ok": True, "positionMm": 9.0, "targetMm": 9.0, "message": "", "lastCommandTs": 2},
        },
    }

    status = asyncio.run(router.status(config))

    assert status["nativeManaged"] is True
    assert status["running"] is True
    assert status["positionMm"] == {"left": 8.0, "right": 9.0}
    assert status["ports"][1]["port"] == "COM9"


def test_router_native_command_sends_hal_payload() -> None:
    router, hal, _teleop, _workers, _hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "direct"

    result = asyncio.run(router.command(config, "left", "target", 7.5))

    assert result.ok is True
    assert result.position_mm == 7.5
    assert result.details["nativeManaged"] is True
    assert hal.commands[0][0] == "teleop.native.gripper_command"
    assert hal.commands[0][1]["targetMm"] == 7.5


def test_router_worker_command_runs_worker_backend() -> None:
    router, _hal, _teleop, workers, hardware = build_router()
    config = default_config()
    config["teleop"]["engine"] = "hal_native"
    config["gripper"]["sampleMode"] = "dual_worker"

    result = asyncio.run(router.command(config, "right", "target", 6.0))

    assert result.message == "worker command"
    assert workers.command_calls == [("right", "target", 6.0)]
    assert hardware.gripper.command_calls == []
```

- [ ] **Step 4: Run the new tests and verify they fail**

Run:

```powershell
python -m pytest backend/tests/test_gripper_router.py -q
```

Expected: import failure because `backend.services.gripper_backend` and `backend.services.gripper_router` do not exist.

## Task 2: Implement Gripper Backends And Router

**Files:**
- Create: `backend/services/gripper_backend.py`
- Create: `backend/services/gripper_router.py`

- [ ] **Step 1: Create `backend/services/gripper_backend.py`**

Implement these public symbols:

```python
from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from backend.drivers.gripper_rs485 import GripperResult


@runtime_checkable
class GripperBackend(Protocol):
    name: str

    def is_enabled(self, config: dict[str, Any]) -> bool:
        raise NotImplementedError

    async def status(self, config: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    async def position(self, config: dict[str, Any], side: str) -> GripperResult:
        raise NotImplementedError

    async def diagnose(self, config: dict[str, Any], side: str) -> GripperResult:
        raise NotImplementedError

    async def command(self, config: dict[str, Any], side: str, command: str, target_mm: float | None) -> GripperResult:
        raise NotImplementedError

    async def stop(self) -> None:
        raise NotImplementedError
```

Add helper functions in the same file:

- `native_teleop_enabled(config) -> bool`: current `app.py` / `CommandService` HAL-native engine check.
- `gripper_serial_ports(config) -> list[dict[str, Any]]`: current `app.py` serial-port formatting.
- `native_gripper_payload(config, side, target) -> dict[str, object]`: move the current payload shape from `CommandService._native_gripper_payload`.
- `hal_response_message(result) -> str`: move the current response-message extraction.
- `native_status_to_gripper_status(config, mapper_status) -> dict[str, Any]`: move the current `native_gripper_status()` body and read native status from `mapper_status`.
- `result_to_status(result) -> dict[str, Any]`: return `{"ok": result.ok, "message": result.message, "details": result.details, "ports": result.details.get("ports", [])}`.

Implement adapters:

- `WorkerGripperAdapter`: delegates `status`, `position`, `diagnose`, and `command` through `asyncio.to_thread`; `diagnose` should return the selected side from worker status as `GripperResult`.
- `NativeGripperAdapter`: uses `teleop_mapper.status(config)` for status and position; `command` calls `hal.command("teleop.native.gripper_command", native_gripper_payload(config, side, target_mm))` only when `target_mm is not None`; a `None` target returns `GripperResult(True, "HAL-native gripper state updated; no position command required", details={"nativeManaged": True})`.
- `DirectGripperAdapter`: delegates probe/position/diagnose/command to `hardware.gripper` through `asyncio.to_thread`; `status` uses `hardware.gripper.probe(config)` and `result_to_status`.

- [ ] **Step 2: Create `backend/services/gripper_router.py`**

Implement:

```python
from __future__ import annotations

from typing import Any

from backend.drivers.gripper_rs485 import GripperResult
from backend.services.gripper_backend import DirectGripperAdapter, GripperBackend, NativeGripperAdapter, WorkerGripperAdapter


class GripperRouter:
    def __init__(
        self,
        *,
        native: NativeGripperAdapter,
        worker: WorkerGripperAdapter,
        direct: DirectGripperAdapter,
    ) -> None:
        self._native = native
        self._worker = worker
        self._direct = direct

    def select(self, config: dict[str, Any]) -> GripperBackend:
        if self._worker.is_enabled(config):
            return self._worker
        if self._native.is_enabled(config):
            return self._native
        return self._direct

    def backend_name(self, config: dict[str, Any]) -> str:
        return self.select(config).name

    def is_native(self, config: dict[str, Any]) -> bool:
        return self.select(config) is self._native

    async def status(self, config: dict[str, Any]) -> dict[str, Any]:
        return await self.select(config).status(config)

    async def position(self, config: dict[str, Any], side: str) -> GripperResult:
        return await self.select(config).position(config, side)

    async def diagnose(self, config: dict[str, Any], side: str) -> GripperResult:
        return await self.select(config).diagnose(config, side)

    async def command(self, config: dict[str, Any], side: str, command: str, target_mm: float | None) -> GripperResult:
        return await self.select(config).command(config, side, command, target_mm)

    async def stop(self) -> None:
        await self._worker.stop()
```

- [ ] **Step 3: Run router tests**

Run:

```powershell
python -m pytest backend/tests/test_gripper_router.py -q
```

Expected: all tests pass.

## Task 3: Wire Router Into `create_app()` Status And Position Paths

**Files:**
- Modify: `backend/app.py`
- Modify: `backend/tests/test_app.py`

- [ ] **Step 1: Instantiate router in `create_app()`**

After `gripper_workers`, `hal`, and `teleop_mapper` exist, construct:

```python
native_adapter = NativeGripperAdapter(hal, teleop_mapper)
worker_adapter = WorkerGripperAdapter(gripper_workers)
direct_adapter = DirectGripperAdapter(hardware)
gripper_router = GripperRouter(native=native_adapter, worker=worker_adapter, direct=direct_adapter)
```

Add `app.state.gripper_router = gripper_router`.

- [ ] **Step 2: Replace status route branching**

In `/api/health` and `/api/hardware/status`, call:

```python
hardware_status = await asyncio.to_thread(hardware.status, include_gripper=False)
hardware_status["gripper"] = await gripper_router.status(config)
```

Keep the existing `hardware_status["omega7"] = await omega7_serial_status(health_config, hal_health)` and `status["omega7"] = await omega7_serial_status(config)` calls unchanged in this task.

- [ ] **Step 3: Replace gripper position and diagnose route branching**

For `/api/gripper/{side}/position`, use:

```python
result = await gripper_router.position(config, side)
if not result.ok:
    raise HTTPException(status_code=503, detail={"code": "GRIPPER_UNAVAILABLE", "message": result.message})
payload = result.__dict__
payload.update(result.details if result.details.get("nativeManaged") else {})
return envelope(payload)
```

For `/api/gripper/{side}/diagnose`, use the same result-to-envelope pattern and keep the direct log call only for non-native/non-worker results if needed.

- [ ] **Step 4: Replace websocket native gripper status branch**

In the websocket loop:

```python
active_native_gripper_status = (
    await gripper_router.status(active_config)
    if gripper_router.is_native(active_config)
    else None
)
```

Keep the existing `telemetry.next_frame` call unchanged except for passing the router-derived `native_gripper_status=active_native_gripper_status`.

- [ ] **Step 5: Add compatibility assertion**

Add to `backend/tests/test_app.py` near app-state tests:

```python
def test_create_app_exposes_gripper_router(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))

    assert client.app.state.gripper_router is not None
    assert client.app.state.gripper_router.backend_name(client.app.state.settings.get_config()) in {
        "dual_worker",
        "hal_native",
        "python_rs485",
    }
```

- [ ] **Step 6: Run app status tests**

Run:

```powershell
python -m pytest backend/tests/test_app.py -k "hardware_status or health or native_gripper or create_app_exposes_gripper_router" -q
```

Expected: all selected tests pass. Existing assertions that `include_gripper` is `False` should remain true.

## Task 4: Refactor `CommandService.gripper_command()` To Use Router

**Files:**
- Modify: `backend/services/command_service.py`
- Modify: `backend/app.py`
- Modify: `backend/tests/test_gripper_worker_service.py`

- [ ] **Step 1: Add optional router dependency**

Change `CommandService.__init__` to accept:

```python
gripper_router: Any | None = None
```

Assign `self.gripper_router = gripper_router`.

In `create_app()`, pass `gripper_router=gripper_router` when constructing `CommandService`.

- [ ] **Step 2: Keep current fallback when no router is supplied**

Existing unit tests construct `CommandService` directly. To keep them surgical, either update those tests to pass a router or keep a private fallback selector inside `CommandService` until the tests are migrated. The preferred implementation is to update fakes and pass a real `GripperRouter` built from fake adapters.

- [ ] **Step 3: Move native helper calls to shared helpers**

Replace calls to `self._hal_native_teleop`, `self._native_gripper_payload`, and `self._hal_response_message` with imported helpers from `backend.services.gripper_backend`.

Keep `_gripper_command_target`, `_gripper_dispatch_command`, `_save_gripper_command_state_async`, `_validate_gripper_command_enabled`, and `_log_gripper_command` in `CommandService`.

- [ ] **Step 4: Dispatch real hardware through the router**

Inside the real-hardware branch:

```python
backend_name = self.gripper_router.backend_name(config)
target = self._gripper_command_target(config, request)

if backend_name == "hal_native":
    if target is None and request.command == "enable":
        target_key = "targetLeftMm" if request.side == "left" else "targetRightMm"
        target = protected_gripper_target_mm(config, float(config.get("gripper", {}).get(target_key, 0.0)))
    result = await self.gripper_router.command(config, request.side, request.command, target)
else:
    self._validate_gripper_command_enabled(config, request)
    dispatch_command, dispatch_target = self._gripper_dispatch_command(config, request, target)
    result = await self.gripper_router.command(config, request.side, dispatch_command, dispatch_target)
```

After dispatch, preserve existing behavior:

- on failure, log with backend name and raise `RuntimeError(result.message)`;
- save gripper command state only after successful real-hardware dispatch;
- return `{"message": result.message}` for worker/direct plus `targetMm` when target exists;
- return `{"message": result.message, "nativeManaged": True, "targetMm": target, "hal": result.details["hal"]}` for native command with HAL result;
- return `{"message": result.message, "nativeManaged": True}` for native enable/disable state-only commands with no HAL result.

- [ ] **Step 5: Keep test-mode local telemetry fallback unchanged**

Do not route test-mode local gripper simulation through `GripperRouter`.

- [ ] **Step 6: Run command-service gripper tests**

Run:

```powershell
python -m pytest backend/tests/test_gripper_worker_service.py -k "gripper_command or native_gripper or dual_worker" -q
```

Expected: all selected tests pass, including nonblocking worker/direct tests.

## Task 5: Move Precheck Gripper Selection Behind Router

**Files:**
- Modify: `backend/app.py`

- [ ] **Step 1: Update `require_hardware_recognized()`**

Use:

```python
backend_name = gripper_router.backend_name(config)
include_gripper_probe = require_gripper and backend_name == "python_rs485"
hardware_status = await asyncio.to_thread(hardware.status, include_gripper=False)
```

Then, only when `require_gripper` is true:

```python
gripper_status = await gripper_router.status(config)
if not isinstance(gripper_status, dict) or not bool(gripper_status.get("ok", False)):
    failures.append(str(gripper_status.get("message") if isinstance(gripper_status, dict) else "gripper serial not ready"))
```

For native and worker modes this must avoid Python RS485 probing. For direct mode the router does the direct probe explicitly.

- [ ] **Step 2: Run recording precheck tests**

Run:

```powershell
python -m pytest backend/tests/test_app.py -k "record_session or precheck or require_hardware or gripper_probe" -q
```

Expected: tests that assert no COM9 failure leaks in native mode still pass.

## Task 6: Remove Old Local Gripper Helpers From `app.py`

**Files:**
- Modify: `backend/app.py`

- [ ] **Step 1: Delete helpers now owned by the router**

Remove local definitions of:

- `native_teleop_config`
- `native_gripper_status`
- `gripper_serial_ports`
- `attach_gripper_serial_ports`

Replace remaining calls with:

- `gripper_router.is_native(config)`
- `await gripper_router.status(config)`
- helper import `native_teleop_enabled(config)` only where startup teleop cleanup still needs the engine check.

- [ ] **Step 2: Run targeted tests**

Run:

```powershell
python -m pytest backend/tests/test_app.py -k "native or gripper or health or hardware_status or websocket" -q
python -m pytest backend/tests/test_gripper_router.py backend/tests/test_gripper_worker_service.py -q
```

Expected: all selected tests pass.

## Task 7: Add `AppServices` Without Moving Routes

**Files:**
- Create: `backend/app_factory.py`
- Modify: `backend/app.py`
- Modify: `backend/tests/test_app.py`

- [ ] **Step 1: Create service dataclass**

Create `backend/app_factory.py` with:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.core.config import SettingsService
from backend.core.logging import LogService
from backend.hal_client.client import HalClient
from backend.services.command_service import CommandService
from backend.services.dataset_recorder import DatasetRecorderService
from backend.services.gripper_router import GripperRouter
from backend.services.gripper_tele_service import GripperTeleService
from backend.services.gripper_worker_service import GripperWorkerService
from backend.services.hardware_service import HardwareService
from backend.services.policy_service import PolicyService
from backend.services.stability_monitor import StabilityMonitorService
from backend.services.telemetry_hub import TelemetryHub
from backend.services.teleop_mapping import TeleopMappingService


@dataclass
class AppServices:
    runtime_dir: Path
    logs: LogService
    settings: SettingsService
    hardware: HardwareService
    gripper_workers: GripperWorkerService
    telemetry: TelemetryHub
    hal: HalClient
    teleop_mapper: TeleopMappingService
    gripper_router: GripperRouter
    commands: CommandService
    gripper_tele: GripperTeleService
    recorder: DatasetRecorderService
    stability: StabilityMonitorService
    policy: PolicyService
```

Add `create_services(runtime_dir, *, make_hal_client_fn) -> AppServices` by moving only the service-instantiation block from `create_app()`. Keep startup logging in `create_app()` for now, so route behavior and log timing do not change in this increment.

- [ ] **Step 2: Use `create_services()` in `create_app()`**

After calling `create_services`, assign:

```python
svc = create_services(active_runtime_dir, make_hal_client_fn=make_hal_client)
app.state.services = svc
app.state.logs = svc.logs
app.state.settings = svc.settings
app.state.telemetry = svc.telemetry
app.state.commands = svc.commands
app.state.hal = svc.hal
app.state.hardware = svc.hardware
app.state.gripper_workers = svc.gripper_workers
app.state.gripper_router = svc.gripper_router
app.state.teleop_mapper = svc.teleop_mapper
app.state.gripper_tele = svc.gripper_tele
app.state.recorder = svc.recorder
app.state.stability = svc.stability
app.state.policy = svc.policy
```

Keep local variables by assigning them from `svc` immediately after this block so existing routes remain unchanged.

- [ ] **Step 3: Add tests**

Add:

```python
def test_create_app_exposes_app_services_and_legacy_state_attrs(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path))
    services = client.app.state.services

    assert client.app.state.logs is services.logs
    assert client.app.state.settings is services.settings
    assert client.app.state.commands is services.commands
    assert client.app.state.gripper_router is services.gripper_router
```

- [ ] **Step 4: Run app creation tests**

Run:

```powershell
python -m pytest backend/tests/test_app.py -k "create_app or import_does_not_create_runtime_services or settings_round_trip" -q
```

Expected: all selected tests pass.

## Task 8: Final Verification For This Increment

**Files:**
- No source files unless verification exposes failures.

- [ ] **Step 1: Run focused backend tests**

Run:

```powershell
python -m pytest backend/tests/test_gripper_router.py backend/tests/test_gripper_driver.py backend/tests/test_gripper_worker_service.py backend/tests/test_gripper_tele_service.py -q
```

Expected: all tests pass.

- [ ] **Step 2: Run app tests**

Run:

```powershell
python -m pytest backend/tests/test_app.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run the backend suite**

Run:

```powershell
python -m pytest backend/tests/ -q
```

Expected: all tests pass. If dataset extras are missing, tests that already call `pytest.skip()` remain skipped.

- [ ] **Step 4: Review diff**

Run:

```powershell
git diff -- backend/app.py backend/app_factory.py backend/services/command_service.py backend/services/gripper_backend.py backend/services/gripper_router.py backend/tests/test_app.py backend/tests/test_gripper_router.py backend/tests/test_gripper_worker_service.py
```

Expected: diff contains only the router, service-factory preparation, and tests described above.

## Out Of Scope For This Increment

- Moving `omega7_serial_status`, `sync_teleop_logical_connection`, and runtime shutdown helpers into services.
- Creating `backend/routes/` modules for every route group.
- Splitting frontend stores.
- Changing HAL, driver, worker process, or frontend behavior.

## Next Increment Recommendation

After this plan passes, write a separate route-extraction plan in this order:

1. Move `health` and `settings` routes first, because they are low-risk and heavily tested.
2. Move `gripper` routes next, because the router introduced here makes them small.
3. Move `motion` and `teleop` routes after helper ownership is settled.
4. Move `recording`, `runtime`, and `websocket` last, because they carry lifecycle and long-running behavior.
