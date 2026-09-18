# HKVL Reconfiguration and Tare Preservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Avoid restarting the HKVL force runtime for an unchanged UI configuration while preserving explicit reapply behavior and making invalid configuration recovery actionable.

**Architecture:** The settings-apply route will distinguish an explicit bodyless reapply from a UI configuration submission. Only the former or a changed derived HAL force payload will call `force.configure`; this prevents unrelated UI changes from restarting the driver and clearing its in-memory Tare bias. Settings recovery remains fail-safe, but records the caught validation error in the warning log.

**Tech Stack:** Python 3.11, FastAPI, Pydantic, pytest, existing HAL command client.

---

## File Structure

- Modify: `backend/app.py` — gate the `force.configure` command in `POST /api/settings/apply` on an explicit bodyless reapply or a changed derived force payload.
- Modify: `backend/core/config.py` — include the caught exception type and message in invalid-config recovery logs.
- Modify: `backend/tests/test_app.py` — cover unchanged HKVL submissions, explicit reapply, and changed force payloads.
- Modify: `backend/tests/test_diagnostic_logging.py` — cover safe default recovery with a diagnostic validation message.

### Task 1: Prevent unchanged HKVL submissions from restarting force acquisition

**Files:**
- Modify: `backend/tests/test_app.py` after `test_force_runtime_settings_are_not_saved_when_hal_rejects_them`
- Modify: `backend/app.py:839-865`

- [ ] **Step 1: Write failing route tests**

Add these tests to `backend/tests/test_app.py`:

```python
def test_apply_settings_does_not_reconfigure_unchanged_hkvl_payload(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["force"]["source"] = "hkvl_serial"
    _app_state(client).settings.save_config(config, emit_log=False)
    calls: list[tuple[str, dict[str, Any]]] = []

    async def capture_force_config(name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((name, payload or {}))
        return {"ok": True}

    monkeypatch.setattr(_app_state(client).hal, "command", capture_force_config)

    response = client.post("/api/settings/apply", json=config)

    assert response.status_code == 200
    assert calls == []


def test_apply_settings_bodyless_request_reapplies_hkvl_force_runtime(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    client = TestClient(create_app(tmp_path))
    config = client.get("/api/settings").json()
    config["force"]["source"] = "hkvl_serial"
    _app_state(client).settings.save_config(config, emit_log=False)
    calls: list[tuple[str, dict[str, Any]]] = []

    async def capture_force_config(name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((name, payload or {}))
        return {"ok": True}

    monkeypatch.setattr(_app_state(client).hal, "command", capture_force_config)

    response = client.post("/api/settings/apply")

    assert response.status_code == 200
    assert calls == [("force.configure", {
        "source": "hkvl_serial",
        "protocol": "hkvl_active_v1",
        "leftPort": "COM15",
        "rightPort": "COM14",
        "leftAxisSign": [-1.0, 1.0, -1.0, 1.0, -1.0, -1.0],
        "rightAxisSign": [-1.0, -1.0, -1.0, 1.0, 1.0, 1.0],
        "baudrate": 1_000_000,
        "expectedSampleHz": 1000,
        "lowpassEnabled": True,
        "lowpassCutoffHz": 10.0,
        "fxyWarnN": 2.0,
        "fxyStopN": 30.0,
        "fzWarnN": 3.0,
        "fzStopN": 30.0,
        "momentWarnNm": 0.02,
        "momentStopNm": 1.0,
        "watchdogMs": 50.0,
        "acknowledgeStableMs": 500,
        "complianceEnabled": False,
        "leftMappingConfirmed": False,
        "leftComplianceMatrix": [1.0, 0.0, 0.0, 1.0],
        "leftComplianceDeadbandN": [0.0, 0.0],
        "leftComplianceGainUmPerNs": [0.0, 0.0],
        "leftComplianceMaxStepUm": [0.0, 0.0],
        "leftComplianceMaxOffsetUm": [0.0, 0.0],
        "rightMappingConfirmed": False,
        "rightComplianceMatrix": [1.0, 0.0, 0.0, 1.0],
        "rightComplianceDeadbandN": [0.0, 0.0],
        "rightComplianceGainUmPerNs": [0.0, 0.0],
        "rightComplianceMaxStepUm": [0.0, 0.0],
        "rightComplianceMaxOffsetUm": [0.0, 0.0],
    })]


def test_apply_settings_reconfigures_changed_hkvl_force_payload(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    client = TestClient(create_app(tmp_path))
    current = client.get("/api/settings").json()
    current["force"]["source"] = "hkvl_serial"
    _app_state(client).settings.save_config(current, emit_log=False)
    candidate = deepcopy(current)
    candidate["force"]["lowpassCutoffHz"] = 15
    calls: list[tuple[str, dict[str, Any]]] = []

    async def capture_force_config(name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        calls.append((name, payload or {}))
        return {"ok": True}

    monkeypatch.setattr(_app_state(client).hal, "command", capture_force_config)

    response = client.post("/api/settings/apply", json=candidate)

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0][0] == "force.configure"
    assert calls[0][1]["source"] == "hkvl_serial"
    assert calls[0][1]["lowpassCutoffHz"] == 15.0
```

