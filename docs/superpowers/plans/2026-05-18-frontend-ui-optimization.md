# Frontend UI Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve the AppStation frontend button hierarchy, layout, color clarity, and safety by adding before/after confirmations for state-changing actions and tightening dense control surfaces.

**Architecture:** Add one reusable confirmation component for before/after comparisons, then wire existing handlers through it. Keep backend APIs and store contracts unchanged. Apply compact CSS improvements through the existing global stylesheet and current React view structure.

**Tech Stack:** React 18, TypeScript, Ant Design, lucide-react, Zustand, Vite, Vitest, Testing Library, Playwright browser audit.

---

## File Structure

- Create `frontend/src/components/ActionCompareModal.tsx`: reusable confirmation modal for current/proposed state comparisons.
- Modify `frontend/src/App.test.tsx`: add failing tests for comparison confirmation, scoped labels, and accessible delete controls.
- Modify `frontend/src/views/SettingsView.tsx`: route high-risk settings and manual-control handlers through comparison modals; improve repeated labels and accessible names.
- Modify `frontend/src/views/ModelView.tsx`: replace ambiguous `Start`/`Stop` labels and add model start/stop comparison confirmation using the shared comparison modal.
- Modify `frontend/src/components/GlobalEmergencyStopButton.tsx`: add a layout class hook and keep accessible labels.
- Modify `frontend/src/index.css`: refine industrial console palette, card action layout, modal diff layout, manual action groups, floating emergency spacing, and mobile behavior.
- Use existing `frontend/output/ui-audit-current/` as baseline evidence only. New audit artifacts should go under `frontend/output/ui-audit-after/`.

## Task 1: Add Comparison Modal Tests And Component

**Files:**
- Create: `frontend/src/components/ActionCompareModal.tsx`
- Modify: `frontend/src/App.test.tsx`

- [ ] **Step 1: Write failing tests for comparison modal behavior**

Add these imports near the top of `frontend/src/App.test.tsx`:

```ts
import { ActionCompareModal } from './components/ActionCompareModal'
```

Add this test near the start of the describe block:

```tsx
it('renders a before and after comparison before confirming a high-risk action', () => {
  const onConfirm = vi.fn()
  const onCancel = vi.fn()

  render(
    <ActionCompareModal
      open
      title="应用相机参数"
      tone="warning"
      impact="将写入全局相机预览参数"
      current={[
        { label: 'Exposure', value: '-5.5' },
        { label: 'Gain', value: '0' },
      ]}
      proposed={[
        { label: 'Exposure', value: '-6.0' },
        { label: 'Gain', value: '12' },
      ]}
      confirmText="确认应用"
      onCancel={onCancel}
      onConfirm={onConfirm}
    />,
  )

  expect(screen.getByText('应用相机参数')).toBeInTheDocument()
  expect(screen.getByText('当前')).toBeInTheDocument()
  expect(screen.getByText('将应用')).toBeInTheDocument()
  expect(screen.getByText('将写入全局相机预览参数')).toBeInTheDocument()
  expect(screen.getByText('-5.5')).toBeInTheDocument()
  expect(screen.getByText('-6.0')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: '确认应用' }))
  expect(onConfirm).toHaveBeenCalledTimes(1)
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
npm test -- src/App.test.tsx -t "renders a before and after comparison before confirming a high-risk action"
```

Expected: fail because `./components/ActionCompareModal` does not exist.

- [ ] **Step 3: Implement the comparison modal**

Create `frontend/src/components/ActionCompareModal.tsx` with:

