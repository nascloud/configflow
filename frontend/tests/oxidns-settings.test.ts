import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import OxidnsSettings from '@/components/generate/OxidnsSettings.vue'
import OxidnsFields from '@/components/generate/OxidnsFields.vue'
import { oxidnsApi, ruleApi, ruleSetApi } from '@/api'
import { notify } from '@/lib/feedback'
import { featureGroups } from '@/lib/oxidns'

vi.mock('@/api', () => ({
  oxidnsApi: { getSettings: vi.fn(), saveSettings: vi.fn() },
  ruleApi: { getAll: vi.fn() }, ruleSetApi: { getAll: vi.fn() }
}))
const wrappers: ReturnType<typeof mount>[] = []
const settings = () => ({
  worker_threads: 8, local_dns: '192.0.2.53', remote_dns: '192.0.2.54',
  direct_rules: ['direct'], proxy_rules: [], direct_rulesets: [], proxy_rulesets: [],
  custom_matches: [], custom_config: '', api: { enabled: true, username: 'reader', password: 'test-only-secret' },
  ...Object.fromEntries(featureGroups.map(feature => [feature.key, { enabled: false, args: {} }]))
})
beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(oxidnsApi.getSettings).mockResolvedValue({ data: settings() } as any)
  vi.mocked(oxidnsApi.saveSettings).mockResolvedValue({ data: { success: true } } as any)
  vi.mocked(ruleApi.getAll).mockResolvedValue({ data: [{ id: 'direct', name: 'Direct rule', itemType: 'rule' }] } as any)
  vi.mocked(ruleSetApi.getAll).mockResolvedValue({ data: [] } as any)
  vi.spyOn(notify, 'success').mockImplementation(() => 0)
  vi.spyOn(notify, 'error').mockImplementation(() => 0)
})
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})
async function render() {
  const wrapper = mount(OxidnsSettings, { attachTo: document.body, props: { open: true, profileId: 'oxi-profile' } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
async function click(text: string) {
  const button = Array.from(document.querySelectorAll('button')).find(button => button.textContent?.trim() === text)
  expect(button, text).toBeDefined()
  button!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }))
  button!.click()
  await flushPromises()
}

describe('OxiDNS profile settings', () => {
  it('loads and saves the chosen profile; applying the verified template preserves rules, upstreams and credentials', async () => {
    const wrapper = await render()
    expect(oxidnsApi.getSettings).toHaveBeenCalledWith('oxi-profile')
    expect((document.getElementById('oxi-worker_threads') as HTMLInputElement).value).toBe('8')
    await click('应用已验证模板')
    await click('保存设置')
    expect(oxidnsApi.saveSettings).toHaveBeenCalledWith(expect.objectContaining({
      worker_threads: 2, cache_lazy_ttl: 300, probe_cache_size: 2048,
      local_dns: '192.0.2.53', remote_dns: '192.0.2.54', direct_rules: ['direct'],
      api: { enabled: true, username: 'reader', password: 'test-only-secret' },
      ip_selector: { enabled: false, args: {} }
    }), 'oxi-profile')
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })

  it('shows every feature, masks credentials and keeps API errors editable', async () => {
    await render()
    await click('特色功能')
    for (const feature of featureGroups) expect(document.body.textContent).toContain(feature.title)
    await click('WebUI / 日志')
    expect(document.getElementById('oxi-api.password')?.getAttribute('type')).toBe('password')
    vi.mocked(oxidnsApi.saveSettings).mockRejectedValue({ response: { data: { message: 'API 用户名和密码必须同时设置' } } })
    await click('保存设置')
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('API 用户名和密码必须同时设置')
    expect((Array.from(document.querySelectorAll('button')).find(b => b.textContent?.trim() === '保存设置') as HTMLButtonElement).disabled).toBe(false)
  })

  it('rejects malformed custom matches before writing settings', async () => {
    await render()
    await click('高级')
    const editor = document.getElementById('oxi-custom-matches') as HTMLTextAreaElement
    editor.value = 'unexpected: mapping'
    editor.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    await click('保存设置')
    expect(oxidnsApi.saveSettings).not.toHaveBeenCalled()
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('YAML 列表')
  })

  it('keeps multi-line input editable and represents RouterOS TLS as an optional object', async () => {
    const model: Record<string, any> = { urls: [], ros: { args: {} } }
    const wrapper = mount(OxidnsFields, { props: { settings: model, fields: [
      { path: 'urls', label: 'URLs', type: 'list' }, { path: 'ros.args.tls', label: 'TLS', type: 'tls' }
    ] } })
    wrappers.push(wrapper)
    await wrapper.get('textarea').setValue('https://example.test/a\n')
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('https://example.test/a\n')
    await wrapper.get('textarea').setValue('https://example.test/a\nhttps://example.test/b')
    expect(model.urls).toEqual(['https://example.test/a', 'https://example.test/b'])
    await wrapper.get('[role="switch"]').trigger('click')
    expect(model.ros.args.tls).toEqual({})
    await wrapper.get('[role="switch"]').trigger('click')
    expect('tls' in model.ros.args).toBe(false)
  })
})
