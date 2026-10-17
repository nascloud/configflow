import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import PolicyRoutingPanel from '@/components/routers/PolicyRoutingPanel.vue'
import { routerApi } from '@/api'

vi.mock('@/api', () => ({ routerApi: { policy: vi.fn() } }))
const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); vi.clearAllMocks() })
const response = (objects: any[] = []) => ({ data: { success: true, generated_at: '2026-10-17T10:00:00',
  policy: { objects, source_agents: [{ id: 'dns', name: 'OxiDNS' }] } } })
const setup = async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }] })
  await router.push('/')
  const wrapper = mount(PolicyRoutingPanel, { props: { id: 'ros', name: '主路由', profile: 'office', ruleSets: ['Telegram'], gateway: '192.168.0.3' }, global: { plugins: [router] } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
const click = async (wrapper: VueWrapper, label: string) => {
  await wrapper.findAll('button').find(button => button.text().startsWith(label))!.trigger('click')
  await flushPromises()
}

describe('RouterOS policy objects', () => {
  it('pages large address lists, searches fields and opens the existing source editor', async () => {
    vi.mocked(routerApi.policy).mockResolvedValue(response(Array.from({ length: 123 }, (_, i) => ({
      id: `*${i}`, kind: 'addresses', status: 'matched', fields: { list: 'CF_Destination_Proxy_IP_01', address: `10.0.0.${i}/32` }
    }))) as any)
    const wrapper = await setup()
    expect(routerApi.policy).toHaveBeenCalledWith('ros', 'office')
    expect(wrapper.findAll('tbody tr')).toHaveLength(50)
    await click(wrapper, '下一页')
    expect(wrapper.find('tbody').text()).toContain('10.0.0.50/32')
    await wrapper.find('input').setValue('10.0.0.122/32')
    expect(wrapper.findAll('tbody tr')).toHaveLength(1)
    expect(wrapper.text()).toContain('第 1 / 1 页')
    expect(wrapper.find('a').attributes('href')).toBe('/agents/dns/oxidns?tab=devices')
    await click(wrapper, '编辑规则集与网关')
    expect(wrapper.emitted('edit')).toHaveLength(1)
  })

  it('distinguishes live edits from missing objects and shows runtime next hop', async () => {
    vi.mocked(routerApi.policy).mockResolvedValue(response([
      { id: '*1', kind: 'mangle', status: 'changed', fields: { chain: 'prerouting', action: 'jump', disabled: 'true', 'jump-target': 'CF_Proxy_Routing_01' } },
      { id: '*2', kind: 'mangle', status: 'missing', fields: { chain: 'CF_Proxy_Routing_01', action: 'mark-routing' } },
      { id: '*3', kind: 'routes', status: 'matched', fields: { 'routing-table': 'CF_Proxy_Routing_01', gateway: '192.168.0.3@main', 'immediate-gw': '192.168.0.3%lan4', active: 'true' } }
    ]) as any)
    const wrapper = await setup()
    await click(wrapper, 'Mangle')
    expect(wrapper.text()).toContain('已被外部修改')
    expect(wrapper.text()).toContain('设备上缺失')
    expect(wrapper.text()).toContain('查看上次发布记录')
    await click(wrapper, '下一跳')
    expect(wrapper.text()).toContain('192.168.0.3%lan4')
    expect(wrapper.text()).toContain('路由活跃')
  })

  it('discards an old device response and clears old data after failed refresh', async () => {
    let resolveOld!: (value: any) => void
    vi.mocked(routerApi.policy).mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }) as any)
    const wrapper = await setup()
    vi.mocked(routerApi.policy).mockResolvedValueOnce(response() as any)
    await wrapper.setProps({ id: 'new', profile: 'home' })
    await flushPromises()
    resolveOld(response([{ id: '*old', kind: 'addresses', status: 'matched', fields: { address: 'old-data' } }]))
    await flushPromises()
    expect(wrapper.text()).not.toContain('old-data')
    expect(wrapper.text()).toContain('尚无已下发的策略对象')
    vi.mocked(routerApi.policy).mockRejectedValueOnce(new Error('设备无法连接'))
    await click(wrapper, '刷新')
    expect(wrapper.find('[role="alert"]').text()).toContain('设备无法连接')
    expect(wrapper.text()).not.toContain('尚无已下发的策略对象')
  })
})
