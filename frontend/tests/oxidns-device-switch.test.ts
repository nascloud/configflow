import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createMemoryHistory, createRouter, RouterView, useRoute } from 'vue-router'
import { agentApi, oxidnsMonitorApi } from '@/api'
import OxiDNSMonitor from '@/views/OxiDNSMonitor.vue'
import { Select } from '@/components/ui/select'
import { OXIDNS_DEVICE_STORAGE_KEY } from '@/composables/useOxiDNSDevices'

vi.mock('@/api', () => ({ agentApi: { getAll: vi.fn() }, oxidnsMonitorApi: { overview: vi.fn(), queries: vi.fn(), query: vi.fn() } }))
const wrappers: VueWrapper[] = []
function stubStorage() {
  const values = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    get length() { return values.size },
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, String(value)) },
    removeItem: (key: string) => { values.delete(key) },
    clear: () => { values.clear() },
    key: (index: number) => Array.from(values.keys())[index] ?? null
  } satisfies Storage)
}
const device = (id: string, status = 'online', service_type = 'oxidns') => ({ id, name: 'Device ' + id, host: 'host-' + id, status, service_type })
const response = (data: any) => ({ data } as any)
const snapshot = (id: string) => ({
  success: true, agent: { ...device(id), profile_id: 'default', version: '1.7.0-go' }, collected_at: '2026-10-17T01:00:00Z',
  core: { status: 'running', version: '1.6.1', uptime_ms: 1000, instance_id: id }, recorders: ['queries'],
  metrics: { requests_total: 100, failed_total: 0, avg_latency_ms: 1, inflight: 0, cache_hit_rate: 50 }, caches: [], servers: []
})
async function render(path = '/agents/one/oxidns') {
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/agents/:agentId/oxidns', component: OxiDNSMonitor }, { path: '/agents', component: { template: '<div>Agents</div>' } }
  ] })
  await router.push(path)
  await router.isReady()
  // App.vue keys routed pages by path; this reproduces a real device switch remount.
  const shell = defineComponent({ components: { RouterView }, setup: () => ({ route: useRoute() }), template: '<RouterView :key="route.path" />' })
  const wrapper = mount(shell, { global: { plugins: [router], stubs: {
    OxiDNSOverviewPanel: { props: ['overview'], template: '<div data-test="overview">{{ overview.agent.name }}</div>' },
    OxiDNSQueryTable: { props: ['data'], emits: ['detail'], template: '<div data-test="queries">{{ data?.records[0]?.client_ip }}<button data-test="detail" @click="$emit(\'detail\', { id: 12 })">详情</button></div>' },
    OxiDNSQueryDetail: { props: ['agentId'], template: '<div data-test="query-detail">{{ agentId }}</div>' }
  } } })
  wrappers.push(wrapper)
  await flushPromises()
  return { wrapper, router }
}
beforeEach(() => {
  vi.clearAllMocks()
  stubStorage()
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: true, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} })))
  vi.mocked(agentApi.getAll).mockResolvedValue(response([device('one'), device('two', 'offline'), device('proxy', 'online', 'mihomo')]))
  vi.mocked(oxidnsMonitorApi.overview).mockImplementation(async id => response(snapshot(id)))
  vi.mocked(oxidnsMonitorApi.queries).mockImplementation(async id => response({ success: true, recorder: 'queries', records: [{ id: 12, client_ip: 'client-' + id }], next_cursor: null }))
})
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.unstubAllGlobals()
  document.body.innerHTML = ''
})

describe('OxiDNS monitor device selection', () => {
  it('shows the current device identity and status without a back-to-Agent action', async () => {
    const { wrapper } = await render()
    const selector = wrapper.get('button[role="combobox"]')
    expect(selector.text()).toContain('Device one')
    expect(selector.text()).toContain('host-one')
    expect(selector.text()).toContain('在线')
    expect(wrapper.text()).not.toContain('返回 Agent')
    expect(window.localStorage.getItem(OXIDNS_DEVICE_STORAGE_KEY)).toBe('one')
  })
  it('keeps the query tab after remount, closes details, and fetches only the newly selected device data', async () => {
    const { wrapper, router } = await render('/agents/one/oxidns?tab=queries')
    await wrapper.get('[data-test="detail"]').trigger('click')
    expect(wrapper.get('[data-test="query-detail"]').text()).toBe('one')
    wrapper.findComponent(Select).vm.$emit('update:modelValue', 'two')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/agents/two/oxidns')
    expect(router.currentRoute.value.query.tab).toBe('queries')
    expect(wrapper.find('[data-test="query-detail"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="queries"]').text()).toContain('client-two')
    expect(wrapper.get('[data-test="queries"]').text()).not.toContain('client-one')
    expect(wrapper.get('button[role="combobox"]').text()).toContain('离线')
    expect(window.localStorage.getItem(OXIDNS_DEVICE_STORAGE_KEY)).toBe('two')
  })
  it('preserves a user-selected overview tab instead of an older tab query', async () => {
    const { wrapper, router } = await render('/agents/one/oxidns?tab=queries')
    await wrapper.findAll('[role="tab"]').find(item => item.text() === '运行概览')!.trigger('keydown', { key: 'Enter' })
    wrapper.findComponent(Select).vm.$emit('update:modelValue', 'two')
    await flushPromises()
    expect(router.currentRoute.value.query.tab).toBe('overview')
    expect(wrapper.find('[data-test="overview"]').exists()).toBe(true)
  })
  it('keeps device switching available when the current core cannot be read', async () => {
    vi.mocked(oxidnsMonitorApi.overview).mockRejectedValueOnce({ response: { data: { message: '核心未运行' } } })
    const { wrapper, router } = await render()
    expect(wrapper.text()).toContain('核心未运行')
    expect(wrapper.get('button[role="combobox"]').attributes('disabled')).toBeUndefined()
    wrapper.findComponent(Select).vm.$emit('update:modelValue', 'two')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/agents/two/oxidns')
    expect(wrapper.text()).toContain('Device two')
  })
  it('retries a failed device list without replacing a valid overview', async () => {
    vi.mocked(agentApi.getAll).mockRejectedValueOnce({ response: { data: { message: '设备列表暂不可用' } } })
    const { wrapper } = await render()
    expect(wrapper.text()).toContain('设备列表暂不可用')
    expect(wrapper.find('[data-test="overview"]').exists()).toBe(true)
    await wrapper.findAll('button').find(item => item.text() === '重试设备列表')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).not.toContain('设备列表暂不可用')
    expect(wrapper.get('button[role="combobox"]').attributes('disabled')).toBeUndefined()
  })
  it('shows connection guidance when no OxiDNS devices remain', async () => {
    vi.mocked(agentApi.getAll).mockResolvedValue(response([device('proxy', 'online', 'mihomo')]))
    const { wrapper } = await render()
    expect(wrapper.text()).toContain('暂无 OxiDNS Agent')
    expect(wrapper.find('[data-test="overview"]').exists()).toBe(false)
  })
  it('does not navigate to a non-OxiDNS Agent', async () => {
    const { wrapper, router } = await render()
    wrapper.findComponent(Select).vm.$emit('update:modelValue', 'proxy')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/agents/one/oxidns')
  })
})