```tsx
import { Alert, Button, Modal, Space, Typography } from 'antd'
import { AlertTriangle, CheckCircle2, ShieldAlert } from 'lucide-react'
import type { ReactNode } from 'react'

export interface ActionCompareItem {
  label: string
  value: ReactNode
  hint?: ReactNode
}

interface ActionCompareModalProps {
  open: boolean
  title: string
  tone?: 'default' | 'warning' | 'danger'
  impact: ReactNode
  expected?: ReactNode
  current: ActionCompareItem[]
  proposed: ActionCompareItem[]
  confirmText: string
  confirmLoading?: boolean
  onConfirm: () => void
  onCancel: () => void
}

function toneIcon(tone: ActionCompareModalProps['tone']) {
  if (tone === 'danger') return <ShieldAlert size={18} />
  if (tone === 'warning') return <AlertTriangle size={18} />
  return <CheckCircle2 size={18} />
}

function toneType(tone: ActionCompareModalProps['tone']) {
  if (tone === 'danger') return 'error'
  if (tone === 'warning') return 'warning'
  return 'info'
}

function CompareColumn({ title, items }: { title: string; items: ActionCompareItem[] }) {
  return (
    <div className="action-compare-column">
      <Typography.Text strong>{title}</Typography.Text>
      <div className="action-compare-list">
        {items.map((item) => (
          <span className="action-compare-row" key={item.label}>
            <small>{item.label}</small>
            <b>{item.value}</b>
            {item.hint && <em>{item.hint}</em>}
          </span>
        ))}
      </div>
    </div>
  )
}

export function ActionCompareModal({
  open,
  title,
  tone = 'default',
  impact,
  expected,
  current,
  proposed,
  confirmText,
  confirmLoading,
  onConfirm,
  onCancel,
}: ActionCompareModalProps) {
  return (
    <Modal
      title={
        <Space size={8}>
          {toneIcon(tone)}
          <span>{title}</span>
        </Space>
      }
      open={open}
      onCancel={onCancel}
      footer={[
        <Button key="cancel" onClick={onCancel}>
          取消
        </Button>,
        <Button key="confirm" danger={tone === 'danger'} loading={confirmLoading} type="primary" onClick={onConfirm}>
          {confirmText}
        </Button>,
      ]}
      width={620}
    >
      <Alert className="action-compare-impact" type={toneType(tone)} message={impact} showIcon={false} />
      <div className="action-compare-grid">
        <CompareColumn title="当前" items={current} />
        <CompareColumn title="将应用" items={proposed} />
      </div>
      {expected && (
        <Typography.Paragraph className="action-compare-expected" type="secondary">
          {expected}
        </Typography.Paragraph>
      )}
    </Modal>
  )
}
```

- [ ] **Step 4: Run the modal test to verify it passes**

Run:

```powershell
npm test -- src/App.test.tsx -t "renders a before and after comparison before confirming a high-risk action"
```

Expected: pass.

## Task 2: Wire Comparison Modal Into Settings Actions

**Files:**
- Modify: `frontend/src/views/SettingsView.tsx`
- Modify: `frontend/src/App.test.tsx`

- [ ] **Step 1: Write failing tests for settings comparison flows**

Add these tests to `frontend/src/App.test.tsx` after the existing snapshot and origin tests:

```tsx
it('shows before and after values before applying camera parameters', async () => {
  window.history.pushState({}, '', '/settings#camera-global')
  render(<App />)
  const globalCameraCard = document.querySelector<HTMLElement>('#camera-global')
  expect(globalCameraCard).toBeTruthy()

  fireEvent.click(within(globalCameraCard!).getByRole('button', { name: '应用参数' }))

  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText('应用全局相机参数')).toBeInTheDocument()
  expect(within(dialog).getByText('当前')).toBeInTheDocument()
  expect(within(dialog).getByText('将应用')).toBeInTheDocument()
  expect(within(dialog).getAllByText('Exposure').length).toBeGreaterThan(0)
  expect(within(dialog).getAllByText('Gain').length).toBeGreaterThan(0)
  expect(within(dialog).getByRole('button', { name: '确认应用' })).toBeInTheDocument()
})

it('shows before and after state before clearing a motion origin', async () => {
  window.history.pushState({}, '', '/settings#motion-left')
  render(<App />)
  const leftCard = document.querySelector<HTMLElement>('#motion-left')
  expect(leftCard).toBeTruthy()

  fireEvent.click(within(leftCard!).getByText('设为采集零点').closest('button')!)
  await waitFor(() => expect(within(leftCard!).getByText('已设置')).toBeInTheDocument())

  fireEvent.click(within(leftCard!).getByText('清除零点').closest('button')!)

  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText('清除左臂采集零点')).toBeInTheDocument()
  expect(within(dialog).getByText('当前')).toBeInTheDocument()
  expect(within(dialog).getByText('将应用')).toBeInTheDocument()
  expect(within(dialog).getByRole('button', { name: '确认清除' })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```powershell
