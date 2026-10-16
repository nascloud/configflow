import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { oxidnsMonitorApi } from '@/api'
import { useOxiDNSMonitor } from '@/composables/useOxiDNSMonitor'
import { emptyQueryFilters, formatDNSCode, type OxiDNSOverview } from '@/types/oxidnsMonitor'
import OxiDNSQueryTable from '@/components/agents/OxiDNSQueryTable.vue'
import OxiDNSQueryDetail from '@/components/agents/OxiDNSQueryDetail.vue'
import OxiDNSMetric from '@/components/agents/OxiDNSMetric.vue'
import { labelOfPath, navigationPath, scopeOfPath } from '@/navigation'

vi.mock('@/api', () => ({ oxidnsMonitorApi: { overview: vi.fn(), queries: vi.fn(), query: vi.fn() } }))
const wrappers: VueWrapper[] = []
const hiddenDescriptor = Object.getOwnPropertyDescriptor(document, 'hidden')
const snapshot = (count: number | null = 100, at = '2026-10-17T01:00:00Z', instance = 'core-one'): OxiDNSOverview => ({
  success: true, agent: { id: 'one', name: 'OxiDNS', host: '192.0.2.13', profile_id: 'default', version: '1.7.0-go', status: 'online' },
  collected_at: at, core: { version: '1.6.1', status: 'running', uptime_ms: 120000, instance_id: instance },
  recorders: ['queries'], metrics: { requests_total: count, failed_total: 2, avg_latency_ms: 0, inflight: 0, cache_hit_rate: 75 },
  caches: [], servers: []
})
const queryRecord = { id: 12, created_at_ms: 1700000000000, elapsed_ms: 0, client_ip: '192.0.2.5',
  questions_json: [{ name: 'example.test', qtype: 1, qclass: 1 }], error: null, has_response: true, rcode: 0,
  answer_count: 1, answers_json: [{ name: 'example.test', rr_type: 1, ttl: 60, payload_text: '192.0.2.1' }] }
const records = (next: string | null = null) => ({ success: true, recorder: 'queries', records: [queryRecord], next_cursor: next })
const response = (data: any) => ({ data } as any)
function deferred() {
  let resolve!: (value: any) => void
  let reject!: (value: any) => void
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function harness(initialTab = 'overview') {
  const id = ref('one')
  const tab = ref(initialTab)
  let model!: ReturnType<typeof useOxiDNSMonitor>
  const wrapper = mount(defineComponent({ setup() { model = useOxiDNSMonitor(id, tab); return () => h('div') } }))
  wrappers.push(wrapper)
  return { id, tab, model, wrapper }
}
beforeEach(() => {
  vi.clearAllMocks()
  Object.defineProperty(document, 'hidden', { configurable: true, value: false })
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} })))
  vi.mocked(oxidnsMonitorApi.overview).mockResolvedValue(response(snapshot()))
  vi.mocked(oxidnsMonitorApi.queries).mockResolvedValue(response(records()))
  vi.mocked(oxidnsMonitorApi.query).mockResolvedValue(response({ success: true, recorder: 'queries', record: { ...queryRecord, steps: [] } }))
})
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.useRealTimers()
  vi.unstubAllGlobals()
  if (hiddenDescriptor) Object.defineProperty(document, 'hidden', hiddenDescriptor)
  else Reflect.deleteProperty(document, 'hidden')
  document.body.innerHTML = ''
})

