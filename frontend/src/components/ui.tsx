/*
 * 阅读导航 03｜通用组件
 * 职责：壳层与高频页使用的轻量控件，避免首屏依赖 antd。
 * 先看：UiButton → UiTag → UiSpace → UiText。
 */
import type { ButtonHTMLAttributes, CSSProperties, ReactNode } from 'react'
import { memo } from 'react'

type ButtonVariant = 'default' | 'primary' | 'danger' | 'text'

interface UiButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  danger?: boolean
  loading?: boolean
  icon?: ReactNode
  block?: boolean
}

/** 渲染当前界面单元，并连接所需数据。 */
export const UiButton = memo(function UiButton({
  variant = 'default',
  danger,
  loading,
  icon,
  block,
  className,
  children,
  disabled,
  ...rest
}: UiButtonProps) {
  const tone = danger ? 'danger' : variant
  return (
    <button
      type="button"
      className={['ui-btn', `ui-btn-${tone}`, block ? 'ui-btn-block' : '', className ?? ''].filter(Boolean).join(' ')}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <span className="ui-btn-spin" aria-hidden /> : icon}
      {children != null && children !== false ? <span>{children}</span> : null}
    </button>
  )
})

interface UiTagProps {
  children: ReactNode
  tone?: 'default' | 'success' | 'warning' | 'error' | 'processing' | 'muted'
  className?: string
  style?: CSSProperties
  title?: string
}

/** 渲染当前界面单元，并连接所需数据。 */
export const UiTag = memo(function UiTag({ children, tone = 'default', className, style, title }: UiTagProps) {
  return (
    <span className={['ui-tag', `ui-tag-${tone}`, className ?? ''].filter(Boolean).join(' ')} style={style} title={title}>
      {children}
    </span>
  )
})

/** 渲染当前界面单元，并连接所需数据。 */
export function UiSpace({
  children,
  size = 8,
  wrap,
  align = 'center',
  className,
  style,
}: {
  children: ReactNode
  size?: number
  wrap?: boolean
  align?: 'start' | 'center' | 'end'
  className?: string
  style?: CSSProperties
}) {
  return (
    <div
      className={['ui-space', className ?? ''].filter(Boolean).join(' ')}
      style={{
        gap: size,
        flexWrap: wrap ? 'wrap' : 'nowrap',
        alignItems: align === 'start' ? 'flex-start' : align === 'end' ? 'flex-end' : 'center',
        ...style,
      }}
    >
      {children}
    </div>
  )
}

/** 渲染当前界面单元，并连接所需数据。 */
export function UiText({
  children,
  strong,
  secondary,
  code,
  className,
  style,
}: {
  children: ReactNode
  strong?: boolean
  secondary?: boolean
  code?: boolean
  className?: string
  style?: CSSProperties
}) {
  const Tag = code ? 'code' : 'span'
  return (
    <Tag
      className={['ui-text', strong ? 'ui-text-strong' : '', secondary ? 'ui-text-secondary' : '', className ?? '']
        .filter(Boolean)
        .join(' ')}
      style={style}
    >
      {children}
    </Tag>
  )
}

/** 渲染当前界面单元，并连接所需数据。 */
export function UiTitle({
  children,
  level = 3,
  className,
}: {
  children: ReactNode
  level?: 2 | 3 | 4
  className?: string
}) {
  const Tag = (level === 2 ? 'h2' : level === 3 ? 'h3' : 'h4') as 'h2' | 'h3' | 'h4'
  return <Tag className={['ui-title', `ui-title-${level}`, className ?? ''].filter(Boolean).join(' ')}>{children}</Tag>
}

/** 渲染当前界面单元，并连接所需数据。 */
export function UiSpin({ tip }: { tip?: string }) {
  return (
    <div className="ui-spin-wrap" role="status" aria-live="polite">
      <span className="ui-spin" aria-hidden />
      {tip ? <span className="ui-spin-tip">{tip}</span> : null}
    </div>
  )
}

/** 渲染当前界面单元，并连接所需数据。 */
export function UiTooltip({ title, children }: { title?: ReactNode; children: ReactNode }) {
  if (!title) return <>{children}</>
  return (
    <span className="ui-tooltip" title={typeof title === 'string' ? title : undefined}>
      {children}
    </span>
  )
}