npm test -- src/App.test.tsx -t "shows before and after"
```

Expected: fail because settings actions execute immediately and do not render comparison dialogs.

- [ ] **Step 3: Import the modal and types in settings**

Add this import to `frontend/src/views/SettingsView.tsx`:

```ts
import { ActionCompareModal, type ActionCompareItem } from '../components/ActionCompareModal'
```

- [ ] **Step 4: Add local comparison state helpers to SettingsView**

Add this type near the existing helper functions:

```ts
interface PendingComparison {
  title: string
  tone?: 'default' | 'warning' | 'danger'
  impact: ReactNode
  expected?: ReactNode
  current: ActionCompareItem[]
  proposed: ActionCompareItem[]
  confirmText: string
  onConfirm: () => void | Promise<void>
}
```

Inside `SettingsView`, add state and runner:

```tsx
const [pendingComparison, setPendingComparison] = useState<PendingComparison | null>(null)
const [comparisonRunning, setComparisonRunning] = useState(false)

const confirmPendingComparison = async () => {
  if (!pendingComparison) return
  setComparisonRunning(true)
  try {
    await pendingComparison.onConfirm()
    setPendingComparison(null)
  } finally {
    setComparisonRunning(false)
  }
}
```

Render this modal before the closing `</div>` of `SettingsView`:

```tsx
<ActionCompareModal
  open={Boolean(pendingComparison)}
  title={pendingComparison?.title ?? ''}
  tone={pendingComparison?.tone}
  impact={pendingComparison?.impact ?? ''}
  expected={pendingComparison?.expected}
  current={pendingComparison?.current ?? []}
  proposed={pendingComparison?.proposed ?? []}
  confirmText={pendingComparison?.confirmText ?? '确认'}
  confirmLoading={comparisonRunning}
  onCancel={() => setPendingComparison(null)}
  onConfirm={() => void confirmPendingComparison()}