describe('OxiDNS monitoring requests', () => {
  it('does not request records until the query tab is opened, and never requests an absent recorder', async () => {
    const { tab, model } = harness()
    await flushPromises()
    expect(oxidnsMonitorApi.queries).not.toHaveBeenCalled()
    tab.value = 'queries'
    await flushPromises()
    expect(oxidnsMonitorApi.queries).toHaveBeenCalledTimes(1)
    vi.mocked(oxidnsMonitorApi.overview).mockResolvedValue(response({ ...snapshot(), recorders: [] }))
    await model.refresh()
    expect(model.recorder.value).toBe('')
    expect(model.queries.value).toBeNull()
    expect(oxidnsMonitorApi.queries).toHaveBeenCalledTimes(1)
  })
  it('computes QPS only from matching core instances and nondecreasing real counters', async () => {
    const { model } = harness()
    await flushPromises()
    expect(model.qps.value).toBeNull()
    vi.mocked(oxidnsMonitorApi.overview).mockResolvedValue(response(snapshot(150, '2026-10-17T01:00:10Z')))
    await model.refresh()
    expect(model.qps.value).toBe(5)
    vi.mocked(oxidnsMonitorApi.overview).mockResolvedValue(response(snapshot(180, '2026-10-17T01:00:20Z', 'new-core')))
    await model.refresh()
    expect(model.qps.value).toBeNull()
    vi.mocked(oxidnsMonitorApi.overview).mockResolvedValue(response(snapshot(null, '2026-10-17T01:00:30Z', 'new-core')))
    await model.refresh()
    expect(model.qps.value).toBeNull()
  })
  it('keeps successful overview data when refresh fails, without pretending it is fresh', async () => {
    const { model } = harness()
    await flushPromises()
    vi.mocked(oxidnsMonitorApi.overview).mockRejectedValue({ response: { data: { code: 'CORE_UNREACHABLE', message: '无法连接 OxiDNS 核心' } } })
    await model.refresh()
    expect(model.overview.value?.collected_at).toBe('2026-10-17T01:00:00Z')
    expect(model.overviewError.value).toBe('无法连接 OxiDNS 核心')
  })
  it('preserves cursor history and its time window while background refresh leaves old pages alone', async () => {
    vi.mocked(oxidnsMonitorApi.queries).mockResolvedValue(response(records('1700000000000:12')))
    const { model } = harness('queries')
    await flushPromises()
    const firstParams = vi.mocked(oxidnsMonitorApi.queries).mock.calls[0][1]
    model.nextPage()
    await flushPromises()
    expect(model.page.value).toBe(2)
    const nextParams = vi.mocked(oxidnsMonitorApi.queries).mock.calls[1][1]
    expect(nextParams.cursor).toBe('1700000000000:12')
    expect(nextParams.until_ms).toBe(firstParams.until_ms)
    await model.refresh()
    expect(oxidnsMonitorApi.queries).toHaveBeenCalledTimes(2)
    expect(model.page.value).toBe(2)
    await model.refresh(true)
    expect(vi.mocked(oxidnsMonitorApi.queries).mock.calls[2][1].cursor).toBe('1700000000000:12')
    model.previousPage()
    await flushPromises()
    expect(model.page.value).toBe(1)
    expect(vi.mocked(oxidnsMonitorApi.queries).mock.calls[3][1].until_ms).toBe(firstParams.until_ms)
  })
  it('aborts old filters and ignores their late results', async () => {
    const pending = deferred()
    vi.mocked(oxidnsMonitorApi.queries).mockImplementationOnce(() => pending.promise as any)
    const { model } = harness('queries')
    await flushPromises()
    const oldSignal = vi.mocked(oxidnsMonitorApi.queries).mock.calls[0][2]?.signal
    model.applyFilters({ ...emptyQueryFilters(), qname: 'current.test', status: 'error' })
    await flushPromises()
    expect(oldSignal?.aborted).toBe(true)
    expect(vi.mocked(oxidnsMonitorApi.queries).mock.calls[1][1]).toMatchObject({ qname: 'current.test', status: 'error', limit: 50 })
    pending.resolve(response({ ...records(), records: [{ ...queryRecord, id: 99 }] }))
    await flushPromises()
    expect(model.queries.value?.records[0].id).toBe(12)
  })
  it('stops automatic refresh while hidden or paused and clears requests on unmount', async () => {
    vi.useFakeTimers()
    const { model, wrapper } = harness()
    await flushPromises()
    Object.defineProperty(document, 'hidden', { configurable: true, value: true })
    await vi.advanceTimersByTimeAsync(20000)
    expect(oxidnsMonitorApi.overview).toHaveBeenCalledTimes(1)
    Object.defineProperty(document, 'hidden', { configurable: true, value: false })
    document.dispatchEvent(new Event('visibilitychange'))
    await flushPromises()
    expect(oxidnsMonitorApi.overview).toHaveBeenCalledTimes(2)
    model.autoRefresh.value = false
    await vi.advanceTimersByTimeAsync(20000)
    expect(oxidnsMonitorApi.overview).toHaveBeenCalledTimes(2)
    wrapper.unmount()
    await vi.advanceTimersByTimeAsync(20000)
    expect(oxidnsMonitorApi.overview).toHaveBeenCalledTimes(2)
  })
  it('does not start a query request after an in-flight refresh is unmounted', async () => {
    const { model, wrapper } = harness('queries')
    await flushPromises()
    const pending = deferred()
    vi.mocked(oxidnsMonitorApi.overview).mockImplementationOnce(() => pending.promise as any)
    const refreshing = model.refresh(true)
    const queryCount = vi.mocked(oxidnsMonitorApi.queries).mock.calls.length
    wrapper.unmount()
    pending.resolve(response(snapshot()))
    await refreshing
    expect(oxidnsMonitorApi.queries).toHaveBeenCalledTimes(queryCount)
  })
  it('resets data and ignores old responses when moving to a different Agent', async () => {
    const pending = deferred()
    vi.mocked(oxidnsMonitorApi.overview).mockImplementationOnce(() => pending.promise as any)
    const { id, model } = harness()
    await flushPromises()
    const oldSignal = vi.mocked(oxidnsMonitorApi.overview).mock.calls[0][1]?.signal
    id.value = 'second'
    await flushPromises()
    expect(oldSignal?.aborted).toBe(true)
    pending.resolve(response({ ...snapshot(), agent: { ...snapshot().agent, name: 'Stale Agent' } }))
    await flushPromises()
    expect(model.overview.value?.agent.name).not.toBe('Stale Agent')
    expect(oxidnsMonitorApi.overview).toHaveBeenLastCalledWith('second', expect.objectContaining({ signal: expect.any(AbortSignal) }))
  })
})

