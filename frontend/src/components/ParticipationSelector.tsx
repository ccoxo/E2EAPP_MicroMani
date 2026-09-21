import type { Participation } from '../types'

export function ParticipationSelector({ value, onChange, disabled = false }: {
  value: Participation; onChange: (value: Participation) => void; disabled?: boolean
}) {
  return <fieldset className="participation-selector" disabled={disabled}>
    <legend>参与设备 · 操作者视角</legend>
    {(['left', 'right'] as const).map((side) => <div key={side}>
      <label><input type="checkbox" checked={value.arms.includes(side)} onChange={(event) => onChange({
        ...value, arms: event.target.checked ? [...value.arms, side] : value.arms.filter((s) => s !== side),
        grippers: event.target.checked ? value.grippers : value.grippers.filter((s) => s !== side),
      })} />{side === 'left' ? '左臂' : '右臂'}</label>
      <label><input type="checkbox" aria-label={side === 'left' ? '左夹爪参与' : '右夹爪参与'} disabled={!value.arms.includes(side)} checked={value.grippers.includes(side)} onChange={(event) => onChange({
        ...value, grippers: event.target.checked ? [...value.grippers, side] : value.grippers.filter((s) => s !== side),
      })} />夹爪</label>
    </div>)}
  </fieldset>
}
