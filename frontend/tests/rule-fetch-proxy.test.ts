import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import SystemSettings from '@/views/SystemSettings.vue'
import { ruleFetchProxyApi } from '@/api'
import { notify } from '@/lib/feedback'

vi.mock('@/api', () => ({
  default: { get: vi.fn(async () => ({ data: { enabled: false } })) },
  configApi: {},
  configTokenApi: { get: vi.fn(async () => ({ data: { config_token: '' } })) },
  serverDomainApi: { get: vi.fn(async () => ({ data: { server_domain: 'http://configflow.test' } })) },
  subStoreUrlApi: { get: vi.fn(async () => ({ data: { sub_store_url: '' } })) },
  ruleFetchProxyApi: { get: vi.fn(), update: vi.fn() }
}))

const proxy = 'http://proxy-user:proxy-secret@192.168.0.3:7890'
const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(ruleFetchProxyApi.get).mockResolvedValue({ data: { rule_fetch_proxy: proxy } } as any)
  vi.mocked(ruleFetchProxyApi.update).mockImplementation(async data => ({ data: { success: true, ...data } }) as any)
  vi.spyOn(notify, 'success').mockImplementation(() => 0)
  vi.spyOn(notify, 'error').mockImplementation(() => 0)
})
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  vi.restoreAllMocks()
})
async function render() {
  const wrapper = mount(SystemSettings)
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}

describe('rule download proxy setting', () => {
  it('loads the saved proxy masked and saves changed values on blur without displaying credentials', async () => {
    const wrapper = await render()
    const input = wrapper.get<HTMLInputElement>('#rule-fetch-proxy')
    expect(input.element.value).toBe(proxy)
    expect(input.attributes('type')).toBe('password')
    await input.trigger('blur')
    expect(ruleFetchProxyApi.update).not.toHaveBeenCalled()
    await input.setValue(' http://192.168.0.3:7890 ')
    await input.trigger('blur')
    await flushPromises()
    expect(ruleFetchProxyApi.update).toHaveBeenCalledWith({ rule_fetch_proxy: 'http://192.168.0.3:7890' })
    expect(input.element.value).toBe('http://192.168.0.3:7890')
    expect(notify.success).toHaveBeenCalledWith('规则下载代理已保存')
  })

  it('persists an empty setting so later downloads use the existing network', async () => {
    const wrapper = await render()
    const input = wrapper.get('#rule-fetch-proxy')
    await input.setValue('')
    await input.trigger('blur')
    await flushPromises()
    expect(ruleFetchProxyApi.update).toHaveBeenCalledWith({ rule_fetch_proxy: '' })
    expect(notify.success).toHaveBeenCalledWith('规则下载代理已清除，将使用现有网络')
  })

  it('reports invalid input without logging or showing credentials and permits retry', async () => {
    const wrapper = await render()
    const input = wrapper.get('#rule-fetch-proxy')
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    vi.mocked(ruleFetchProxyApi.update).mockRejectedValueOnce({
      isAxiosError: true,
      config: { data: { rule_fetch_proxy: proxy } },
      response: { status: 400, data: { message: `Rejected ${proxy}` } }
    })
    await input.setValue(`${proxy}/invalid`)
    await input.trigger('blur')
    await flushPromises()
    expect(notify.error).toHaveBeenCalledWith('代理地址无效，请填写 HTTP/HTTPS 代理地址或留空')
    expect(notify.success).not.toHaveBeenCalled()
    expect(consoleError).not.toHaveBeenCalled()
    await input.setValue('http://192.168.0.3:7890')
    await input.trigger('blur')
    await flushPromises()
    expect(notify.success).toHaveBeenCalledWith('规则下载代理已保存')
  })
})