describe('OxiDNS monitor display', () => {
  it('does not turn unavailable metrics into a fabricated zero', () => {
    const wrapper = mount(OxiDNSMetric, { props: { label: '失败请求', value: null } })
    wrappers.push(wrapper)
    expect(wrapper.text()).toContain('—')
    expect(wrapper.text()).not.toContain('0')
  })
  it('shows zero elapsed time and never labels a normal answer as a cache hit', () => {
    const wrapper = mount(OxiDNSQueryTable, { props: {
      agentId: 'one', recorders: ['queries'], recorder: 'queries', filters: emptyQueryFilters(), data: records(),
      loading: false, error: '', page: 1, hasNext: false
    } })
    wrappers.push(wrapper)
    expect(wrapper.text()).toContain('0 ms')
    expect(wrapper.text()).toContain('NOERROR')
    expect(wrapper.text()).not.toContain('缓存命中')
    expect(wrapper.text()).not.toContain('命中规则')
    expect(wrapper.text()).toContain('本页 1 条')
  })
  it('renders actual answer content and only actual recorded steps in query details', async () => {
    vi.mocked(oxidnsMonitorApi.query).mockResolvedValue(response({ success: true, recorder: 'queries', record: {
      ...queryRecord, steps: [{ event_index: 0, sequence_tag: 'main', node_index: 1, kind: 'matcher', tag: 'allow_list', outcome: 'matched' }]
    } }))
    const wrapper = mount(OxiDNSQueryDetail, { attachTo: document.body, props: { agentId: 'one', recorder: 'queries', recordId: 12 } })
    wrappers.push(wrapper)
    await flushPromises()
    expect(document.body.textContent).toContain('192.0.2.1')
    expect(document.body.textContent).toContain('allow_list')
    expect(document.body.textContent).toContain('matched')
    expect(oxidnsMonitorApi.query).toHaveBeenCalledWith('one', 12, 'queries', expect.objectContaining({ signal: expect.any(AbortSignal) }))
  })
  it('highlights the dedicated OxiDNS navigation entry in overview scope', () => {
    expect(navigationPath('/agents/oxi-13/oxidns')).toBe('/oxidns')
    expect(scopeOfPath('/agents/oxi-13/oxidns')).toBe('overview')
    expect(labelOfPath('/agents/oxi-13/oxidns')).toBe('OxiDNS 监控')
    expect(navigationPath('/agents')).toBe('/agents')
    expect(navigationPath('/agents/oxi-13/unrelated')).toBe('/agents/oxi-13/unrelated')
  })
})

describe('OxiDNS response code display', () => {
  it.each([
    ['No Error', 'NOERROR'], ['Form Error', 'FORMERR'], ['Server Failure', 'SERVFAIL'],
    ['Non-Existent Domain', 'NXDOMAIN'], ['Not Implemented', 'NOTIMP'], ['Query Refused', 'REFUSED'],
    ['Name should not exist', 'YXDOMAIN'], ['RR Set should not exist', 'YXRRSET'],
    ['RR Set does not exist', 'NXRRSET'], ['Not authorized', 'NOTAUTH'], ['Name not in zone', 'NOTZONE'],
    ['Bad OPT Version', 'BADVERS'], ['TSIG Signature Failure', 'BADSIG'], ['Key not recognized', 'BADKEY'],
    ['Signature out of time window', 'BADTIME'], ['Bad TKEY mode', 'BADMODE'], ['Duplicate key name', 'BADNAME'],
    ['Algorithm not supported', 'BADALG'], ['Bad truncation', 'BADTRUNC'], ['Bad server cookie', 'BADCOOKIE']
  ])('shows official response %s as %s', (official, mnemonic) => {
    expect(formatDNSCode(official)).toBe(mnemonic)
  })
  it('normalizes known case variants, numeric values, and existing mnemonics', () => {
    expect(formatDNSCode('NO ERROR')).toBe('NOERROR')
    expect(formatDNSCode('nxdomain')).toBe('NXDOMAIN')
    expect(formatDNSCode(0)).toBe('NOERROR')
    expect(formatDNSCode('23')).toBe('BADCOOKIE')
    expect(formatDNSCode(null)).toBe('—')
  })
  it('preserves unrecognized core response codes verbatim', () => {
    expect(formatDNSCode('RCODE42')).toBe('RCODE42')
    expect(formatDNSCode('Future Response')).toBe('Future Response')
    expect(formatDNSCode(65535)).toBe('65535')
    expect(formatDNSCode('constructor')).toBe('constructor')
  })
})
