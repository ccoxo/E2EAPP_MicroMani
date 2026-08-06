# Force Chart Side Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make each HKVL card's historical chart use the same hardware side as its current values and status.

**Architecture:** Preserve the existing telemetry and chart contracts. Convert operator side to hardware side once in `ForceSensorCard`, then use that value for current data, status, Tare, configuration, and history.

**Tech Stack:** React 18, TypeScript, Zustand, Vitest, Testing Library, ECharts mock

---

### Task 1: Add a side-mapping regression test

**Files:**
- Modify: `frontend/src/App.test.tsx`
- Test: `frontend/src/App.test.tsx`

- [ ] **Step 1: Write the failing test**

Render `SettingsView` with two telemetry-history samples whose `forceLeft` and `forceRight` values are intentionally distinct. Inspect the mocked chart options under each force card and assert operator-left plots hardware-right history while operator-right plots hardware-left history.

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- src/App.test.tsx -t "plots force history from the card's mapped hardware side"`

Expected: FAIL because the operator-left card receives `forceLeft` and the operator-right card receives `forceRight`.

### Task 2: Correct the chart side

**Files:**
- Modify: `frontend/src/views/SettingsView.tsx:1586`

- [ ] **Step 1: Write minimal implementation**

Change:

```tsx
<ForceChart history={history} side={side} height={170} />
```

to:

```tsx
<ForceChart history={history} side={hardwareSide} height={170} />
```

- [ ] **Step 2: Run the targeted test**

Run: `npm test -- src/App.test.tsx -t "plots force history from the card's mapped hardware side"`

Expected: PASS.

- [ ] **Step 3: Run frontend verification**

Run:

```text
npm test
npm run typecheck
npm run build
```

Expected: all commands pass without new failures.
