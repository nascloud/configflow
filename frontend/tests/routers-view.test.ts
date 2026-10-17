import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import Routers from '@/views/Routers.vue'
import { routerApi, agentApi } from '@/api'
import { NAV_GROUPS, scopeOfPath } from '@/navigation'

vi.mock('@/api', () => ({
  agentApi: { getAll: vi.fn() },
  routerApi: { list: vi.fn(), create: vi.fn(), update: vi.fn(), remove: vi.fn(), probe: vi.fn(), report: vi.fn() }
}))
vi.mock('@/lib/feedback', () => ({ notify: { success: vi.fn(), error: vi.fn() }, confirmDanger: vi.fn() }))

const wrappers: VueWrapper[] = []
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.clearAllMocks()
})

const ROUTER = {
  id: 'router_1', name: '主路由', type: 'ikuai', endpoint: 'http://192.0.2.1', username: 'admin',
  verify_tls: true, proxy_gateway_agent_id: 'a1', dns_agent_id: '', has_password: true,
  last_probe: { ok: true, version: '4.0.308', at: '2026-10-17T10:00:00' }
}
const REPORT = {
  in_sync: false,
  summary: { synced: 1, adoptable: 1, missing: 1, extra: 0 },
  desired: { fake_ip_range: '198.18.0.0/16', proxy_gateway: '192.0.2.4', proxy_cidrs: [], warnings: ['规则集「X」没有本地缓存'] },
  routes: {
    items: [
      { cidr: '198.18.0.0/16', sources: ['fake-ip'], status: 'synced', detail: '' },
      { cidr: '91.108.4.0/22', sources: ['Telegram'], status: 'adoptable', detail: '将直接接管' }
    ],
    extra: [],
    unmanaged_other: [{ cidr: '8.8.8.8/32', gateway: '192.0.2.4', comment: 'DNS', enabled: true }]
  },
  nat_bypass: { items: [{ cidr: '198.18.0.0/16', status: 'missing', detail: '仍会被源地址转换' }], disabled_rules: [] },
  return_routing: { status: 'ok', nft_available: true, iproute_available: true,
    return_tables: [{ family: 'inet', name: 'mihomo_return', marks: ['0x00400000'] }],
    fwmark_rules: [{ priority: '104', mark: '0x400000/0x400000', table: '104' }],
    table_routes: { 104: ['default via 192.0.2.1 dev eth0'] }, tun_devices: ['Meta'] }
}

describe('routers view', () => {
  it('is reachable from the profile-scoped navigation', () => {
    const profile = NAV_GROUPS.find(group => group.scope === 'profile')
    expect(profile?.items.some(item => item.path === '/routers' && item.label === '上游路由')).toBe(true)
    expect(scopeOfPath('/routers')).toBe('profile')
  })

  it('lists routers without passwords and renders the drift report', async () => {
    vi.mocked(routerApi.list).mockResolvedValue({ data: { success: true, routers: [ROUTER] } } as any)
    vi.mocked(agentApi.getAll).mockResolvedValue({ data: [{ id: 'a1', name: 'mihomo', host: '192.0.2.4', service_type: 'mihomo' }] } as any)
    vi.mocked(routerApi.report).mockResolvedValue({ data: { success: true, report: REPORT, generated_at: '2026-10-17T10:00:00' } } as any)

    const wrapper = mount(Routers, { attachTo: document.body })
    wrappers.push(wrapper)
    await flushPromises()
    expect(wrapper.text()).toContain('主路由')
    expect(wrapper.text()).toContain('4.0.308')
    expect(wrapper.html()).not.toContain('password')

    const reportButton = wrapper.findAll('button').find(button => button.text().includes('差异报告'))
    await reportButton!.trigger('click')
    await flushPromises()
    expect(routerApi.report).toHaveBeenCalledWith('router_1')
    const text = wrapper.text()
    for (const label of ['已同步', '可接管', '缺失', '存在差异', '已配置', 'mihomo_return', '8.8.8.8/32', '没有本地缓存']) {
      expect(text).toContain(label)
    }
  })
})