- [ ] **Step 2: Run the new route tests and verify the unchanged-HKVL test fails**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_app.py -k "apply_settings and hkvl" -q
```

Expected: the unchanged-HKVL test fails because the current `or "hkvl_serial" in {current_source, candidate_source}` condition invokes `force.configure`; the bodyless and changed-payload tests pass or exercise the existing call.

- [ ] **Step 3: Replace the unconditional HKVL condition with explicit reapply semantics**

In `backend/app.py`, remove `current_source` and `candidate_source` and replace the conditional with:

```python
if config is None or current_force_payload != candidate_force_payload:
    await hal.command("force.configure", candidate_force_payload)
```

Keep `candidate = current` for a bodyless call. Do not change `PUT /api/settings`, snapshot application, HAL safety logic, or Tare code.

- [ ] **Step 4: Run the route tests and verify they pass**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_app.py -k "apply_settings and hkvl" -q
```

Expected: PASS; unchanged body submissions make no HAL command, while bodyless reapply and an actual force-payload change each make exactly one `force.configure` call.

- [ ] **Step 5: Commit the configuration-application change**

```powershell
git add backend/app.py backend/tests/test_app.py
git commit -m "fix: avoid unnecessary HKVL reconfiguration"
```

### Task 2: Log the invalid configuration reason before safe recovery

**Files:**
- Modify: `backend/tests/test_diagnostic_logging.py:1-8` and after `test_settings_save_logs_config_write_hash_and_changed_keys`
- Modify: `backend/core/config.py:479-482`

- [ ] **Step 1: Write the failing recovery-log test**

Add `import json` alongside the existing imports, then add:

```python
def test_invalid_config_recovery_logs_validation_reason(tmp_path: Path) -> None:
    logs = LogService(emit_startup=False)
    invalid = default_config()
    invalid["force"]["source"] = "unsupported-force-source"
    (tmp_path / "config.json").write_text(json.dumps(invalid), encoding="utf-8")

    config = SettingsService(tmp_path, logs).get_config()

    assert config["force"]["source"] == "nidaq"
    assert [entry.msg for entry in logs.list_entries()] == [
        "config.json was invalid; default config restored: "
        "ValueError: force.source must be nidaq or hkvl_serial"
    ]
```

Add `from backend.core.defaults import default_config` with the existing imports if it is not already present.

- [ ] **Step 2: Run the recovery-log test and verify it fails**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_diagnostic_logging.py::test_invalid_config_recovery_logs_validation_reason -q
```

Expected: FAIL because the current warning omits `ValueError: force.source must be nidaq or hkvl_serial`.

- [ ] **Step 3: Include the caught validation error in the warning**

In `backend/core/config.py`, bind the recovery exception and replace the warning:

```python
except (OSError, json.JSONDecodeError, ValueError) as exc:
    config = default_config()
    self.save_config(config, source="startup")
    self.logs.warning(
        "[BACKEND]",
        f"config.json was invalid; default config restored: {type(exc).__name__}: {exc}",
    )
    return config
```

Do not log the raw configuration file or a traceback.

- [ ] **Step 4: Run diagnostics tests and verify they pass**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_diagnostic_logging.py -q
```

Expected: PASS; invalid input restores the default force source and records only the error class and validation message.

- [ ] **Step 5: Commit the recovery diagnostic change**

```powershell
git add backend/core/config.py backend/tests/test_diagnostic_logging.py
git commit -m "fix: log invalid runtime configuration reason"
```

### Task 3: Run focused regression verification

**Files:**
- Verify only: `backend/app.py`, `backend/core/config.py`, `backend/tests/test_app.py`, `backend/tests/test_diagnostic_logging.py`

- [ ] **Step 1: Run the combined focused suite**

Run:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests/test_app.py backend/tests/test_diagnostic_logging.py -q
```

Expected: PASS with no collection errors.

- [ ] **Step 2: Inspect the final diff for scope**

Run:

```powershell
git diff HEAD~2..HEAD -- backend/app.py backend/core/config.py backend/tests/test_app.py backend/tests/test_diagnostic_logging.py
git status --short
```

Expected: the implementation changes only the two designed behaviors and tests; existing user changes remain unstaged and untouched.

- [ ] **Step 3: Verify the safety boundary manually in the running app**

With both arms stopped and servos disabled, apply an unchanged HKVL configuration from the Settings page. Confirm that the force status does not return to `force_configuration_pending`, no new Tare is needed solely because of that apply, and a deliberate HKVL force change still produces the safety latch.

## Self-Review

- Spec coverage: Task 1 covers unchanged UI application, explicit reapply, and changed payload behavior; Task 2 covers safe recovery diagnostics; Task 3 verifies scope and runtime safety behavior.
- Placeholder scan: the plan contains no `TODO`, `TBD`, or unspecified test steps.
- Type consistency: all new app tests use the existing `TestClient`, `MonkeyPatch`, `_app_state`, `Path`, `Any`, and `deepcopy` imports. The invalid-config test uses `LogService`, `SettingsService`, and `default_config`.
