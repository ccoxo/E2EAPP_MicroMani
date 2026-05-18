# Frontend UI Optimization Design

Date: 2026-05-18

## Objective

Optimize the AppStation frontend so operators can inspect and use every visible action safely and efficiently. The work covers button behavior, button layout, page layout, color hierarchy, before/after comparisons for state-changing actions, and other frontend issues found during audit.

## Current Evidence

- The frontend is a Vite React app using Ant Design, lucide-react, Zustand, and a large shared stylesheet in `frontend/src/index.css`.
- The app builds and serves locally at `http://127.0.0.1:5173`.
- Rendered audit screenshots and button data were generated under `frontend/output/ui-audit-current/`.
- Existing uncommitted work already touches teleoperation defaults and `frontend/src/views/SettingsView.tsx`; implementation must preserve and build around those changes.

The audit found these priority issues:

- Settings has the densest action surface. It exposes HAL reconnect, safety simulation and reset, motion enable/disable/home/snapshot, camera tuning, force tare/export, gripper commands, Omega.7 connection, manual axis movement, manual recording, replay, and snapshot deletion in one route.
- Several high-risk actions execute without an explicit before/after comparison. Examples: camera parameter apply, motion parameter save/apply, capture or clear origin, enable or disable axes, home, gripper target execution, model start/stop.
- Some button labels repeat without enough context. Examples: `Start`, `Stop`, `使能`, `停止`, `回零`, and icon-only delete buttons.
- Floating emergency stop overlaps low-right content on dashboard, record, settings, and manual views, especially in dense settings sections.
- The current palette is usable but too flat in dense areas. Many pale blue/gray surfaces compete with critical statuses instead of making normal, warning, danger, and primary actions immediately distinct.
- Mobile and narrow layouts stack correctly in many places, but header actions and floating controls can crowd the first viewport.

## Assumptions

- This is an operational robot hardware console, not a marketing UI. The design should stay compact, precise, and work-focused.
- Existing Ant Design components should remain the base. No new dependency should be added.
- The global emergency stop remains persistent and prominent.
- "Before/after comparison" means showing the current state or saved value next to the proposed state or expected result before the operator commits a risky or persistent action.
- Debug-only controls can be visually demoted, but should not be removed unless they are redundant or unsafe.

## Recommended Approach

Use a focused interaction-system pass rather than a full redesign. The implementation should introduce reusable UI helpers for action grouping and before/after confirmation, then apply them to the highest-risk pages first.

This approach is preferred because it improves the real operator workflow without splitting routes, changing backend contracts, or rewriting large view files.

## Interaction Design

### Button Hierarchy

Buttons should follow a consistent hierarchy:

- Primary: the main safe forward action in a local task, such as `保存硬件快照`, `确认开始`, `标记有效`, or `启动服务`.
- Default: inspection, navigation, export, detection, and non-destructive utility actions.
- Danger: destructive, safety-related, or hardware-motion risk actions, such as emergency stop, clear origin, delete, disable, forced stop, simulated danger, and homing.
- Text or icon-only: only for repeated compact utilities when an accessible label and tooltip are present.

Repeated labels should include context when the surrounding UI does not make scope obvious. For example, model cards should use `启动 ACT` or `停止 ACT`, and manual memory delete buttons should have aria labels and tooltips.

### Action Grouping

Each dense hardware card should have a small primary action group and a secondary "more actions" group. The visible row should not mix unrelated risk levels.

Recommended grouping:

- HAL card: visible `重连`; status and connection fields remain in the body.
- Safety card: visible `复位确认`; `模拟危险` demoted to a debug-style secondary action.
- Motion card: visible status plus `使能全部` or `断使能`; `回零`, `急停`, snapshot load, origin capture, and origin clear should be separated by intent.
- Camera card: visible `应用参数` and `重连预览`; applying parameters uses before/after diff.
- Force card: visible `Tare`; `CSV` remains a secondary utility action.
- Gripper card: visible `执行目标`; open, close, home, stop, enable, and disable remain grouped but clarified by side.
- Teleop card: connection and gravity/force controls are grouped separately from calibration and safety actions.
- Manual control: movement, stop, enable/disable, self-check, and emergency stop should be visually separated inside each side card.

