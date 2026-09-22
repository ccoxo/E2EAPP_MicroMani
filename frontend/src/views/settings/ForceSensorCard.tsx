/*
 * 六维力传感器设置卡；从 SettingsView 按域拆出。
 * 先看：ForceSensorCard。
 */
import { Crosshair, Download, RotateCcw, Waves } from 'lucide-react'
import { useState } from 'react'
import { mockMode, tareForceSensor } from '../../api'
import { ForceChart } from '../../components/Charts'
import {
  UiButton,
  UiField,
  UiInput,
  UiNumber,
  UiSelect,
  UiSpace,
  UiSwitch,
  UiTag,
  UiText,
} from '../../components/ui'
import {
  armHardwareSpecs,
  forceChannels,
  hardwareSideForOperatorSide,
  nano17Spec,
  operatorSideLabel,
  semanticAxes,
  type RobotSide,
} from '../../data'
import { useTelemetryStore } from '../../stores/telemetry'
import { controlSafetyBlockReason } from '../../utils/controlSafety'
import type {
  AppConfig,
  LogEntry,
  TelemetryFrame,
} from '../../types'
import { HardwareConfigCard, MetricBox, commandLog } from './shared'

const forceAxisCalibrationAxes = semanticAxes.map((axis, index) => ({ axis, channel: forceChannels[index], index }))
const forceAxisCalibrationGroups = [
  { title: '平移力', unit: 'N', axes: forceAxisCalibrationAxes.slice(0, 3) },
  { title: '旋转力矩', unit: 'N·m', axes: forceAxisCalibrationAxes.slice(3) },
]
const fallbackForceAxisSigns: Record<RobotSide, number[]> = {
  left: [1, 1, -1, -1, -1, 1],
  right: [1, -1, 1, -1, 1, -1],
}

function formatForceValue(value: number, index: number) {
  return index < 3 ? `${(value * 1000).toFixed(0)} mN` : `${(value * 1000).toFixed(1)} mN·m`
}

/** 格式化对应数值用于界面展示。 */
function forceState(values: number[], config: AppConfig) {
  const danger = Math.max(
    Math.abs(values[0]) / config.safety.fxyStopN,
    Math.abs(values[1]) / config.safety.fxyStopN,
    Math.abs(values[2]) / config.safety.fzStopN,
    Math.abs(values[3]) / config.safety.momentStopNm,
    Math.abs(values[4]) / config.safety.momentStopNm,
    Math.abs(values[5]) / config.safety.momentStopNm,
  )
  if (danger >= 1) return 'error'
  if (danger >= 0.65) return 'warn'
  return 'ok'
}
/** Send a command event into the shared UI log stream. */

