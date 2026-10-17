import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, reactive } from 'vue'
import { oxidnsMonitorApi } from '@/api'
import { useOxiDNSQuerySources } from '@/composables/useOxiDNSQuerySources'
import { querySourceLabel, type OxiDNSQueries, type OxiDNSQuery } from '@/types/oxidnsMonitor'

vi.mock('@/api', () => ({ oxidnsMonitorApi: { query: vi.fn() } }))
const row: OxiDNSQuery = { id: 1, created_at_ms: 1, elapsed_ms: 0, client_ip: '192.0.2.1',
  questions_json: [], error: null, has_response: true, rcode: 'NOERROR', answer_count: 0, answers_json: [] }
type Step = NonNullable<OxiDNSQuery['steps']>[number]
const step = (tag: string, outcome: string, kind = 'executor', sequence_tag = 'main'): Step =>
  ({ event_index: 0, sequence_tag, node_index: 0, kind, tag, outcome })
const run = (tag: string, outcome = 'next', sequence = 'main') => [step(tag, 'entered', 'executor', sequence), step(tag, outcome, 'executor', sequence)]
const label = (...steps: Step[]) => querySourceLabel({ ...row, steps })
const probeHit = [step('local_probe_cache', 'entered', 'executor', 'local_probe'),
  step('qs.match.local_probe.1.0.has_resp', 'not_matched', 'matcher', 'local_probe'),
  step('return', 'return', 'builtin', 'local_probe'), step('local_probe_cache', 'return', 'executor', 'local_probe')]

describe('query source from the standard ConfigFlow trace', () => {
  it.each([['forward_local', '本地 DNS'], ['forward_remote', '远程 DNS'], ['forward_fallback', '备用 DNS']])('summarizes %s', (tag, expected) => {
    expect(label(...run(tag))).toBe(expected)
  })
  it('uses the remote or fallback path after a local probe', () => {
    expect(label(...run('forward_local'), ...run('qs.exec.main.1.drop_resp'), ...run('forward_remote'))).toBe('远程 DNS')
    expect(label(...run('forward_remote', 'error'), ...run('forward_fallback'))).toBe('备用 DNS')
  })
  it('recognizes a short-circuit cache hit but not a miss wrapping a downstream stop', () => {
    expect(label(...run('lazy_cache', 'stop'))).toBe('缓存')
    expect(label(step('lazy_cache', 'entered'), ...run('forward_local'), step('accept', 'stop', 'builtin'), step('lazy_cache', 'stop'))).toBe('本地 DNS')
    expect(label(step('lazy_cache', 'entered'), step('lazy_cache', 'next'))).toBe('未识别')
  })
  it('recognizes a probe-cache hit and lets later remote forwarding replace it', () => {
    expect(label(...probeHit)).toBe('缓存')
    expect(label(...probeHit, ...run('qs.exec.main.1.drop_resp'), ...run('forward_remote'))).toBe('远程 DNS')
    expect(label(step('local_probe_cache', 'entered', 'executor', 'local_probe'),
      ...run('forward_local', 'next', 'local_probe'), step('local_probe_cache', 'return', 'executor', 'local_probe'))).toBe('本地 DNS')
  })
  it('does not claim success from an entry, a failed forward, a matcher or an unmatched completion', () => {
    expect(label(step('forward_remote', 'entered'))).toBe('未识别')
    expect(label(...run('forward_remote', 'error'))).toBe('未识别')
    expect(label(step('forward_remote', 'matched', 'matcher'))).toBe('未识别')
    expect(label(step('forward_remote', 'next'))).toBe('未识别')
    expect(label(...run('forward_custom'))).toBe('未识别')
  })
  it('does not confuse identical plugin tags in different sequences', () => {
    expect(label(step('lazy_cache', 'entered', 'executor', 'one'), step('lazy_cache', 'stop', 'executor', 'two'))).toBe('未识别')
  })
  it('clears discarded responses and distinguishes local handling from upstream resolution', () => {
    expect(label(...run('forward_local'), ...run('qs.exec.main.1.drop_resp'))).toBe('未识别')
    expect(label(...run('forward_local'), step('reject', 'stop', 'builtin'))).toBe('规则拒绝')
    expect(label(...run('custom_hosts'), step('qs.match.main.1.0.has_resp', 'matched', 'matcher'))).toBe('本地 Hosts')
    expect(label(...run('custom_hosts'), step('qs.match.main.1.0.has_resp', 'not_matched', 'matcher'), ...run('forward_remote'))).toBe('远程 DNS')
    expect(querySourceLabel({ ...row, error: 'timeout', steps: run('forward_remote') })).toBe('处理错误')
  })
})