### Before/After Comparison

Create a reusable confirmation surface that can show:

- Title and risk tone.
- Current state or current values.
- Proposed state or proposed values.
- Impact scope.
- Optional expected result after the operation.
- Confirm and cancel actions.

Use it for:

- Applying camera tuning: current exposure, gain, auto exposure, white balance, resolution, FPS -> proposed values.
- Saving or applying parameter snapshots: active scope and current values -> snapshot values or saved target.
- Saving motion parameters: card, profile, soft limits, origin status -> snapshot target.
- Capturing origin: current origin validity and pulses -> proposed origin state.
- Clearing origin: current valid origin -> invalidated origin for the selected side.
- Enabling or disabling motion axes: current effective enabled state -> requested state.
- Homing: current side and origin status -> home command target.
- Gripper target execution: current opening and enabled state -> target opening or open/close/home command.
- Model start/stop: current active model -> requested active/stopped state.
- Dataset delete and episode delete already use Popconfirm, but should keep descriptive titles and accessible labels.

Low-risk actions such as log export, route navigation, and simple playback controls do not need the comparison surface.

## Visual Design

The visual direction is a restrained industrial console:

- Keep the neutral light background, but make content bands and panels use clearer contrast.
- Use blue only for primary commit actions and active navigation.
- Use green/teal for healthy connected states, amber for warning or partial readiness, red for danger and destructive actions.
- Reduce decorative gradients in dense control surfaces. Use borders, left accents, compact tags, and spacing to create hierarchy.
- Keep card radius at 8px or less.
- Preserve tabular numeric rendering for telemetry.
- Avoid oversized headings inside panels. Use compact section titles and stable control dimensions.

## Layout Design

Global shell:

- Keep the left navigation on desktop and horizontal nav on mobile.
- Add bottom/right content padding so the floating emergency stack does not cover important buttons.
- Hide or relocate the floating settings shortcut where the settings nav item is already visible.

Settings:

- Keep two tabs: hardware configuration and manual control.
- Add a compact action toolbar pattern to hardware cards.
- Make card headers wrap predictably without crowding buttons.
- Move high-risk actions near related state readouts instead of placing every action in the card header.
- Keep existing hash focusing behavior for dashboard-to-settings navigation.

Manual control:

- Keep side-by-side cards on desktop.
- Use stable grid rows for movement controls.
- Separate movement actions from safety and enable controls.
- Clarify side-specific repeated labels.

Model and fine-tune:

- Replace ambiguous English action labels with scoped Chinese labels.
- Keep model precision controls local to each card.
- Group start and stop actions consistently.

Dataset and record:

- Keep existing cockpit layouts.
- Ensure destructive buttons keep confirmations and accessible names.
- Playback controls do not need before/after diff, but should keep stable sizing and labels.

## Data Flow

Most changes are local UI state changes around existing handlers. The implementation should not alter backend API contracts.

Before/after comparison data should be derived from existing config, telemetry, and local form state at the moment the operator clicks an action. Confirming the modal calls the existing handler. On success, the existing log injection and telemetry refresh behavior should remain in place.

## Error Handling

Existing try/catch behavior should remain. If a confirmed action fails, the current log panel behavior should still receive the error. Confirmation modals should close only when the action is accepted by the existing handler path or when the user cancels.

## Testing And Verification

Implementation is complete only when:

- `npm run typecheck` passes.
- `npm run build` passes.
- Relevant Vitest tests pass, with updates or additions for before/after confirmation behavior.
- A rendered browser audit is repeated for desktop and narrow mobile widths.
- Screenshots show that emergency stop remains visible and no longer covers primary page actions.
- Settings, manual control, model, record, dataset, fine-tune, auto, and dashboard routes all render without obvious overlap or inaccessible critical actions.

## Out Of Scope

- Adding new dependencies.
- Changing backend hardware behavior or API contracts.
- Splitting settings into new routes.
- Removing safety or debug capabilities without explicit confirmation.
