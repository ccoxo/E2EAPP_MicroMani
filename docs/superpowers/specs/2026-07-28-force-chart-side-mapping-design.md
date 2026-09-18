# Force Chart Side Mapping Design

## Goal

Ensure each force-sensor card plots the same physical HKVL sensor that supplies its current-value boxes and status metrics.

## Root cause

The settings page converts the operator-facing arm side to `hardwareSide` for current values, status, port, and Tare. The chart still receives the unconverted operator-facing `side`, so `ForceChart` selects the opposite `forceLeft`/`forceRight` history array.

## Design

Keep `ForceChart` and the telemetry history contract unchanged. Inside `ForceSensorCard`, pass the already computed `hardwareSide` to `ForceChart`. This is the smallest correction and preserves the intentional operator-side/hardware-side mapping everywhere else.

## Verification

Add a frontend regression test with distinct left/right force-history values. It must assert that the operator-left card (hardware right) plots `forceRight`, while the operator-right card (hardware left) plots `forceLeft`. Run the targeted test, the full frontend test suite, type checking, and the production build.
