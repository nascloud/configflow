import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import OxiDNSMonitorEntry from '@/views/OxiDNSMonitorEntry.vue'
import { agentApi } from '@/api'

const navigation = vi.hoisted(() => ({ replace: vi.fn(), push: vi.fn() }))
vi.mock('vue-router', () => ({ useRouter: () => navigation }))
vi.mock('@/api', () => ({ agentApi: { getAll: vi.fn() } }))
const wrappers: VueWrapper[] = []
const agent = (id: string, service_type = 'oxidns') => ({ id, name: 'Device ' + id, host: '192.0.2.13', status: 'online', service_type })
const response = (data: unknown) => ({ data } as any)
async function render() {
  const wrapper = mount(OxiDNSMonitorEntry)
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
beforeEach(() => { vi.clearAllMocks(); navigation.replace.mockResolvedValue(undefined) })
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()) })

describe('OxiDNS navigation entry', () => {
  it('opens the single OxiDNS Agent directly with an encoded ID', async () => {
    vi.mocked(agentApi.getAll).mockResolvedValue(response([agent('a/b'), agent('proxy', 'mihomo')]))
    await render()
    expect(navigation.replace).toHaveBeenCalledWith('/agents/a%2Fb/oxidns')
  })
  it('offers a compact selection containing only OxiDNS Agents', async () => {
    vi.mocked(agentApi.getAll).mockResolvedValue(response([agent('one'), agent('two'), agent('other', 'mosdns')]))
    const wrapper = await render()
    expect(navigation.replace).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Device one')
    expect(wrapper.text()).toContain('Device two')
    expect(wrapper.text()).not.toContain('Device other')
    await wrapper.get('button[aria-label="查看 Device two 的监控"]').trigger('click')
    expect(navigation.push).toHaveBeenCalledWith('/agents/two/oxidns')
  })
  it('shows connection guidance only after a successful empty result', async () => {
    vi.mocked(agentApi.getAll).mockResolvedValue(response([agent('proxy', 'mihomo')]))
    const wrapper = await render()
    expect(wrapper.text()).toContain('暂无 OxiDNS Agent')
    const button = wrapper.findAll('button').find(item => item.text().includes('前往 Agent 接入'))!
    await button.trigger('click')
    expect(navigation.push).toHaveBeenCalledWith('/agents')
  })
  it('shows a readable error and retries instead of reporting no Agents', async () => {
    vi.mocked(agentApi.getAll).mockRejectedValueOnce({ response: { data: { message: '服务暂不可用' } } })
    const wrapper = await render()
    expect(wrapper.text()).toContain('服务暂不可用')
    expect(wrapper.text()).not.toContain('暂无 OxiDNS Agent')
    vi.mocked(agentApi.getAll).mockResolvedValueOnce(response([agent('recovered')]))
    await wrapper.findAll('button').find(item => item.text() === '重试')!.trigger('click')
    await flushPromises()
    expect(navigation.replace).toHaveBeenCalledWith('/agents/recovered/oxidns')
  })
  it('ignores a late singleton response after leaving the page', async () => {
    let finish!: (value: any) => void
    vi.mocked(agentApi.getAll).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    const wrapper = await render()
    wrapper.unmount()
    finish(response([agent('late')]))
    await flushPromises()
    expect(navigation.replace).not.toHaveBeenCalled()
  })
})
