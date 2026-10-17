import type { OxiDNSQuery, OxiDNSQuerySource } from '@/types/oxidnsMonitor'

type Step = NonNullable<OxiDNSQuery['steps']>[number]
const forwards = new Map([['forward_local', '本地 DNS'], ['forward_remote', '远程 DNS'], ['forward_fallback', '备用 DNS']])
const completed = new Set(['next', 'stop', 'return'])
const sameCall = (a: Step, b: Step) => a.tag === b.tag && a.sequence_tag === b.sequence_tag && a.node_index === b.node_index
const quickPlugin = (tag: string | null, type: string) => tag?.startsWith('qs.') && tag.endsWith('.' + type)
const summaryLabels = new Map<OxiDNSQuerySource, string>([
  ['local', '本地 DNS'], ['remote', '远程 DNS'], ['fallback', '备用 DNS'], ['cache', '缓存'],
  ['hosts', '本地 Hosts'], ['blocked', '规则拦截'], ['rejected', '规则拒绝'], ['unknown', '未识别'],
  ['unrecorded', '未记录'], ['error', '处理错误'], ['no_response', '无应答']
])
export const hasQuerySource = (record: OxiDNSQuery) => record.resolution_source !== undefined && summaryLabels.has(record.resolution_source)

/** Summarize ConfigFlow's standard routing tags, not the winning server inside a forward group. */
export function querySourceLabel(record: OxiDNSQuery): string {
  if (record.error) return '处理错误'
  if (!record.has_response) return '无应答'
  if (hasQuerySource(record)) return summaryLabels.get(record.resolution_source!)!
  const steps = record.steps || []
  if (!steps.length) return '未记录'
  let source = '未识别'
  const calls: Array<{ step: Step; index: number }> = []
  for (const [index, step] of steps.entries()) {
    if (step.kind === 'builtin' && step.tag === 'reject' && step.outcome === 'stop') source = '规则拒绝'
    if (step.kind !== 'executor') continue
    if (step.outcome === 'entered') {
      calls.push({ step, index })
      continue
    }
    let callIndex = calls.length - 1
    while (callIndex >= 0 && !sameCall(calls[callIndex].step, step)) callIndex--
    if (callIndex < 0) continue
    const [call] = calls.splice(callIndex, 1)
    if (!completed.has(step.outcome)) continue
    const forward = forwards.get(step.tag || '')
    if (forward) {
      source = forward
    } else if (quickPlugin(step.tag, 'drop_resp') || step.tag === 'drop_resp') {
      source = '未识别'
    } else if (step.tag === 'adguard_block') {
      source = '规则拦截'
    } else if (step.tag === 'custom_hosts') {
      const check = steps[index + 1]
      if (check?.kind === 'matcher' && quickPlugin(check.tag, 'has_resp') && check.outcome === 'matched') source = '本地 Hosts'
    } else if (step.tag === 'lazy_cache' && step.outcome === 'stop' && index === call.index + 1) {
      // A short-circuit hit has no downstream steps. A miss wraps the entire
      // remaining chain, so its trailing "stop" must not overwrite a forward.
      source = '缓存'
    } else if (step.tag === 'local_probe_cache' && step.sequence_tag === 'local_probe') {
      const inner = steps.slice(call.index + 1, index)
      // The generated probe skips !has_resp -> forward_local on a cache hit,
      // then returns. Later remote/fallback execution still replaces this label.
      if (inner.length === 2 && inner[0].kind === 'matcher' && quickPlugin(inner[0].tag, 'has_resp')
        && inner[0].outcome === 'not_matched' && inner[1].kind === 'builtin'
        && inner[1].tag === 'return' && inner[1].outcome === 'return' && step.outcome === 'return') source = '缓存'
    }
  }
  return source
}
