# AppStation HAL

This is the Windows-only hardware access boundary for LTDMC motion cards and Omega.7 master hands.

Current status:

- Builds without vendor SDKs as a deterministic skeleton.
- Keeps Python Backend away from `LTDMC.dll` and Force Dimension SDK calls.
- Provides the mapping, limit checks, and driver seams required before enabling real hardware.

Real hardware build path:

1. Install LTDMC and Force Dimension SDKs on the workstation.
2. Add include/library paths in `CMakeLists.txt` or a local CMake preset.
3. Configure with `-DAPPSTATION_ENABLE_VENDOR_SDKS=ON`.
4. Implement the guarded SDK calls in `LTDMCDriver.cpp` and `Omega7Driver.cpp` where marked.
5. Add the HTTP/WebSocket transport layer for `/health`, `/motion/state`, `/motion/home_all`, `/motion/emergency_stop`, and telemetry broadcast.

The Python backend already has `RealHalClient`; start it with `APPSTATION_HAL_MODE=real` once this service is listening on `hal.baseUrl`.

Local workstation runtime:

- LTDMC runtime files are vendored under `hal/vendor/leishine`.
- Force Dimension runtime DLLs are vendored under `hal/vendor/force_dimension`.
- `HalServer.exe` should be started through `scripts/start-hal.ps1`; the script verifies `LTDMC.dll`, `dhd64.dll`, `drd64.dll`, and `jodellTool.dll` are present beside the executable and fails fast if `/health.ltdmc_ok` is false.
- Use `scripts/check-hal.ps1` for a non-motion health check before manual axis testing.
