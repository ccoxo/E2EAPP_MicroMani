# Swap Wrist Camera Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Exchange the active logical left- and right-wrist camera assignments without changing the global camera or unrelated runtime settings.

**Architecture:** Make a four-line operational configuration change in the ignored local file `backend/runtime/config.json`. Move each USB identity together with its wrist descriptor so identity-based resolution and index fallback remain aligned.

**Tech Stack:** JSON, PowerShell verification

---

### Task 1: Exchange the active wrist camera assignments

**Files:**
- Modify: `backend/runtime/config.json:16-19`
- Reference: `docs/superpowers/specs/2026-07-27-swap-wrist-camera-mapping-design.md`

- [ ] **Step 1: Save a temporary comparison copy and verify the pre-change values**

Run:

```powershell
$comparisonCopy = Join-Path $env:TEMP 'E2EAPP_MicroMani-camera-config-before.json'
Copy-Item -LiteralPath 'backend/runtime/config.json' -Destination $comparisonCopy -Force
$config = Get-Content -LiteralPath 'backend/runtime/config.json' -Raw | ConvertFrom-Json
if (
  $config.cameras.global -ne 'IMX335 / index 0' -or
  $config.cameras.wristLeft -ne 'IMX335 / index 1' -or
  $config.cameras.wristLeftIdentity -ne 'USB\VID_0ABD&PID_8050&MI_00\7&1396F44D&0&0000' -or
  $config.cameras.wristRight -ne 'IMX335 / index 2' -or
  $config.cameras.wristRightIdentity -ne 'USB\VID_0ABD&PID_8050&MI_00\8&3724732E&0&0000'
) {
  throw 'Camera configuration no longer matches the approved baseline.'
}
'PRECHECK_PASS'
```

Expected: `PRECHECK_PASS`.

- [ ] **Step 2: Apply the minimal four-line configuration change**

Replace only these properties:

```json
"wristLeft": "IMX335 / index 2",
"wristLeftIdentity": "USB\\VID_0ABD&PID_8050&MI_00\\8&3724732E&0&0000",
"wristRight": "IMX335 / index 1",
"wristRightIdentity": "USB\\VID_0ABD&PID_8050&MI_00\\7&1396F44D&0&0000"
```

Leave `global`, resolutions, tuning, and every other configuration field unchanged.

- [ ] **Step 3: Parse the modified JSON and assert the final mapping**

Run:

```powershell
$config = Get-Content -LiteralPath 'backend/runtime/config.json' -Raw | ConvertFrom-Json
if (
  $config.cameras.global -ne 'IMX335 / index 0' -or
  $config.cameras.globalIdentity -ne 'USB\VID_0ABD&PID_8050&MI_00\7&398F0A3&0&0000' -or
  $config.cameras.wristLeft -ne 'IMX335 / index 2' -or
  $config.cameras.wristLeftIdentity -ne 'USB\VID_0ABD&PID_8050&MI_00\8&3724732E&0&0000' -or
  $config.cameras.wristRight -ne 'IMX335 / index 1' -or
  $config.cameras.wristRightIdentity -ne 'USB\VID_0ABD&PID_8050&MI_00\7&1396F44D&0&0000'
) {
  throw 'Camera mapping verification failed.'
}
'MAPPING_VERIFY_PASS'
```

Expected: `MAPPING_VERIFY_PASS`.

- [ ] **Step 4: Confirm the comparison contains only the approved four lines**

Run:

```powershell
$comparisonCopy = Join-Path $env:TEMP 'E2EAPP_MicroMani-camera-config-before.json'
git diff --no-index --unified=1 -- $comparisonCopy 'backend/runtime/config.json'
```

Expected: exactly four removed property lines and four added property lines, covering only `wristLeft`, `wristLeftIdentity`, `wristRight`, and `wristRightIdentity`. Exit code `1` is expected because the two files differ.

- [ ] **Step 5: Remove the temporary comparison copy**

Run:

```powershell
$comparisonCopy = Join-Path $env:TEMP 'E2EAPP_MicroMani-camera-config-before.json'
Remove-Item -LiteralPath $comparisonCopy
```

Expected: the temporary file no longer exists. No Git commit is made for `backend/runtime/config.json` because `backend/runtime/` is intentionally ignored and stores machine-local operational state.
