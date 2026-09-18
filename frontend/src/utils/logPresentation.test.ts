import { describe, expect, it } from 'vitest'
import type { LogEntry } from '../types'
import { buildLogRows, deduplicateLogEntries, isDiagnosticLog } from './logPresentation'

function log(msg: string, patch: Partial<LogEntry> = {}): LogEntry {
  return { id: 1, ts: 1000, channel: '[HAL]', level: 'INFO', msg, ...patch }
}

describe('日志诊断分类', () => {
  it.each([
    'component=TELEOP event=teleop_status clip=- updateRet=[0] lastError="" blockReason=-',
    'event=teleop_axis_trace clipped=[X:0,Y:0,Z:0] updateRet=[X:0,Yaw:-0] stopReason=[0]',
    'teleop diag left->right axis=Yaw clip=- updateRet=[0]',
    'native status left->right ref/input raw=[Yaw:0.25]',
    'native status left->right ref/input last=left->right clip=[0] updateRet=[0] grip=left:OK target=0;right:IDLE target=0',
  ])('收起正常周期诊断：%s', (message) => {
    expect(isDiagnosticLog(log(message))).toBe(true)
  })

  it.each([
    'clipped=[X:0,Y:1,Z:0]',
    'updateRet=[X:0,Y:-1,Z:0]',
    'updateRet=[Roll:0.0001]',
    'updateRet=[unknown]',
    'stopReason=[X:0,Roll:7,Yaw:0]',
    'clip=Yaw',
    'clip=[X:0,Y:1,Z:0]',
    'lastError="HAL returned error 7"',
    'lastError="error \\"quoted\\" detail"',
    'blockReason="force_latched:confirm safety"',
    'block=disconnected',
    'block=disconnected block=-',
    'updateRet=[X:7] updateRet=[0]',
    'grip=left:ERR target=0;right:OK target=2',
  ])('INFO 中的异常仍保留：%s', (fields) => {
    expect(isDiagnosticLog(log(`event=teleop_axis_trace ${fields}`))).toBe(false)
  })

  it.each(['WARNING', 'ERROR'] as const)('从不收起 %s', (level) => {
    expect(isDiagnosticLog(log('event=teleop_status updateRet=[0]', { level }))).toBe(false)
    expect(isDiagnosticLog(log('Frame 100 OK, ws=30Hz', { channel: '[BACKEND]', level }))).toBe(false)
  })

  it.each(['teleop_mode', 'teleop_origin_transition', 'teleop_profile', 'axis_config_snapshot', 'manual_move'])('保留操作和配置事件 %s', (event) => {
    expect(isDiagnosticLog(log(`event=${event}`))).toBe(false)
  })

  it('识别 DEBUG 与测试帧心跳，不把包含事件名称的普通消息当作诊断', () => {
    expect(isDiagnosticLog(log('调试细节', { level: 'DEBUG' }))).toBe(true)
    expect(isDiagnosticLog(log('Frame 125 OK, ws=29.8Hz', { channel: '[BACKEND]' }))).toBe(true)
    expect(isDiagnosticLog(log('Frame 125 OK, ws=30Hz; warning', { channel: '[BACKEND]' }))).toBe(false)
    expect(isDiagnosticLog(log('message="event=teleop_status query selected"'))).toBe(false)
    expect(isDiagnosticLog(log('event=teleop_status', { channel: '[SAFETY]' }))).toBe(false)
  })
})

describe('日志重放去重', () => {
  it('只删除完整标识一致的重放，保留真实的新事件及本地/后端编号碰撞', () => {
    const first = log('已连接')
    const differentEntries = [
      log('已连接', { id: 2 }),
      log('已连接', { ts: 1001 }),
      log('已连接', { channel: '[BACKEND]' }),
      log('已连接', { level: 'WARNING' }),
      log('未连接'),
    ]
    expect(deduplicateLogEntries([first, ...differentEntries, { ...first }])).toEqual([first, ...differentEntries])
  })
})

describe('日志连续重复折叠', () => {
  it('以相邻时间差折叠，展示最新时间并保留首次时间及稳定键', () => {
    const first = log('Backend WebSocket error', { level: 'ERROR' })
    const second = { ...first, id: 2, ts: 31_000 }
    const third = { ...first, id: 3, ts: 61_000 }
    const entries = Object.freeze([Object.freeze(first), Object.freeze(second), Object.freeze(third)])
    const rows = buildLogRows(entries)
    expect(rows).toEqual([{ entry: third, count: 3, firstTs: 1000, key: buildLogRows([first])[0].key }])
    expect(entries).toEqual([first, second, third])
    expect(rows[0].entry).toBe(third)
  })

  it('去掉重放后再计数，避免把传输重放显示成错误发生次数', () => {
    const first = log('连接断开', { level: 'WARNING' })
    const next = { ...first, id: 2, ts: 2000 }
    const rows = buildLogRows([first, next, { ...first }, { ...next }])
    expect(rows).toHaveLength(1)
    expect(rows[0].count).toBe(2)
  })

  it.each([
    { ts: 31_001 },
    { ts: 999 },
    { channel: '[BACKEND]' as const },
    { level: 'ERROR' as const },
    { msg: '不同错误' },
  ])('超过时间窗口、倒序或不同事件不折叠：%j', (patch) => {
    expect(buildLogRows([log('连接失败'), log('连接失败', { id: 2, ...patch })])).toHaveLength(2)
  })

  it('只合并相邻事件，跨来源的相同编号不产生相同渲染键', () => {
    const entries = [log('A'), log('B'), log('A', { ts: 2000 })]
    const rows = buildLogRows(entries)
    expect(rows.map(row => row.count)).toEqual([1, 1, 1])
    expect(new Set(rows.map(row => row.key)).size).toBe(3)
    expect(buildLogRows([])).toEqual([])
  })
})