let wrapper: VueWrapper | undefined
beforeEach(() => { vi.resetAllMocks(); Object.defineProperty(document, 'hidden', { configurable: true, value: false }) })
afterEach(() => { wrapper?.unmount(); wrapper = undefined })
function harness(count = 1) {
  const input = reactive({ agentId: 'one', recorder: 'queries', instanceId: 'core', data: {
    success: true, recorder: 'queries', next_cursor: null,
    records: Array.from({ length: count }, (_, i) => ({ ...row, id: i + 1 }))
  } as OxiDNSQueries | null })
  let model!: ReturnType<typeof useOxiDNSQuerySources>
  wrapper = mount(defineComponent({ setup() { model = useOxiDNSQuerySources(input); return () => null } }))
  return { input, model }
}
const detail = (id: string | number, steps = run('forward_remote'), created_at_ms = 1) => ({ data: { success: true, record: { ...row, id, created_at_ms, steps } } } as any)
function deferred() {
  let resolve!: (value: any) => void
  const promise = new Promise<any>(yes => { resolve = yes })
  return { promise, resolve }
}
describe('query list source loading', () => {
  it('loads at most four details concurrently and reuses summaries on list refresh', async () => {
    const pending = deferred()
    vi.mocked(oxidnsMonitorApi.query).mockImplementation(async (_agent, id) => { await pending.promise; return detail(id) })
    const { input, model } = harness(6)
    expect(oxidnsMonitorApi.query).toHaveBeenCalledTimes(4)
    expect(model.sourceOf(row).label).toBe('读取中…')
    pending.resolve(null)
    await flushPromises()
    expect(oxidnsMonitorApi.query).toHaveBeenCalledTimes(6)
    expect(model.sourceOf(row).label).toBe('远程 DNS')
    input.data = { ...input.data!, records: [...input.data!.records] }
    await flushPromises()
    expect(oxidnsMonitorApi.query).toHaveBeenCalledTimes(6)
  })
  it.each(['agentId', 'recorder', 'instanceId'] as const)('aborts and discards stale results when %s changes', async field => {
    const pending = deferred()
    vi.mocked(oxidnsMonitorApi.query).mockReturnValueOnce(pending.promise).mockResolvedValue(detail(1, run('lazy_cache', 'stop')))
    const { input, model } = harness()
    const signal = vi.mocked(oxidnsMonitorApi.query).mock.calls[0][3]?.signal
    input[field] = 'second'
    await flushPromises()
    expect(signal?.aborted).toBe(true)
    pending.resolve(detail(1))
    await flushPromises()
    expect(model.sourceOf(row).label).toBe('缓存')
  })
  it('keeps list rows on a detail failure, offers a retry, and does not repeatedly retry on refresh', async () => {
    vi.mocked(oxidnsMonitorApi.query).mockRejectedValueOnce(new Error('unreachable')).mockResolvedValue(detail(1))
    const { input, model } = harness()
    await flushPromises()
    expect(model.sourceOf(row)).toEqual({ label: '读取失败', failed: true })
    input.data = { ...input.data! }
    await flushPromises()
    expect(oxidnsMonitorApi.query).toHaveBeenCalledTimes(1)
    model.retry(row)
    await flushPromises()
    expect(model.sourceOf(row).label).toBe('远程 DNS')
  })
  it('uses inline steps without requesting details and skips failed or unanswered records', async () => {
    const { input, model } = harness(0)
    const answered = { ...row, steps: run('forward_local') }
    input.data!.records = [answered, { ...row, id: 2, has_response: false }, { ...row, id: 3, error: 'timeout' }]
    input.data = { ...input.data! }
    await flushPromises()
    expect(model.sourceOf(answered).label).toBe('本地 DNS')
    expect(oxidnsMonitorApi.query).not.toHaveBeenCalled()
  })
  it('rejects a detail whose record identity changed, and fetches a new timestamp for a reused ID', async () => {
    vi.mocked(oxidnsMonitorApi.query).mockResolvedValueOnce(detail(1, run('forward_remote'), 99)).mockResolvedValue(detail(1, run('forward_local'), 2))
    const { input, model } = harness()
    await flushPromises()
    expect(model.sourceOf(row).failed).toBe(true)
    const newRow = { ...row, created_at_ms: 2 }
    input.data = { ...input.data!, records: [newRow] }
    await flushPromises()
    expect(model.sourceOf(newRow).label).toBe('本地 DNS')
  })
  it('aborts on unmount without starting remaining queued requests', async () => {
    const pending = deferred()
    vi.mocked(oxidnsMonitorApi.query).mockReturnValue(pending.promise)
    harness(6)
    const signals = vi.mocked(oxidnsMonitorApi.query).mock.calls.map(call => call[3]?.signal)
    wrapper!.unmount(); wrapper = undefined
    pending.resolve(detail(1))
    await flushPromises()
    expect(signals.every(signal => signal?.aborted)).toBe(true)
    expect(oxidnsMonitorApi.query).toHaveBeenCalledTimes(4)
  })
})
