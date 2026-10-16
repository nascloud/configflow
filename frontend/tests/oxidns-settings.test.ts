import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { load as loadYaml } from 'js-yaml'
import OxidnsSettings from '@/components/generate/OxidnsSettings.vue'
import OxidnsFields from '@/components/generate/OxidnsFields.vue'
import { oxidnsApi, ruleApi, ruleSetApi } from '@/api'
import { notify } from '@/lib/feedback'
import { featureGroups } from '@/lib/oxidns'

vi.mock('@/api', () => ({
  oxidnsApi: { getSettings: vi.fn(), saveSettings: vi.fn(), render: vi.fn() },
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
  vi.mocked(oxidnsApi.getSettings).mockImplementation(async () => ({ data: settings() }) as any)
  vi.mocked(oxidnsApi.saveSettings).mockResolvedValue({ data: { success: true } } as any)
  vi.mocked(oxidnsApi.render).mockResolvedValue({ data: { config: 'runtime: {worker_threads: 5}\nplugins: [{tag: main, type: sequence, args: [{exec: reject 0}]}]' } } as any)
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
function button(text: string, scope: ParentNode = document) {
  const button = Array.from(scope.querySelectorAll('button')).find(button => button.textContent?.trim() === text)
  expect(button, text).toBeDefined()
  return button!
}
async function click(text: string, scope: ParentNode = document) {
  const button = Array.from(scope.querySelectorAll('button')).find(button => button.textContent?.trim() === text)
  expect(button, text).toBeDefined()
  button!.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, button: 0 }))
  button!.click()
  await flushPromises()
}
async function input(selector: string, value: string) {
  const element = document.querySelector(selector) as HTMLInputElement | HTMLTextAreaElement
  expect(element, selector).not.toBeNull()
  element.value = value
  element.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}