/>
```

- [ ] **Step 5: Pass `setPendingComparison` into Settings child cards**

Add a prop named `requestComparison: (comparison: PendingComparison) => void` to `MotionCard`, `CameraCard`, `GripperCard`, `ManualArmControl`, and `ManualGripperControl`.

Pass `requestComparison={setPendingComparison}` from `SettingsView` to all settings children that need it.

- [ ] **Step 6: Wrap camera apply**

In `CameraCard`, replace the `onClick={() => void handleApplyTuning()}` body with:

```tsx
onClick={() =>
  requestComparison({
    title: `应用${spec.label}参数`,
    tone: 'warning',
    impact: `将写入${spec.label}预览参数，并刷新当前预览流。`,
    expected: '确认后会调用现有相机参数接口，失败时继续写入日志面板。',
    current: [
      { label: '分辨率', value: previewResolution },
      { label: 'FPS', value: `${config.cameras.fps}` },
      { label: 'Exposure', value: `${tuning.exposure}` },
      { label: 'Gain', value: `${tuning.gain}` },
      { label: 'Auto exposure', value: tuning.autoExposure ? '开' : '关' },
      { label: 'Auto WB', value: tuning.autoWhiteBalance ? '开' : '关' },
    ],
    proposed: [
      { label: '分辨率', value: previewResolution },
      { label: 'FPS', value: `${config.cameras.fps}` },
      { label: 'Exposure', value: `${sanitizeTuning(tuning).exposure}` },
      { label: 'Gain', value: `${sanitizeTuning(tuning).gain}` },
      { label: 'Auto exposure', value: sanitizeTuning(tuning).autoExposure ? '开' : '关' },
      { label: 'Auto WB', value: sanitizeTuning(tuning).autoWhiteBalance ? '开' : '关' },
    ],
    confirmText: '确认应用',
    onConfirm: handleApplyTuning,
  })
}
```

- [ ] **Step 7: Wrap motion origin clear**

In `MotionCard`, replace `onClick={() => void handleClearOrigin()}` with:

```tsx
onClick={() =>
  requestComparison({
    title: `清除${sideSpec.shortLabel}采集零点`,
    tone: 'danger',
    impact: `将清除${sideSpec.shortLabel}采集零点，后续手动控制会显示 HAL 绝对位置。`,
    expected: '确认后只影响当前侧零点标记和脉冲缓存，不会移动硬件。',
    current: [
      { label: '当前状态', value: originStatusText },
      { label: '范围', value: originScopeText },
    ],
    proposed: [
      { label: '当前状态', value: '未设置' },
      { label: '范围', value: side === 'left' ? '左侧零点将清除' : '右侧零点将清除' },
    ],
    confirmText: '确认清除',
    onConfirm: handleClearOrigin,
  })
}
```

- [ ] **Step 8: Wrap motion origin capture**

In `MotionCard`, replace `onClick={() => void handleCaptureOrigin()}` with a comparison:

```tsx
onClick={() =>
  requestComparison({
    title: `设为${sideSpec.shortLabel}采集零点`,
    tone: 'warning',
    impact: `将把${sideSpec.shortLabel}当前位置保存为采集零点。`,
    expected: '确认后不会移动硬件，只保存当前位置作为后续相对显示基准。',
    current: [
      { label: '当前状态', value: originStatusText },
      { label: '当前位置', value: positions.slice(sideSpec.stateOffset, sideSpec.stateOffset + 6).map((value) => value.toFixed(1)).join(', ') },
    ],
    proposed: [
      { label: '当前状态', value: '已设置' },
      { label: '更新时间', value: '确认时写入' },
    ],
    confirmText: '确认设为零点',
    onConfirm: handleCaptureOrigin,
  })
}
```

- [ ] **Step 9: Wrap gripper target execution**

In `GripperCard` and `ManualGripperControl`, wrap `issueManualGripperMove(side, 'target', config.gripper[targetKey])` with:

```tsx
requestComparison({
  title: `${sideSpec.shortLabel}夹爪执行目标`,
  tone: 'warning',
  impact: `将向${sideSpec.shortLabel}夹爪下发目标开合命令。`,
  expected: '确认后仍由现有夹爪安全限制和命令力限制保护。',
  current: [
    { label: '当前开合', value: currentText },
    { label: '使能状态', value: gripperEnabled ? '已使能' : '未使能' },
  ],
  proposed: [
    { label: '目标开合', value: `${config.gripper[targetKey].toFixed(1)} mm` },
    { label: '命令力限制', value: `≤ ${config.gripper.commandForceLimitN.toFixed(1)} N` },
  ],
  confirmText: '确认执行',
  onConfirm: () => issueManualGripperMove(side, 'target', config.gripper[targetKey]),
})
```

- [ ] **Step 10: Wrap motion enable, disable, and home**

In `MotionCard`, replace the direct `handleEnable`, `handleDisable`, and `handleHome` button handlers with comparison requests.

For `使能全部`, use:

```tsx
onClick={() =>
  requestComparison({
    title: `${sideSpec.shortLabel}运动轴使能`,
    tone: 'warning',
    impact: `将向${sideSpec.shortLabel}运动控制卡发送全部轴使能请求。`,
    expected: '确认后会调用现有 HAL 使能接口，真实轴状态仍以后端遥测为准。',
    current: [
      { label: '当前状态', value: effectiveEnabled === true ? '已使能' : partialEnabled ? '部分使能' : effectiveEnabled === false ? '未使能' : '未知' },
      { label: '控制卡', value: `Card ${configCardNo}` },
    ],
    proposed: [
      { label: '请求状态', value: '全部轴使能' },
      { label: '影响范围', value: sideSpec.axisOrder.join(' / ') },
    ],
    confirmText: '确认使能',
    onConfirm: handleEnable,
  })
}
```

For `断使能`, use the same structure with `tone: 'danger'`, title `${sideSpec.shortLabel}运动轴断使能`, proposed request `全部轴断使能`, confirm text `确认断使能`, and `onConfirm: handleDisable`.

For `回零`, replace the existing `Modal.confirm` flow with:

```tsx
onClick={() =>
  requestComparison({
    title: `${sideSpec.shortLabel}回零`,
    tone: 'danger',
    impact: `将通过 HAL 调用 LTDMC dmc_home_move，执行前请确认工作区安全。`,
    expected: '确认后硬件会开始回零动作；急停按钮保持可用。',
    current: [
      { label: '控制卡', value: `Card ${configCardNo}` },
      { label: '零点状态', value: originStatusText },
    ],
    proposed: [
      { label: '动作', value: 'dmc_home_move' },
      { label: '影响轴', value: sideSpec.axisOrder.join(' / ') },
    ],
    confirmText: '确认回零',
    onConfirm: handleHome,
  })
}
```

Adjust `handleHome` so it performs the async home command directly and no longer opens its own `Modal.confirm`.

- [ ] **Step 11: Run settings tests**

Run:

```powershell
npm test -- src/App.test.tsx -t "settings|before and after|motion origin"
```

Expected: all selected tests pass.

## Task 3: Clarify Model Labels And Add Model Comparison

**Files:**
- Modify: `frontend/src/views/ModelView.tsx`
- Modify: `frontend/src/views/SettingsView.tsx`
- Modify: `frontend/src/App.test.tsx`

- [ ] **Step 1: Write failing tests for scoped labels**

Add these tests to `frontend/src/App.test.tsx`:

```tsx
it('uses scoped model start and stop action labels', () => {
  render(<App />)
  fireEvent.click(screen.getByRole('link', { name: '模型' }))
  expect(screen.getByRole('button', { name: '启动 ACT' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '停止 ACT' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Start' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument()
})

it('shows before and after state before starting a model', async () => {
  render(<App />)
  fireEvent.click(screen.getByRole('link', { name: '模型' }))
  fireEvent.click(screen.getByRole('button', { name: '启动 ACT' }))

  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByText('启动 ACT')).toBeInTheDocument()
  expect(within(dialog).getByText('当前')).toBeInTheDocument()
  expect(within(dialog).getByText('将应用')).toBeInTheDocument()
  expect(within(dialog).getByRole('button', { name: '确认启动' })).toBeInTheDocument()
})

it('gives manual memory delete buttons accessible names', () => {
  window.history.pushState({}, '', '/settings#manual')
  useTelemetryStore.setState((state) => ({
    manualControl: {
      ...state.manualControl,
      memories: [
        { id: 77, name: '左臂测试动作', actions: [], durationMs: 1200, createdAt: Date.now() },
      ],
    },
  }))
  render(<App />)
  expect(screen.getByRole('button', { name: '删除动作记忆 左臂测试动作' })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
npm test -- src/App.test.tsx -t "scoped model|manual memory delete"
```

Expected: fail because the model buttons are still `Start`/`Stop`, the model start action does not open a comparison modal, and the delete icon lacks the scoped accessible name.

- [ ] **Step 3: Add model comparison state and modal**

In `frontend/src/views/ModelView.tsx`, add:

```ts
import { ActionCompareModal, type ActionCompareItem } from '../components/ActionCompareModal'
```

Add this type near `fallbackModels`:

```ts
interface PendingModelAction {
  title: string
  tone?: 'default' | 'warning' | 'danger'
  impact: string
  current: ActionCompareItem[]
  proposed: ActionCompareItem[]
  confirmText: string
  onConfirm: () => void
}
```

Inside `ModelView`, add:

```tsx
const [pendingModelAction, setPendingModelAction] = useState<PendingModelAction | null>(null)
```

Before the closing wrapper `</div>`, render:

```tsx
<ActionCompareModal
  open={Boolean(pendingModelAction)}
  title={pendingModelAction?.title ?? ''}
  tone={pendingModelAction?.tone}
  impact={pendingModelAction?.impact ?? ''}
  current={pendingModelAction?.current ?? []}
  proposed={pendingModelAction?.proposed ?? []}
  confirmText={pendingModelAction?.confirmText ?? '确认'}
  confirmLoading={loading}
  onCancel={() => setPendingModelAction(null)}
  onConfirm={() => {
    pendingModelAction?.onConfirm()
    setPendingModelAction(null)
  }}
/>
```

- [ ] **Step 4: Update model action labels and wrap actions**

In `frontend/src/views/ModelView.tsx`, change:

```tsx
<Button size="small" onClick={() => startModel(model.id)}>Start</Button>
<Button size="small" onClick={() => stopModel(model.id)}>Stop</Button>
```

to:

```tsx
<Button size="small" onClick={() => startModel(model.id)}>启动 {model.name}</Button>
<Button size="small" onClick={() => stopModel(model.id)}>停止 {model.name}</Button>
```

Then replace each direct `onClick` with comparison setup:

```tsx
onClick={() =>
  setPendingModelAction({
    title: `启动 ${model.name}`,
    tone: 'warning',
    impact: `将把策略服务切换到 ${model.name}。`,
    current: [
      { label: '当前 active', value: activeModelId || '未启动' },
      { label: '当前状态', value: model.status },
    ],
    proposed: [
      { label: '请求 active', value: model.id },
      { label: '预估延迟', value: `${model.latencyMs || 0}ms` },
    ],
    confirmText: '确认启动',
    onConfirm: () => startModel(model.id),
  })
}
```

For stop, use title `停止 ${model.name}`, `tone: 'danger'`, impact `将停止 ${model.name} 策略服务。`, proposed request `停止服务`, confirm text `确认停止`, and `onConfirm: () => stopModel(model.id)`.

- [ ] **Step 5: Add accessible delete label**

In `ManualMemoryRow`, change:

```tsx
<Button size="small" icon={<Trash2 size={14} />} onClick={() => deleteManualMemory(memory.id)} />
```

to:

```tsx
<Button
  aria-label={`删除动作记忆 ${memory.name}`}
  size="small"
  icon={<Trash2 size={14} />}
  onClick={() => deleteManualMemory(memory.id)}
/>
```

- [ ] **Step 6: Run scoped label tests**

Run:

```powershell
npm test -- src/App.test.tsx -t "scoped model|manual memory delete"
```

Expected: pass.

## Task 4: Refine Layout, Palette, And Floating Emergency Spacing

**Files:**
- Modify: `frontend/src/components/GlobalEmergencyStopButton.tsx`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Write failing DOM-level assertions for emergency spacing hooks**

Add this test to `frontend/src/App.test.tsx`:

```tsx
it('marks the floating emergency stack as a reserved overlay for layout spacing', () => {
  render(<App />)
  const emergencyButton = screen.getByRole('button', { name: '全局急停 F12' })
  const stack = emergencyButton.closest('.floating-emergency-stack')
  expect(stack).toHaveClass('floating-emergency-stack-reserved')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
npm test -- src/App.test.tsx -t "reserved overlay"
```

Expected: fail because the class does not exist yet.

- [ ] **Step 3: Add the emergency layout class hook**

In `frontend/src/components/GlobalEmergencyStopButton.tsx`, change:

```tsx
<div className={`floating-emergency-stack ${active ? 'floating-emergency-stack-active' : ''}`}>
```

to:

```tsx
<div className={`floating-emergency-stack floating-emergency-stack-reserved ${active ? 'floating-emergency-stack-active' : ''}`}>
```

- [ ] **Step 4: Add CSS refinements**

Append these rules near the existing settings and floating emergency CSS in `frontend/src/index.css`:

```css
.main-content {
  padding-right: 154px;
  padding-bottom: 94px;
}

.hardware-config-actions {
  justify-content: flex-end;
  padding: 8px 0 2px;
  border-top: 1px solid #edf1f6;
}

.hardware-config-actions .ant-btn {
  min-width: 92px;
}

.action-compare-impact {
  margin-bottom: 12px;
}

.action-compare-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.action-compare-column {
  min-width: 0;
  padding: 10px;
  border: 1px solid #d7dde7;
  border-radius: 8px;
  background: #f8fafc;
}

.action-compare-list {
  display: flex;
  flex-direction: column;
  gap: 7px;
  margin-top: 8px;
}

.action-compare-row {
  display: grid;
  grid-template-columns: minmax(96px, 0.8fr) minmax(0, 1fr);
  gap: 8px;
  align-items: baseline;
  min-width: 0;
}

.action-compare-row small,
.action-compare-row em {
  color: #667085;
}

.action-compare-row b {
  min-width: 0;
  overflow: hidden;
  color: #17324d;
  text-overflow: ellipsis;
}

.action-compare-expected {
  margin: 12px 0 0;
}

.manual-action-row {
  align-items: center;
  padding-top: 8px;
  border-top: 1px solid #e5e9f0;
}

.floating-settings {
  display: none;
}

@media (max-width: 1200px) {
  .main-content {
    padding-right: 12px;
  }
}

@media (max-width: 760px) {
  .main-content {
    padding-right: 10px;
    padding-bottom: 122px;
  }

  .action-compare-grid {
    grid-template-columns: 1fr;
  }

  .floating-emergency-stack {
    right: 10px;
    bottom: 72px;
  }
}
```

If a duplicate selector already exists later in the file, merge these declarations into the existing selector instead of leaving conflicting definitions.

- [ ] **Step 5: Run layout hook test**

Run:

```powershell
npm test -- src/App.test.tsx -t "reserved overlay"
```

Expected: pass.

## Task 5: Full Verification And Browser Audit

**Files:**
- No source files unless verification reveals failures.
- Create/update audit artifacts under `frontend/output/ui-audit-after/`.

- [ ] **Step 1: Run focused frontend tests**

Run:

```powershell
npm test -- src/App.test.tsx
```

Expected: all App tests pass.

- [ ] **Step 2: Run typecheck**

Run:

```powershell
npm run typecheck
```

Expected: exit code 0.

- [ ] **Step 3: Run build**

Run:

```powershell
npm run build
```

Expected: exit code 0.

- [ ] **Step 4: Generate after screenshots and button audit**

Run a Playwright script equivalent to the baseline audit, saving screenshots and JSON under:

```text
frontend/output/ui-audit-after/
```

It must cover these routes at `1440x900` and `390x844`:

```text
/
/record
/dataset
/model
/fine-tune
/auto
/settings
/settings#manual
```

Expected: screenshots are produced and no route crashes.

- [ ] **Step 5: Compare audit results**

Inspect `frontend/output/ui-audit-after/button-audit.json` and confirm:

- Critical buttons still exist on all pages.
- The floating emergency button is visible.
- The settings page renders the comparison modal for camera apply and origin clear flows.
- Ambiguous model `Start`/`Stop` labels are gone.
- Manual memory delete buttons have accessible names.

- [ ] **Step 6: Review git diff**

Run:

```powershell
git diff -- frontend/src/components/ActionCompareModal.tsx frontend/src/App.test.tsx frontend/src/views/SettingsView.tsx frontend/src/views/ModelView.tsx frontend/src/components/GlobalEmergencyStopButton.tsx frontend/src/index.css
```

Expected: diff contains only frontend UI/action-comparison changes aligned with the design spec.
