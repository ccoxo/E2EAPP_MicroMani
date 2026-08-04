# HKVL Reconfiguration and Tare Preservation Design

## Goal

Prevent an unchanged HKVL-36A configuration from restarting the force runtime, re-latching safety, and clearing the in-memory Tare bias. Make an invalid runtime configuration report the precise validation failure before it is restored to defaults.

## Evidence

The 2026-08-04 runtime log shows `config.json was invalid; default config restored` twice. Each reset changes `force.source` from `hkvl_serial` to `nidaq`; the UI then writes `hkvl_serial` back. Switching to HKVL calls `force.configure`, which deliberately creates the `force_configuration_pending` latch and restarts the serial driver. Driver startup resets `tareBias` to zero. Safety acknowledgement requires both sensors to be fresh and below warning thresholds for 500 ms, so a static sensor bias can prevent acknowledgement until a new Tare completes.

## Scope

### Configuration application

`POST /api/settings/apply` receives the complete UI configuration. It will send `force.configure` only when the derived HAL force payload differs from the active one.

An explicit API call without a configuration body remains a force-runtime reapply request and continues to send `force.configure`. The existing UI always sends a body, so unrelated UI settings changes will not restart HKVL.

### Tare behavior

No Tare value will be retained across a genuine force-runtime reconfiguration. A change to port, protocol, baud rate, force-safety threshold, axis sign, filter, or compliance settings must still restart the runtime and invalidate the old zero reference. The change only prevents unrelated settings from causing that restart.

### Invalid configuration diagnostics

When runtime configuration loading fails, the backend will retain its existing safe recovery behavior (restore defaults) and add the exception type and validation message to the warning log. It must not include the entire configuration payload.

## Out of Scope

- Changing safety thresholds, watchdog timing, or acknowledgement rules.
- Bypassing force safety for rotation-axis tests.
- Preserving Tare across a genuine HKVL configuration change.
- Changing serial-device reconnect behavior.

## Error Handling

- A changed force payload continues to be rejected by HAL unless motion is stopped and servos are disabled; the configuration file remains unchanged on rejection.
- An invalid configuration still restores safe defaults, now with an actionable diagnostic message.

## Verification

Add backend tests that verify:

1. applying an unchanged HKVL configuration with a body does not call `force.configure`;
2. an explicit bodyless reapply still calls `force.configure`;
3. a changed HKVL force payload still calls `force.configure` before it is saved; and
4. invalid configuration recovery logs the validation error while restoring defaults.

Existing HAL tests continue to verify that a real force reconfiguration latches safety and requires healthy, unloaded samples before acknowledgement.