const matchEditor = () => document.querySelector('[data-testid="oxidns-match-editor"]')!
const entryEditor = (label: string) => document.querySelector(`section[aria-label="${label}"]`)!
const savedPayload = () => vi.mocked(oxidnsApi.saveSettings).mock.calls.at(-1)?.[0] as Record<string, any>

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

  it('shows feature help while collapsed without expanding or enabling the feature', async () => {
    await render()
    await click('特色功能')
    for (const feature of featureGroups) {
      expect(document.querySelector(`button[aria-label="${feature.title}说明"]`)).not.toBeNull()
    }
    const trigger = document.querySelector('button[aria-label="查询记录设置"]') as HTMLButtonElement
    const help = document.querySelector('button[aria-label="查询记录说明"]') as HTMLButtonElement
    const toggle = document.getElementById('oxi-enable-query_recorder') as HTMLButtonElement
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(trigger.contains(help)).toBe(false)
    help.click()
    await flushPromises()
    expect(document.querySelector('[data-field-help-content]')?.textContent).toContain('SQLite 查询历史')
    expect(trigger.getAttribute('aria-expanded')).toBe('false')
    expect(toggle.getAttribute('aria-checked')).toBe('false')
    trigger.click()
    await flushPromises()
    expect(trigger.getAttribute('aria-expanded')).toBe('true')
    expect(toggle.getAttribute('aria-checked')).toBe('false')
  })

  it('blocks malformed custom matches, retains the draft across tabs, then saves repaired values', async () => {
    await render()
    await click('高级')
    await click('YAML', matchEditor())
    await input('#oxi-custom-matches', 'unexpected: mapping')
    expect(button('保存设置').disabled).toBe(true)
    await click('保存设置')
    expect(oxidnsApi.saveSettings).not.toHaveBeenCalled()
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('YAML 列表')
    await click('缓存')
    expect(button('保存设置').disabled).toBe(true)
    await click('高级')
    expect((document.getElementById('oxi-custom-matches') as HTMLTextAreaElement).value).toBe('unexpected: mapping')
    await input('#oxi-custom-matches', '- matches: qtype 1\n  exec: goto china_dns\n  enabled: false\n  extension: {keep: true}')
    expect(button('保存设置').disabled).toBe(false)
    await click('保存设置')
    expect(savedPayload().custom_matches).toEqual([{ matches: 'qtype 1', exec: 'goto china_dns', enabled: false, extension: { keep: true } }])
  })

  it('edits list entries and represents RouterOS TLS as an optional object', async () => {
    const model: Record<string, any> = { urls: [], ros: { args: {} } }
    const wrapper = mount(OxidnsFields, { props: { settings: model, fields: [
      { path: 'urls', label: 'URLs', type: 'list' }, { path: 'ros.args.tls', label: 'TLS', type: 'tls' }
    ] } })
    wrappers.push(wrapper)
    const add = () => wrapper.findAll('button').find(button => button.text().trim() === '添加条目')!
    await add().trigger('click')
    await wrapper.get('[aria-label="URLs第 1 项"]').setValue('https://example.test/a')
    await add().trigger('click')
    await wrapper.get('[aria-label="URLs第 2 项"]').setValue('https://example.test/b')
    expect(model.urls).toEqual(['https://example.test/a', 'https://example.test/b'])
    await wrapper.get('[role="switch"]').trigger('click')
    expect(model.ros.args.tls).toEqual({})
    await wrapper.get('[role="switch"]').trigger('click')
    expect('tls' in model.ros.args).toBe(false)
  })

  it('validates upstream YAML across tabs and saves all complete upstream parameters after repair', async () => {
    await render()
    await click('DNS 上游')
    await click('YAML', entryEditor('直连 DNS'))
    await input('#oxi-local_dns-yaml', '- addr: [broken')
    expect(button('保存设置').disabled).toBe(true)
    await click('核心模板')
    await click('DNS 上游')
    expect((document.getElementById('oxi-local_dns-yaml') as HTMLTextAreaElement).value).toBe('- addr: [broken')
    expect(button('保存设置').disabled).toBe(true)
    await input('#oxi-local_dns-yaml', '- addr: https://dns.example.test/query\n  bootstrap: 192.0.2.1\n  enable_pipeline: false\n  extension: {nested: [1, true]}')
    await click('保存设置')
    expect(loadYaml(savedPayload().local_dns)).toEqual([{ addr: 'https://dns.example.test/query', bootstrap: '192.0.2.1', enable_pipeline: false, extension: { nested: [1, true] } }])
    expect(savedPayload().remote_dns).toBe('192.0.2.54')
  })

  it('keeps multiple editor errors independent until every draft is repaired', async () => {
    await render()
    await click('高级')
    await click('YAML', matchEditor())
    await input('#oxi-custom-matches', 'invalid: mapping')
    await click('YAML', entryEditor('基础配置扩展'))
    await input('#oxi-custom-config', 'plugins: [broken')
    await input('#oxi-custom-matches', '[]')
    expect(button('保存设置').disabled).toBe(true)
    await click('WebUI / 日志')
    await click('高级')
    expect((document.getElementById('oxi-custom-config') as HTMLTextAreaElement).value).toBe('plugins: [broken')
    await input('#oxi-custom-config', 'extra_setting: {keep: [1, false]}')
    expect(button('保存设置').disabled).toBe(false)
    await click('保存设置')
    expect(loadYaml(savedPayload().custom_config)).toEqual({ extra_setting: { keep: [1, false] } })
    expect(savedPayload().custom_matches).toEqual([])
  })

  it('resets invalid drafts and errors when switching profiles without leaking changes', async () => {
    const profileB = { ...settings(), worker_threads: 3, local_dns: '192.0.2.99', custom_matches: [{ matches: ['qtype 28'], exec: 'accept', enabled: false }] }
    vi.mocked(oxidnsApi.getSettings).mockImplementation(async (profileId) => ({ data: profileId === 'other-profile' ? structuredClone(profileB) : settings() }) as any)
    const wrapper = await render()
    await click('高级')
    await click('YAML', matchEditor())
    await input('#oxi-custom-matches', '- matches: [broken')
    expect(button('保存设置').disabled).toBe(true)
    await wrapper.setProps({ profileId: 'other-profile' })
    await flushPromises()
    expect(oxidnsApi.getSettings).toHaveBeenLastCalledWith('other-profile')
    expect(document.getElementById('oxi-custom-matches')).toBeNull()
    expect((document.querySelector('[aria-label="匹配项 1 条件 1"]') as HTMLTextAreaElement).value).toBe('qtype 28')
    expect(button('保存设置').disabled).toBe(false)
    await click('保存设置')
    expect(oxidnsApi.saveSettings).toHaveBeenLastCalledWith(expect.objectContaining({ worker_threads: 3, local_dns: '192.0.2.99', custom_matches: profileB.custom_matches }), 'other-profile')
  })

  it('discards unsaved edits and invalid drafts after cancel and reopening', async () => {
    const wrapper = await render()
    await input('#oxi-worker_threads', '5')
    await click('高级')
    await click('YAML', entryEditor('基础配置扩展'))
    await input('#oxi-custom-config', 'plugins: [broken')
    expect(button('保存设置').disabled).toBe(true)
    await click('取消')
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])
    await wrapper.setProps({ open: false })
    await flushPromises()
    await wrapper.setProps({ open: true })
    await flushPromises()
    expect(oxidnsApi.getSettings).toHaveBeenCalledTimes(2)
    expect((document.getElementById('oxi-worker_threads') as HTMLInputElement).value).toBe('8')
    expect(document.getElementById('oxi-custom-config')).toBeNull()
    expect(button('保存设置').disabled).toBe(false)
    await click('保存设置')
    expect(savedPayload().custom_config).toBe('')
    expect(savedPayload().worker_threads).toBe(8)
  })

  it('initializes native mode from the unsaved template and retains both drafts across mode switches', async () => {
    await render()
    await input('#oxi-worker_threads', '5')
    await click('完整配置', document.querySelector('[aria-label="OxiDNS 配置方式"]')!)
    expect(oxidnsApi.render).toHaveBeenCalledWith(expect.objectContaining({ worker_threads: 5, config_mode: 'template', api: expect.objectContaining({ password: 'test-only-secret' }) }), 'oxi-profile')
    expect(document.querySelector('section[aria-label="完整 OxiDNS 配置"]')).not.toBeNull()
    await click('快捷模板')
    expect((document.getElementById('oxi-worker_threads') as HTMLInputElement).value).toBe('5')
    await click('完整配置', document.querySelector('[aria-label="OxiDNS 配置方式"]')!)
    expect(oxidnsApi.render).toHaveBeenCalledTimes(1)
    await click('保存设置')
    expect(savedPayload()).toEqual(expect.objectContaining({ config_mode: 'native', worker_threads: 5, native_config: expect.stringContaining('worker_threads: 5') }))
  })

  it('keeps template mode editable when full configuration rendering fails', async () => {
    vi.mocked(oxidnsApi.render).mockRejectedValue({ response: { data: { message: '规则文件缺失' } } })
    await render()
    await click('完整配置', document.querySelector('[aria-label="OxiDNS 配置方式"]')!)
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('规则文件缺失')
    expect(button('快捷模板').getAttribute('aria-pressed')).toBe('true')
    expect(button('保存设置').disabled).toBe(false)
  })

  it('saves attached text and binary files with native settings scoped to the current profile', async () => {
    vi.mocked(oxidnsApi.getSettings).mockResolvedValue({ data: { ...settings(), config_mode: 'native', native_config: 'plugins: [{tag: main, type: sequence, args: [{exec: reject 0}]}]', additional_files: [{ path: 'certs/example.pem', content: 'TEST CERT', encoding: 'text' }, { path: 'rules/example.dat', content: 'AP8=', encoding: 'base64' }] } } as any)
    await render()
    await click('附加文件')
    expect((document.querySelector('[aria-label="文件 1 路径"]') as HTMLInputElement).value).toBe('certs/example.pem')
    await click('保存设置')
    expect(savedPayload().additional_files).toEqual([{ path: 'certs/example.pem', content: 'TEST CERT', encoding: 'text' }, { path: 'rules/example.dat', content: 'AP8=', encoding: 'base64' }])
    expect(savedPayload().config_mode).toBe('native')
  })

  it('clears feature editor errors on disable and restores its last valid values on re-enable', async () => {
    const original = settings()
    original.adguard = { enabled: true, args: {}, urls: ['https://rules.example.test/original'] }
    vi.mocked(oxidnsApi.getSettings).mockResolvedValue({ data: original } as any)
    await render()
    await click('特色功能')
    const toggle = document.getElementById('oxi-enable-adguard') as HTMLButtonElement
    const feature = featureGroups.find(feature => feature.key === 'adguard')!
    const trigger = document.querySelector(`button[aria-label="${feature.title}设置"]`) as HTMLButtonElement
    trigger.click()
    await flushPromises()
    await click('YAML', entryEditor('规则订阅'))
    await input('[id="oxi-adguard.urls-yaml"]', '- https://rules.example.test/updated')
    await input('[id="oxi-adguard.urls-yaml"]', 'invalid: mapping')
    expect(button('保存设置').disabled).toBe(true)
    toggle.click()
    await flushPromises()
    expect(entryEditor('规则订阅')).toBeNull()
    expect(button('保存设置').disabled).toBe(false)
    toggle.click()
    await flushPromises()
    expect(document.getElementById('oxi-adguard.urls-yaml')).toBeNull()
    expect((document.querySelector('[aria-label="规则订阅第 1 项"]') as HTMLInputElement).value).toBe('https://rules.example.test/updated')
    expect(button('保存设置').disabled).toBe(false)
    await click('保存设置')
    expect(savedPayload().adguard).toEqual(expect.objectContaining({ enabled: true, urls: ['https://rules.example.test/updated'] }))
  })
})