/** 渲染当前界面单元，并连接所需数据。 */
export function UiSegmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T | null
  options: { label: ReactNode; value: T }[]
  onChange: (value: T) => void
}) {
  return (
    <div className="ui-segmented" role="radiogroup">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          className={option.value === value ? 'on' : ''}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/** 渲染当前界面单元，并连接所需数据。 */
export function UiProgress({
  percent,
  status = 'active',
  format,
}: {
  percent: number
  status?: 'active' | 'success' | 'exception'
  format?: (percent: number) => ReactNode
}) {
  const value = Math.max(0, Math.min(100, percent))
  return (
    <div className="ui-progress">
      <div className="ui-progress-track">
        <div
          className={`ui-progress-fill ui-progress-${status}`}
          style={{ width: `${value}%` }}
        />
      </div>
      {format ? <span className="ui-progress-label">{format(value)}</span> : null}
    </div>
  )
}

/** 轻量卡片容器，替代 antd Card。 */
export function UiCard({
  title,
  extra,
  children,
  bodyStyle,
  className,
  style,
}: {
  title?: ReactNode
  extra?: ReactNode
  children: ReactNode
  bodyStyle?: CSSProperties
  className?: string
  style?: CSSProperties
}) {
  return (
    <section className={['ui-card', className ?? ''].filter(Boolean).join(' ')} style={style}>
      {title != null || extra != null ? (
        <header className="ui-card-head">
          <strong>{title}</strong>
          {extra}
        </header>
      ) : null}
      <div className="ui-card-body" style={bodyStyle}>
        {children}
      </div>
    </section>
  )
}

/** 表单字段：替代 antd Form.Item。 */
export function UiField({
  label,
  tooltip,
  children,
  className,
  style,
}: {
  label: ReactNode
  tooltip?: ReactNode
  children: ReactNode
  className?: string
  style?: CSSProperties
}) {
  return (
    <label className={['ui-field', className ?? ''].filter(Boolean).join(' ')} style={style} title={typeof tooltip === 'string' ? tooltip : undefined}>
      <span className="ui-field-label">{label}</span>
      {children}
    </label>
  )
}

/** 数值输入：API 接近 antd InputNumber，onChange 收 number | null。 */
export function UiNumber({
  value,
  onChange,
  min,
  max,
  step,
  disabled,
  style,
  className,
  'aria-label': ariaLabel,
}: {
  value?: number | null
  onChange?: (value: number | null) => void
  min?: number
  max?: number
  step?: number
  disabled?: boolean
  style?: CSSProperties
  className?: string
  'aria-label'?: string
}) {
  return (
    <input
      type="number"
      className={['ui-input', className ?? ''].filter(Boolean).join(' ')}
      style={style}
      aria-label={ariaLabel}
      value={value == null || Number.isNaN(value) ? '' : String(value)}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      onChange={(event) => {
        const raw = event.target.value
        if (raw === '') {
          onChange?.(null)
          return
        }
        const next = Number(raw)
        onChange?.(Number.isFinite(next) ? next : null)
      }}
    />
  )
}

/** 文本输入，API 接近 antd Input。 */
export function UiInput({
  value,
  onChange,
  placeholder,
  disabled,
  style,
  className,
  'aria-label': ariaLabel,
  type,
}: {
  value?: string
  onChange?: (event: { target: { value: string } }) => void
  placeholder?: string
  disabled?: boolean
  style?: CSSProperties
  className?: string
  'aria-label'?: string
  type?: string
}) {
  return (
    <input
      type={type ?? 'text'}
      className={['ui-input', className ?? ''].filter(Boolean).join(' ')}
      style={style}
      aria-label={ariaLabel}
      value={value ?? ''}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(event) => onChange?.(event)}
    />
  )
}

/** 开关：API 接近 antd Switch。 */
export function UiSwitch({
  checked,
  onChange,
  disabled,
  checkedChildren,
  unCheckedChildren,
  'aria-label': ariaLabel,
  style,
}: {
  checked?: boolean
  onChange?: (checked: boolean) => void
  disabled?: boolean
  checkedChildren?: ReactNode
  unCheckedChildren?: ReactNode
  'aria-label'?: string
  style?: CSSProperties
}) {
  return (
    <span className="ui-switch-wrap" style={style}>
      <label className="ui-switch">
        <input
          type="checkbox"
          aria-label={ariaLabel}
          checked={Boolean(checked)}
          disabled={disabled}
          onChange={(event) => onChange?.(event.target.checked)}
        />
        <span />
      </label>
      <small>{checked ? checkedChildren : unCheckedChildren}</small>
    </span>
  )
}

/** 下拉选择：API 接近 antd Select。 */
export function UiSelect<T extends string | number = string>({
  value,
  options,
  onChange,
  disabled,
  style,
  className,
  'aria-label': ariaLabel,
}: {
  value?: T
  options: { value: T; label: ReactNode; disabled?: boolean }[]
  onChange?: (value: T) => void
  disabled?: boolean
  style?: CSSProperties
  className?: string
  'aria-label'?: string
}) {
  return (
    <select
      className={['ui-select', className ?? ''].filter(Boolean).join(' ')}
      style={style}
      aria-label={ariaLabel}
      value={value == null ? '' : String(value)}
      disabled={disabled}
      onChange={(event) => {
        const raw = event.target.value
        const match = options.find((item) => String(item.value) === raw)
        if (match) onChange?.(match.value)
      }}
    >
      {options.map((item) => (
        <option key={String(item.value)} value={String(item.value)} disabled={item.disabled}>
          {typeof item.label === 'string' || typeof item.label === 'number' ? item.label : String(item.value)}
        </option>
      ))}
    </select>
  )
}

/** 滑块：API 接近 antd Slider。 */
export function UiSlider({
  value,
  onChange,
  min = 0,
  max = 100,
  step,
  disabled,
  style,
  className,
}: {
  value?: number
  onChange?: (value: number) => void
  min?: number
  max?: number
  step?: number
  disabled?: boolean
  style?: CSSProperties
  className?: string
}) {
  return (
    <input
      type="range"
      className={['ui-range', className ?? ''].filter(Boolean).join(' ')}
      style={style}
      min={min}
      max={max}
      step={step}
      disabled={disabled}
      value={value ?? min}
      onChange={(event) => onChange?.(Number(event.target.value))}
    />
  )
}

/** 受控 Tabs：替代 antd Tabs；仅挂载 active 面板。 */
export function UiTabs({
  items,
  activeKey,
  onChange,
}: {
  items: { key: string; label: ReactNode }[]
  activeKey: string
  onChange: (key: string) => void
}) {
  return (
    <div className="ui-tabs">
      <div className="ui-tabs-nav" role="tablist">
        {items.map((item) => (
          <button
            key={item.key}
            type="button"
            role="tab"
            aria-selected={item.key === activeKey}
            className={item.key === activeKey ? 'on' : ''}
            onClick={() => onChange(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>
    </div>
  )
}