export function ForceSensorCard({
  side,
  config,
  updateConfig,
  focusHash,
  values,
  forceStatus,
  injectLog,
}: {
  side: RobotSide
  config: AppConfig
  updateConfig: (patch: Partial<AppConfig>) => void
  focusHash: string
  values: number[]
  forceStatus: TelemetryFrame['forceStatus']
  injectLog: (level: 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR', msg: string, channel?: LogEntry['channel']) => void
}) {
  const history = useTelemetryStore((state) => state.history)
  const tareBlockedReason = useTelemetryStore((state) => controlSafetyBlockReason(state, !mockMode))
  const hardwareSide = hardwareSideForOperatorSide(side)
  const sideSpec = armHardwareSpecs[hardwareSide]
  const operatorLabel = operatorSideLabel(side)
  const ipKey = sideSpec.forceIpKey
  const isHkvl = config.force.source === 'hkvl_serial'
  const sideStatus = forceStatus?.sides?.[hardwareSide]
  const calibration = forceStatus?.calibration?.sides?.[hardwareSide]
  const state = isHkvl && !sideStatus?.healthy ? 'error' : forceState(values, config)
  const id = `force-${side}`
  const serialPortKey = hardwareSide === 'left' ? 'leftPort' : 'rightPort'
  const compliance = config.force.compliance[hardwareSide]
  const mappingsConfirmed =
    config.force.compliance.left.mappingConfirmed
    && config.force.compliance.right.mappingConfirmed
  const axisSignConfig = config.force.axisSign ?? fallbackForceAxisSigns
  const axisSigns = axisSignConfig[hardwareSide] ?? fallbackForceAxisSigns[hardwareSide]
  const [axisCalibrationOpen, setAxisCalibrationOpen] = useState(false)
  const [axisSignDraft, setAxisSignDraft] = useState<number[]>(axisSigns)
  const updateCompliance = (nextSide: typeof compliance) => {
    updateConfig({
      force: {
        ...config.force,
        compliance: {
          ...config.force.compliance,
          [hardwareSide]: nextSide,
        },
      },
    })
  }
  const updateComplianceArray = (
    key: 'matrix' | 'deadbandN' | 'gainUmPerNs' | 'maxStepUm' | 'maxOffsetUm',
    index: number,
    value: number | null,
  ) => {
    const next = [...compliance[key]]
    next[index] = Number(value ?? 0)
    updateCompliance({ ...compliance, [key]: next })
  }
  const openAxisCalibration = () => {
    setAxisSignDraft([...axisSigns])
    setAxisCalibrationOpen(true)
  }
  const saveAxisCalibration = () => {
    updateConfig({
      force: {
        ...config.force,
        axisSign: {
          ...axisSignConfig,
          [hardwareSide]: axisSignDraft,
        },
        compliance: {
          ...config.force.compliance,
          [hardwareSide]: {
            ...compliance,
            mappingConfirmed: true,
          },
        },
      },
    })
    commandLog(injectLog, '[FORCE]', `${operatorLabel} 六轴方向标定已保存；请释放载荷后重新 Tare`)
    setAxisCalibrationOpen(false)
  }
  const updateAxisSignDraft = (index: number, value: number) => {
    setAxisSignDraft((current) => current.map((sign, signIndex) => signIndex === index ? value : sign))
  }
  return (
    <HardwareConfigCard
      id={id}
      focusHash={focusHash}
      icon={<Waves size={20} />}
      title={`${operatorLabel} ${isHkvl ? 'HKVL-36A' : 'Nano-17'} 六维力`}
      subtitle={isHkvl ? 'HAL 原生串口 · N / Nm · 只读主动帧' : `${nano17Spec.model} · Fx/Fy/Fz=mN · Mx/My/Mz=mN·m`}
      state={state}
      actions={
        <UiSpace wrap>
            {!isHkvl && <UiButton
              icon={<RotateCcw size={15} />}
              disabled={Boolean(tareBlockedReason)}
              title={tareBlockedReason ?? undefined}
              onClick={() => {
                void tareForceSensor(hardwareSide).then(() => {
                  commandLog(injectLog, '[FORCE]', `${operatorLabel} ${isHkvl ? 'HKVL-36A' : 'Nano-17'} Tare 请求已接受`)
                }).catch((error) => {
                  injectLog('ERROR', `Tare 失败：${String(error)}`, '[FORCE]')
                })
            }}
          >
            Tare
          </UiButton>}
          <UiButton icon={<Download size={15} />} onClick={() => commandLog(injectLog, '[FORCE]', `${operatorLabel} 力数据导出`)}>
            CSV
          </UiButton>
        </UiSpace>
      }
      wide
    >
      <div className={`force-settings-layout${isHkvl ? ' force-settings-layout-hkvl' : ''}`}>
        <div className="force-visual-area">
          <ForceChart history={history} side={hardwareSide} height={170} />
          <div className="force-current-grid force-current-grid-settings">
            {forceChannels.map((channel, index) => (
              <span key={channel}>
                <b>{channel}</b>
                {formatForceValue(values[index] ?? 0, index)}
              </span>
            ))}
          </div>
        </div>
        <div className="force-settings-column">
          <div className="hardware-form-grid hardware-form-grid-compact ui-form">
            <UiField label="数据源">
              <UiSelect                 value={config.force.source}
                options={[
                  { value: 'hkvl_serial', label: 'HKVL-36A / HAL 串口（主用）' },
                  { value: 'nidaq', label: 'ATI Nano-17 / NI-DAQ（备用）' },
                ]}
                onChange={(source) => updateConfig({ force: {
                  ...config.force,
                  source,
                  tareSamples: source === 'hkvl_serial' && (
                    !Number.isInteger(config.force.tareSamples)
                    || config.force.tareSamples < 200 || config.force.tareSamples > 1000
                  ) ? 0 : config.force.tareSamples,
                } })}
              />
            </UiField>
            {isHkvl ? (
              <>
                <UiField label="串口">
                  <UiInput                     value={config.force.serial[serialPortKey]}
                    onChange={(event) => updateConfig({
                      force: {
                        ...config.force,
                        serial: { ...config.force.serial, [serialPortKey]: event.target.value },
                      },
                    })}
                  />
                </UiField>
                <UiField label="协议"><UiInput disabled value={config.force.serial.protocol} /></UiField>
                <UiField label="波特率"><UiNumber disabled value={config.force.serial.baudrate} /></UiField>
                <UiField label="测量采样率 Hz"><UiNumber disabled value={config.force.serial.expectedSampleHz} /></UiField>
              </>
            ) : (
              <>
                <UiField label="DAQ 通道">
                  <UiInput value={config.force[ipKey]} onChange={(event) => updateConfig({ force: { ...config.force, [ipKey]: event.target.value } })} />
                </UiField>
                <UiField label="采样率 Hz"><UiNumber min={1} value={config.force.sampleHz} onChange={(value) => updateConfig({ force: { ...config.force, sampleHz: Number(value ?? 200) } })} /></UiField>
                <UiField label="录制窗口样本"><UiNumber min={0} max={512} value={config.force.recordWindowSamples} onChange={(value) => updateConfig({ force: { ...config.force, recordWindowSamples: Number(value ?? 0) } })} /></UiField>
              </>
            )}
            <UiField label={isHkvl ? 'Tare 样本（200–1000）' : 'Tare 样本'}>
              <UiNumber min={isHkvl ? 200 : 0} max={isHkvl ? 1000 : 512} step={1}
                value={isHkvl && config.force.tareSamples === 0 ? 200 : config.force.tareSamples}
                onChange={(value) => updateConfig({ force: { ...config.force, tareSamples: Number(value ?? 0) } })} />
            </UiField>
            <UiField label="低通滤波">
              <UiSwitch
                checked={config.force.lowpassEnabled}
                checkedChildren="ON"
                unCheckedChildren="OFF"
                onChange={(checked) => updateConfig({ force: { ...config.force, lowpassEnabled: checked } })}
              />
            </UiField>
            <UiField label="低通截止 Hz"><UiNumber min={0} value={config.force.lowpassCutoffHz} onChange={(value) => updateConfig({ force: { ...config.force, lowpassCutoffHz: Number(value ?? 10) } })} /></UiField>
            {!isHkvl && <UiField label="标定证书">
              <UiSwitch
                checked={config.force.certificateConfirmed}
                checkedChildren="已确认"
                unCheckedChildren="待确认"
                onChange={(checked) => updateConfig({ force: { ...config.force, certificateConfirmed: checked } })}
              />
            </UiField>}
          </div>
          <div className="hardware-metric-grid">
            {isHkvl ? (
              <>
                <MetricBox label="端口" value={sideStatus?.port || config.force.serial[serialPortKey]} hint={`${config.force.serial.baudrate.toLocaleString()} bps · 8N1`} tone={sideStatus?.connected ? 'ok' : 'warn'} />
                <MetricBox label="采样率" value={`${Number(sideStatus?.sampleHz ?? 0).toFixed(1)} Hz`} hint={`样本年龄 ${Number(sideStatus?.sampleAgeMs ?? 0).toFixed(1)} ms`} tone={sideStatus?.healthy ? 'ok' : 'warn'} />
                <MetricBox label="帧校验" value={`CRC ${sideStatus?.crcErrors ?? 0}`} hint={`resync ${sideStatus?.resyncBytes ?? 0}`} tone={(sideStatus?.crcErrors ?? 0) > 0 ? 'warn' : 'ok'} />
                <MetricBox label="连接" value={sideStatus?.healthy ? '健康' : '异常'} hint={sideStatus?.error || `左右偏差 ${Number(forceStatus?.leftRightSkewMs ?? 0).toFixed(1)} ms`} tone={sideStatus?.healthy ? 'ok' : 'warn'} />
              </>
            ) : (
              <>
                <MetricBox label="Fx/Fy 量程" value={nano17Spec.range.fxy} />
                <MetricBox label="Fz 量程" value={nano17Spec.range.fz} />
                <MetricBox label="Moment 量程" value={nano17Spec.range.moment} />
                <MetricBox label="NI-DAQmx" value="DIFF ai0:5" hint={`${nano17Spec.fastDaqHz}Hz`} />
              </>
            )}
          </div>
          {isHkvl && (
            <>
              <details className="gripper-config-section">
                <summary>{operatorLabel}去皮统计（Fx/Fy/Fz：N，Mx/My/Mz：N·m）</summary>
                <div style={{ overflowX: 'auto' }}>
                <table className="ui-table" aria-label={`${operatorLabel}去皮统计`}>
                  <thead><tr><th>统计项</th>{forceChannels.map((channel) => <th key={channel}>{channel}</th>)}</tr></thead>
                  <tbody>
                {([
                  ['零点', calibration?.bias], ['去皮前均值', calibration?.preMean],
                  ['去皮前标准差', calibration?.preStdDev], ['去皮前峰峰值', calibration?.prePeakToPeak],
                  ['残差均值', calibration?.residualMean], ['残差标准差', calibration?.residualStdDev],
                  ['残差峰峰值', calibration?.residualPeakToPeak],
                ] as const).map(([label, statistics]) => (
                  <tr key={label}><th scope="row">{label}</th>{forceChannels.map((channel, index) => (
                    <td key={channel}>{statistics?.[index]?.toFixed(5) ?? '—'}</td>
                  ))}</tr>
                ))}
                  </tbody>
                </table>
                </div>
              </details>
              <div className="force-control-workbench">
                <section className="force-control-panel force-coordinate-panel">
                  <div className="force-control-panel-head">
                    <div>
                      <b>六轴坐标方向</b>
                      <span>操作者视角 · 传感器原始坐标到滑轨坐标</span>
                    </div>
                    <UiTag tone={compliance.mappingConfirmed ? 'success' : 'warning'}>
                      {compliance.mappingConfirmed ? '已验证' : '待标定'}
                    </UiTag>
                  </div>
                  <UiButton
                    aria-label={`${operatorLabel}六轴方向标定`}
                    className="force-calibration-launch"
                    icon={<Crosshair size={15} />}
                    disabled={config.force.compliance.enabled}
                    onClick={openAxisCalibration}
                  >
                    六轴方向标定
                  </UiButton>
                  <div className="force-axis-sign-grid" aria-label={`${operatorLabel}已标定方向`}>
                    {forceAxisCalibrationAxes.map(({ axis, channel, index }) => (
                      <span className="force-axis-sign" key={axis}>
                        <small>{channel}</small>
                        <b>{`${axis}${axisSigns[index] < 0 ? '−' : '+'}`}</b>
                      </span>
                    ))}
                  </div>
                  <UiText secondary className="force-control-note">
                    {config.force.compliance.enabled ? '请先关闭位置导纳，才能修改方向标定。' : '保存后会重新加载传感器，随后需在无外力时 Tare。'}
                  </UiText>
                </section>

                <section className="force-control-panel force-compliance-panel">
                  <div className="force-control-panel-head">
                    <div>
                      <b>X/Z 顺应</b>
                      <span>仅作用于新的 Omega.7 原生遥操作目标</span>
                    </div>
                    <UiSwitch
                      aria-label="启用 X/Z 顺应"
                      checked={config.force.compliance.enabled}
                      disabled={!mappingsConfirmed && !config.force.compliance.enabled}
                      checkedChildren="已启用"
                      unCheckedChildren={mappingsConfirmed ? '已关闭' : '等待标定'}
                      onChange={(enabled) => updateConfig({
                        force: {
                          ...config.force,
                          compliance: { ...config.force.compliance, enabled },
                        },
                      })}
                    />
                  </div>
                  <div className="force-compliance-grid ui-form">
                    <UiField label="X/Z 映射矩阵">
                      <UiSpace wrap>
                        {compliance.matrix.map((value, index) => (
                          <UiNumber
                            aria-label={`${operatorLabel}映射矩阵 ${index + 1}`}
                            key={index}
                            step={0.1}
                            value={value}
                            onChange={(next) => updateComplianceArray('matrix', index, next)}
                          />
                        ))}
                      </UiSpace>
                    </UiField>
                    {([
                      ['deadbandN', 'X/Z 死区 N'],
                      ['gainUmPerNs', 'X/Z 增益 μm/(N·s)'],
                      ['maxStepUm', '单帧上限 μm'],
                      ['maxOffsetUm', '会话偏移上限 μm'],
                    ] as const).map(([key, label]) => (
                      <UiField label={label} key={key}>
                        <UiSpace wrap>
                          {compliance[key].map((value, index) => (
                            <UiNumber
                              aria-label={`${operatorLabel}${label} ${index === 0 ? 'X' : 'Z'}`}
                              key={index}
                              min={0}
                              value={value}
                              onChange={(next) => updateComplianceArray(key, index, next)}
                            />
                          ))}
                        </UiSpace>
                      </UiField>
                    ))}
                  </div>
                </section>
              </div>
              <UiText secondary className="force-control-footer-note">
                停止、急停或配置变化会清空累计偏移；方向标定只改变力坐标解释，不改变滑轨运动方向。
              </UiText>
              {axisCalibrationOpen && (
              <div className="ui-modal-mask" role="presentation" onClick={() => setAxisCalibrationOpen(false)}>
                <div className="ui-modal force-axis-calibration-modal" style={{ width: 'min(720px, calc(100vw - 32px))' }} role="dialog" aria-label={`${operatorLabel} 六轴方向标定`} onClick={(event) => event.stopPropagation()}>
                <header className="ui-modal-head"><strong>{`${operatorLabel} 六轴方向标定`}</strong></header>
                <div className="ui-modal-body">
                <div className="force-calibration-intro">
                  <div>
                    <span>操作者视角</span>
                    <b>{operatorLabel}</b>
                    <span>{sideStatus?.port || config.force.serial[serialPortKey]}</span>
                  </div>
                  <p>选择传感器原始轴相对滑轨运动坐标的方向。此操作不会改变滑轨运动方向。</p>
                </div>
                <div className="force-axis-calibration-groups">
                  {forceAxisCalibrationGroups.map((group) => (
                    <section className="force-axis-calibration-group" key={group.title}>
                      <div className="force-axis-calibration-group-head">
                        <b>{group.title}</b>
                        <span>{group.unit}</span>
                      </div>
                      <div className="force-axis-calibration-axis-grid">
                        {group.axes.map(({ axis, channel, index }) => (
                          <div className="force-axis-calibration-axis" key={axis}>
                            <div>
                              <b>{axis}</b>
                              <span>{channel}</span>
                            </div>
                            <div className="ui-segmented" role="radiogroup" aria-label={`${operatorLabel}${axis}方向`}>
                              <button
                                type="button"
                                role="radio"
                                aria-checked={(axisSignDraft[index] ?? 1) === 1}
                                className={(axisSignDraft[index] ?? 1) === 1 ? 'on' : ''}
                                onClick={() => updateAxisSignDraft(index, 1)}
                              >
                                同向 +
                              </button>
                              <button
                                type="button"
                                role="radio"
                                aria-checked={(axisSignDraft[index] ?? 1) === -1}
                                className={(axisSignDraft[index] ?? 1) === -1 ? 'on' : ''}
                                onClick={() => updateAxisSignDraft(index, -1)}
                              >
                                反向 −
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    </section>
                  ))}
                </div>
                <UiText secondary className="force-calibration-warning">
                  保存会重新加载力传感器。请释放外力、完成 Tare 后，再启用位置导纳。
                </UiText>
                </div>
                <div className="ui-modal-actions">
                  <UiButton onClick={() => setAxisCalibrationOpen(false)}>取消</UiButton>
                  <UiButton variant="primary" onClick={saveAxisCalibration}>保存标定</UiButton>
                </div>
                </div>
              </div>
              )}
            </>
          )}
        </div>
      </div>
    </HardwareConfigCard>
  )
}
