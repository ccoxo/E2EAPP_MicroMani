# Home and Settings Backend Test Plan

Scope: backend contracts that support the Home dashboard and Settings hardware pages without changing the project architecture.

## Automated Coverage

1. Settings API and persistence
   - `GET /api/settings` returns a complete `AppConfig`.
   - `PUT /api/settings` round-trips config changes.
   - Settings snapshots can be created, applied, listed, and deleted.

2. Telemetry contract for Home page
   - `/ws` emits `telemetry` frames.
   - Frames include 12 joint positions, 2 gripper positions, 6-axis force arrays, camera states, process states, queue depth, and `wsOk`.
   - Real HAL unavailable mode is reported without crashing the backend.

3. Hardware default contract for Settings page
   - HAL defaults use the Python-to-C++ HAL boundary on port `8091`.
   - LTDMC, Omega.7, jodell, PICO script paths point to the reference project locations.
   - NI-DAQmx force channels match the reference hardware: left `Dev5/ai0:5`, right `Dev3/ai0:5`, 200 Hz.
   - Safety thresholds are stored in backend units: N and Nm, not display units.

4. Unit conversion and gripper command safety
   - Rotation UI values are degrees, not millidegrees.
   - Translation UI values are micrometers.
   - EPG006 target mm maps to jodell command position bytes.
   - Default gripper ports and slave IDs match the reference project.

## Commands

Run from `F:\E2EAPP_MicroMani\backend`:

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy .
```

Run from `F:\E2EAPP_MicroMani\frontend`:

```powershell
npm test -- --run
npm run lint
npm run build
```

## Current Manual Hardware Gaps

- Real LTDMC/Omega.7 calls still require `HalServer.exe` built with vendor SDKs enabled.
- NI-DAQmx probing requires the physical NI devices and ATI calibration files to be present on the workstation.
- PICO start/stop/status now route through reference BAT scripts when available, but live validation requires the PICO-4 to be reachable on `10.90.131.124:5555`.
