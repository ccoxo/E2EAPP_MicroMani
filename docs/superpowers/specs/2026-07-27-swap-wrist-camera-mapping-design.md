# Swap Wrist Camera Mapping

## Goal

Swap the logical left- and right-wrist camera assignments in the active runtime configuration while leaving the global camera unchanged.

## Scope

Modify only `backend/runtime/config.json`:

- Exchange `cameras.wristLeft` and `cameras.wristRight`.
- Exchange `cameras.wristLeftIdentity` and `cameras.wristRightIdentity`.
- Do not change `cameras.global`, camera tuning, resolution, or recording code.

## Result

The active mapping becomes:

- `global` -> `IMX335 / index 0`
- `wristLeft` -> `IMX335 / index 2`
- `wristRight` -> `IMX335 / index 1`

The corresponding USB identities move with their assigned wrist cameras so identity-based resolution and index-based fallback remain consistent.

## Verification

- Parse `backend/runtime/config.json` as JSON.
- Assert the global descriptor and identity are unchanged.
- Assert both wrist descriptors and both wrist identities are exactly exchanged.
- Confirm no other configuration fields changed.
