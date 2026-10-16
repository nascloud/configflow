import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { oxidnsMonitorApi } from '@/api'
import { ListboxFilter } from 'reka-ui'
import OxiDNSClientSelect from '@/components/agents/OxiDNSClientSelect.vue'
import OxiDNSQueryTable from '@/components/agents/OxiDNSQueryTable.vue'
import { emptyQueryFilters } from '@/types/oxidnsMonitor'

vi.mock('@/api', () => ({ oxidnsMonitorApi: { clients: vi.fn() } }))
const wrappers: VueWrapper[] = []
const result = (ips = ['192.0.2.1', '192.0.2.10']) => ({ data: { success: true, recorder: 'recorder', clients: ips.map(ip => ({ ip, count: 2 })), sample_size: 4, sample_limited: false } } as any)
function deferred() {
  let resolve!: (value: any) => void
  const promise = new Promise(yes => { resolve = yes })
  return { resolve, promise }
}
function mountPicker(props: Record<string, any> = {}) {
  const wrapper = mount(OxiDNSClientSelect, { attachTo: document.body, props: { modelValue: '', agentId: 'one', recorder: 'recorder', range: '1h', ...props } })
  wrappers.push(wrapper)
  return wrapper
}
async function open(wrapper: VueWrapper) {
  await wrapper.get('[role="combobox"]').trigger('click')
  await flushPromises()
}
async function choose(ip: string) {
  const option = [...document.querySelectorAll<HTMLElement>('[role="option"]')].find(node => node.textContent?.includes(ip))
  expect(option).toBeTruthy()
  option!.click()
  await flushPromises()
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(oxidnsMonitorApi.clients).mockResolvedValue(result())
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} })))
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  document.body.innerHTML = ''
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('existing OxiDNS clients', () => {
  it('loads a standalone time-scoped list only when opened and selects the full IP', async () => {
    const wrapper = mountPicker()
    expect(oxidnsMonitorApi.clients).not.toHaveBeenCalled()
    await open(wrapper)
    const [id, params] = vi.mocked(oxidnsMonitorApi.clients).mock.calls[0]
    expect(id).toBe('one')
    expect(params).toMatchObject({ recorder: 'recorder' })
    expect(Number(params.until_ms) - Number(params.since_ms)).toBe(3600000)
    expect(Object.keys(params).sort()).toEqual(['recorder', 'search', 'since_ms', 'until_ms'])
    expect(document.body.textContent).toContain('192.0.2.10')
    await choose('192.0.2.10')
    expect(wrapper.emitted('update:modelValue')?.[0]).toEqual(['192.0.2.10'])
    expect(wrapper.get('[role="combobox"]').attributes('aria-expanded')).toBe('false')
  })
  it('searches historical clients separately and ignores obsolete responses', async () => {
    vi.useFakeTimers()
    const wrapper = mountPicker({ range: 'all' })
    await open(wrapper)
    const stale = deferred()
    vi.mocked(oxidnsMonitorApi.clients).mockImplementationOnce(() => stale.promise as any)
    wrapper.getComponent(ListboxFilter).vm.$emit('update:modelValue', '192.0.2.')
    await vi.advanceTimersByTimeAsync(300)
    const oldSignal = vi.mocked(oxidnsMonitorApi.clients).mock.calls[1][2]?.signal
    vi.mocked(oxidnsMonitorApi.clients).mockResolvedValue(result(['2001:db8::5']))
    wrapper.getComponent(ListboxFilter).vm.$emit('update:modelValue', '2001:db8::')
    await vi.advanceTimersByTimeAsync(300)
    await flushPromises()
    expect(oldSignal?.aborted).toBe(true)
    expect(vi.mocked(oxidnsMonitorApi.clients).mock.lastCall?.[1]).toMatchObject({ search: '2001:db8::', since_ms: undefined })
    stale.resolve(result(['192.0.2.99']))
    await flushPromises()
    expect(document.body.textContent).toContain('2001:db8::5')
    expect(document.body.textContent).not.toContain('192.0.2.99')
  })
  it('keeps the selected client available when it falls outside the recent sample', async () => {
    vi.mocked(oxidnsMonitorApi.clients).mockResolvedValue({ data: { ...result().data, sample_size: 10000, sample_limited: true } } as any)
    const wrapper = mountPicker({ modelValue: '192.0.2.250' })
    await open(wrapper)
    expect(document.querySelector('[role="listbox"]')?.textContent).toContain('192.0.2.250')
    expect(document.querySelector('[role="option"][aria-selected="true"]')?.textContent).toContain('192.0.2.250')
    expect(document.body.textContent).toContain('最近 10,000 条匹配记录')
  })
  it('aborts and clears options when changing devices, recorders or time ranges', async () => {
    const pending = deferred()
    vi.mocked(oxidnsMonitorApi.clients).mockImplementationOnce(() => pending.promise as any)
    const wrapper = mountPicker()
    await open(wrapper)
    const signal = vi.mocked(oxidnsMonitorApi.clients).mock.calls[0][2]?.signal
    await wrapper.setProps({ agentId: 'two', recorder: 'other', range: '24h' })
    expect(signal?.aborted).toBe(true)
    pending.resolve(result(['192.0.2.99']))
    await flushPromises()
    vi.mocked(oxidnsMonitorApi.clients).mockResolvedValue(result(['192.0.2.20']))
    await open(wrapper)
    expect(vi.mocked(oxidnsMonitorApi.clients).mock.lastCall?.[0]).toBe('two')
    expect(vi.mocked(oxidnsMonitorApi.clients).mock.lastCall?.[1].recorder).toBe('other')
    expect(document.body.textContent).not.toContain('192.0.2.99')
    expect(document.body.textContent).toContain('192.0.2.20')
  })
  it('explains an old Agent error and allows retry without a misleading empty state', async () => {
    vi.mocked(oxidnsMonitorApi.clients).mockRejectedValueOnce({ response: { data: { message: '请升级 Agent 到 1.7.1-go' } } })
    const wrapper = mountPicker()
    await open(wrapper)
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('1.7.1-go')
    expect(document.body.textContent).not.toContain('暂无客户端记录')
    const retry = [...document.querySelectorAll('button')].find(node => node.textContent?.trim() === '重试')!
    retry.click()
    await flushPromises()
    expect(document.body.textContent).toContain('192.0.2.10')
  })
  it('applies a client selection immediately and can clear it without typing', async () => {
    const wrapper = mount(OxiDNSQueryTable, { attachTo: document.body, props: {
      agentId: 'one', recorders: ['recorder'], recorder: 'recorder', filters: emptyQueryFilters(),
      data: null, loading: false, error: '', page: 1, hasNext: false
    } })
    wrappers.push(wrapper)
    const picker = wrapper.getComponent(OxiDNSClientSelect)
    await open(picker)
    await choose('192.0.2.10')
    expect(wrapper.emitted('filter')?.[0]?.[0]).toMatchObject({ client_ip: '192.0.2.10' })
    await open(picker)
    const all = [...document.querySelectorAll('button')].find(node => node.textContent?.trim() === '全部客户端')!
    all.click()
    await flushPromises()
    expect(wrapper.emitted('filter')?.at(-1)?.[0]).toMatchObject({ client_ip: '' })
  })
})
