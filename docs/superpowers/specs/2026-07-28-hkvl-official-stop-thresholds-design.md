# HKVL-36A Official Stop Thresholds Design

## Goal

Temporarily use the HKVL-36A published full-scale ratings as the configured force-stop ceilings until application-specific thresholds are established by commissioning tests.

## Values

- Fx/Fy stop: `30 N`
- Fz stop: `30 N`
- Mx/My/Mz stop: `1 Nm`
- Fx/Fy warning: retain `2 N`
- Fz warning: retain `3 N`
- Moment warning: retain `0.02 Nm`
- Watchdog: retain `50 ms`

The stop values are sensor full-scale ratings, not manufacturer-recommended robot safety thresholds. Warning values and watchdog timing remain provisional engineering settings and must not be labeled as official.

## Scope

Update backend defaults, the active runtime configuration, frontend defaults and input fallbacks, tests, and project reference documentation. Preserve the existing HAL trip algorithm and the validation rule `0 < warn < stop <= sensor full scale`.

Do not automatically apply the new thresholds to the running HAL until native teleoperation is stopped, all axes are stopped, and all servos are disabled.

## Verification

Use tests to prove the default and HAL payload values are `30 N / 1 Nm`, verify values above those limits remain rejected, then run backend and frontend test/build checks relevant to the modified files.
