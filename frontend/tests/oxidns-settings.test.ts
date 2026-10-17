import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { load as loadYaml, dump as dumpYaml } from 'js-yaml'
import OxidnsSettings from '@/components/generate/OxidnsSettings.vue'
import OxidnsNativeEditor from '@/components/generate/OxidnsNativeEditor.vue'
import OxidnsConfigNavigation from '@/components/generate/OxidnsConfigNavigation.vue'
import OxidnsCommonSettings from '@/components/generate/OxidnsCommonSettings.vue'
import { agentApi, profileApi, oxidnsApi, ruleApi, ruleSetApi } from '@/api'
import { confirmState, notify, settleConfirm } from '@/lib/feedback'
import SettingsWorkspace from '@/components/generate/SettingsWorkspace.vue'

vi.mock('@/api', () => ({
  agentApi: { getAll: vi.fn(), pushConfig: vi.fn(), getDeployment: vi.fn(), activateDeployment: vi.fn() },
  profileApi: { list: vi.fn() },
  oxidnsApi: { getSettings: vi.fn(), saveSettings: vi.fn(), prepareEditor: vi.fn(), readCommonSettings: vi.fn(), applyCommonSettings: vi.fn(), validateSettings: vi.fn() },
  ruleApi: { getAll: vi.fn() }, ruleSetApi: { getAll: vi.fn() }
}))
const wrappers: ReturnType<typeof mount>[] = []
const config = 'runtime: {worker_threads: 8, extension: false}\nplugins: [{tag: main, type: sequence, args: [{exec: reject 0}]}]\nunknown: {values: [0, false, null]}'
const projection = (settings: any) => ({ values: { worker_threads: (loadYaml(settings.native_config) as any)?.runtime?.worker_threads }, capabilities: {} })
const settings = () => ({ config_mode: 'native', native_config: config, additional_files: [], template_settings: { worker_threads: 2 } })
const publishGuard = { expected_profile_id: 'oxi-profile', expected_service_type: 'oxidns', expected_config_revision: 7 }
beforeEach(() => {
  settleConfirm('cancel')
  vi.clearAllMocks()
  vi.mocked(oxidnsApi.getSettings).mockImplementation(async () => ({ data: settings() }) as any)
  vi.mocked(oxidnsApi.validateSettings).mockImplementation(async (value: any) => {
    const doc = loadYaml(value.native_config) as any
    const issues: any[] = []
    if (doc?.runtime?.worker_threads === 0) issues.push({path: ['runtime', 'worker_threads'], message: '工作线程数必须大于 0。', code: 'minimum', severity: 'error'})
    doc?.plugins?.forEach((plugin: any, index: number) => {
      if (['http_server', 'quic_server'].includes(plugin.type) && !plugin.args?.listen) issues.push({path: ['plugins', index, 'args', 'listen'], message: '请填写监听地址。', code: 'required', severity: 'error'})
    })
    return {data: {valid: !issues.length, issues}} as any
  })
  vi.mocked(oxidnsApi.saveSettings).mockResolvedValue({ data: { success: true, publish_guard: publishGuard } } as any)
  vi.mocked(profileApi.list).mockResolvedValue({ data: [{ id: 'oxi-profile', name: 'DNS配置', is_default: true }] } as any)
  vi.mocked(agentApi.getAll).mockResolvedValue({ data: [{ id: 'dns', name: 'OxiDNS', profile_id: 'oxi-profile', service_type: 'oxidns', status: 'online' }] } as any)
  vi.mocked(agentApi.pushConfig).mockImplementation(async (_id, deploymentId) => ({ data: { success: true, deployment_id: deploymentId, status: 'verifying' } }) as any)
  vi.mocked(agentApi.getDeployment).mockImplementation(async (_id, deploymentId) => ({ data: { deployment_id: deploymentId, status: 'verifying' } }) as any)
  vi.mocked(oxidnsApi.prepareEditor).mockImplementation(async value => ({ data: { settings: structuredClone(value) } }) as any)
  vi.mocked(oxidnsApi.readCommonSettings).mockImplementation(async value => ({ data: projection(value) }) as any)
  vi.mocked(oxidnsApi.applyCommonSettings).mockImplementation(async (value: any, changes) => {
    const settings = JSON.parse(JSON.stringify(value))
    const document = loadYaml(settings.native_config) as any
    document.runtime ??= {}
    if ('worker_threads' in changes) document.runtime.worker_threads = changes.worker_threads
    settings.native_config = dumpYaml(document)
    return { data: { settings, ...projection(settings) } } as any
  })
  vi.mocked(ruleApi.getAll).mockResolvedValue({ data: [{ id: 'direct', name: 'Direct rule', itemType: 'rule' }] } as any)
  vi.mocked(ruleSetApi.getAll).mockResolvedValue({ data: [] } as any)
  vi.spyOn(notify, 'success').mockImplementation(() => 0)
  vi.spyOn(notify, 'error').mockImplementation(() => 0)
})
afterEach(() => {
  settleConfirm('cancel')
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
async function settleValidation() {
  if (document.querySelector('[aria-label="配置检查结果"]')?.textContent?.includes('正在检查')) {
    await new Promise(resolve => setTimeout(resolve, 330)); await flushPromises()
  }
}
async function input(selector: string, value: string) {
  const element = document.querySelector(selector) as HTMLInputElement | HTMLTextAreaElement
  expect(element, selector).not.toBeNull()
  element.value = value
  element.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
  if (selector === worker) { await new Promise(resolve => setTimeout(resolve, 180)); await flushPromises() }
  await settleValidation()
}
const savedPayload = () => vi.mocked(oxidnsApi.saveSettings).mock.calls.at(-1)?.[0] as Record<string, any>

const views = () => document.querySelector('[aria-label="配置编辑方式"]')!
const worker = '#oxi-worker_threads'
const yamlInput = '[aria-label="OxiDNS 完整配置 YAML"]'

describe('unified OxiDNS profile settings', () => {
  it('saves and publishes the returned version without closing the editor or marking unsaved changes', async () => {
    const wrapper = await render()
    await input(worker, '5')
    await click('保存并发布')
    expect(oxidnsApi.saveSettings).toHaveBeenCalledTimes(1)
    expect(agentApi.pushConfig).toHaveBeenCalledWith('dns', expect.any(String), publishGuard)
    expect(vi.mocked(oxidnsApi.saveSettings).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(agentApi.pushConfig).mock.invocationCallOrder[0])
    expect(wrapper.emitted('update:open')).toBeUndefined()
    expect(wrapper.findComponent(SettingsWorkspace).props('dirty')).toBe(false)
    expect(wrapper.findComponent(SettingsWorkspace).props('busy')).toBe(false)
    expect(document.body.textContent).toContain('草稿已保存')
    expect(document.body.textContent).not.toContain('发布成功')
  })

  it('retains the saved draft when publication validation rejects it', async () => {
    const wrapper = await render()
    await input(worker, '0')
    vi.mocked(agentApi.pushConfig).mockRejectedValueOnce({ response: { status: 400, data: { message: '工作线程数必须大于 0。' } } })
    await click('保存并发布')
    expect(agentApi.pushConfig).toHaveBeenCalledOnce()
    expect(document.body.textContent).toContain('草稿已保存，发布失败')
    expect(document.body.textContent).toContain('工作线程数必须大于 0。')
    expect(wrapper.emitted('update:open')).toBeUndefined()
    expect(wrapper.findComponent(SettingsWorkspace).props('dirty')).toBe(false)
    expect(savedPayload().native_config).toContain('worker_threads: 0')
  })

  it('never publishes if saving the draft fails', async () => {
    const wrapper = await render()
    await input(worker, '5')
    vi.mocked(oxidnsApi.saveSettings).mockRejectedValueOnce({ response: { data: { message: '保存暂时失败' } } })
    await click('保存并发布')
    expect(agentApi.pushConfig).not.toHaveBeenCalled()
    expect(wrapper.findComponent(SettingsWorkspace).props('dirty')).toBe(true)
    expect(document.body.textContent).toContain('保存暂时失败')
  })

  it('tracks real draft changes without treating editor navigation as unsaved edits', async () => {
    const wrapper = await render()
    const workspace = () => wrapper.findComponent(SettingsWorkspace)
    expect(workspace().props('dirty')).toBe(false)
    await click('完整参数', views())
    await click('YAML', views())
    expect(workspace().props('dirty')).toBe(false)
    await input(yamlInput, config + '\n# unsaved comment')
    expect(workspace().props('dirty')).toBe(true)
    await input(yamlInput, config)
    expect(workspace().props('dirty')).toBe(false)
    expect(oxidnsApi.saveSettings).not.toHaveBeenCalled()
  })

  it('keeps unfinished invalid form input protected when it cannot be synchronized', async () => {
    const wrapper = await render()
    await input(worker, '')
    expect(button('保存草稿').disabled).toBe(true)
    expect(wrapper.findComponent(SettingsWorkspace).props('dirty')).toBe(true)
    ;(document.querySelector('[aria-label="返回生成工作台"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(confirmState.open).toBe(true)
    settleConfirm('cancel')
    await flushPromises()
    expect(wrapper.emitted('update:open')).toBeUndefined()
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('')
  })

  it('edits one document in all views and saves once to the chosen profile', async () => {
    const wrapper = await render()
    expect(oxidnsApi.prepareEditor).toHaveBeenCalledWith(settings(), 'oxi-profile')
    expect(button('常用设置', views()).getAttribute('aria-pressed')).toBe('true')
    await input(worker, '5')
    await click('完整参数', views())
    await click('YAML', views())
    const text = (document.querySelector(yamlInput) as HTMLTextAreaElement).value
    expect((loadYaml(text) as any).runtime.worker_threads).toBe(5)
    await input(yamlInput, text.replace('worker_threads: 5', 'worker_threads: 6'))
    await click('常用设置', views())
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('6')
    expect(oxidnsApi.saveSettings).not.toHaveBeenCalled()
    await click('保存草稿')
    expect(agentApi.pushConfig).not.toHaveBeenCalled()
    expect(oxidnsApi.saveSettings).toHaveBeenCalledTimes(1)
    expect(oxidnsApi.saveSettings).toHaveBeenLastCalledWith(expect.objectContaining({ config_mode: 'native' }), 'oxi-profile')
    expect(loadYaml(savedPayload().native_config)).toEqual({ ...(loadYaml(config) as any), runtime: { worker_threads: 6, extension: false } })
    expect(savedPayload().template_settings).toEqual({ worker_threads: 2 })
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })

  it('prepares the active legacy template and retains the inactive native backup', async () => {
    const dormant = 'plugins: [{tag: empty_http, type: http_server, args: {}}]'
    const legacy = { config_mode: 'template', native_config: dormant, worker_threads: 8 }
    vi.mocked(oxidnsApi.getSettings).mockResolvedValue({ data: legacy } as any)
    vi.mocked(oxidnsApi.prepareEditor).mockResolvedValue({ data: { settings: { ...settings(), legacy_native_config: dormant } } } as any)
    await render()
    expect(oxidnsApi.prepareEditor).toHaveBeenCalledWith(legacy, 'oxi-profile')
    expect(document.querySelector('[aria-label="旧草稿备份说明"]')).not.toBeNull()
    expect(button('保存草稿').disabled).toBe(false)
    expect(oxidnsApi.saveSettings).not.toHaveBeenCalled()
    await click('保存草稿')
    expect(savedPayload().legacy_native_config).toBe(dormant)
    expect(savedPayload().native_config).toBe(config)
  })

  it.each(['load', 'prepare'])('blocks saving on %s failure and allows retry', async stage => {
    const api = stage === 'load' ? oxidnsApi.getSettings : oxidnsApi.prepareEditor
    vi.mocked(api).mockRejectedValueOnce({ response: { data: { message: '配置加载失败' } } })
    await render()
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('配置加载失败')
    expect(button('保存草稿').disabled).toBe(true)
    await click('重新加载')
    expect(button('保存草稿').disabled).toBe(false)
  })

  it.each(['http_server', 'quic_server'])('reports incomplete %s while allowing draft storage', async type => {
    const wrapper = await render()
    await click('完整参数', views())
    wrapper.getComponent(OxidnsNativeEditor).getComponent(OxidnsConfigNavigation).vm.$emit('select', `add:plugin:${type}`)
    await flushPromises(); await settleValidation()
    expect(button('保存草稿').disabled).toBe(false)
    expect(document.body.textContent).toContain('必填 · 待填写')
    expect(document.querySelector('[aria-label="配置检查问题"]')?.textContent).toContain('请填写监听地址')
    expect(document.body.textContent).toContain('发布前需要修正')
    expect(oxidnsApi.saveSettings).not.toHaveBeenCalled()
    await click('YAML', views())
    const args = type === 'http_server'
      ? '{listen: "127.0.0.1:8053", entries: [{path: /dns-query, exec: main}]}'
      : '{listen: "127.0.0.1:8853", entry: main, cert: cert.pem, key: key.pem}'
    await input(yamlInput, `plugins: [{tag: main, type: sequence, args: [{exec: reject 0}]}, {tag: ${type}, type: ${type}, args: ${args}}]`)
    expect(button('保存草稿').disabled).toBe(false)
    await click('保存草稿')
    expect((loadYaml(savedPayload().native_config) as any).plugins[1].args.listen).toMatch(/^127/)
  })

  it('retains valid empty reverse_lookup args with default help', async () => {
    const wrapper = await render()
    await click('完整参数', views())
    wrapper.getComponent(OxidnsNativeEditor).getComponent(OxidnsConfigNavigation).vm.$emit('select', 'add:plugin:reverse_lookup')
    await flushPromises(); await settleValidation()
    expect(document.body.textContent).toContain('使用默认参数')
    expect(document.querySelector('[aria-label="反向查询缓存默认参数说明"]')).not.toBeNull()
    expect(button('保存草稿').disabled).toBe(false)
    await click('保存草稿')
    expect((loadYaml(savedPayload().native_config) as any).plugins.at(-1)).toEqual({ tag: 'reverse_lookup', type: 'reverse_lookup', args: {} })
  })

  it('preserves attached files and unknown values through panel changes', async () => {
    const files = [{ path: 'certs/example.pem', content: 'TEST CERT', encoding: 'text' }, { path: 'rules/example.dat', content: 'AP8=', encoding: 'base64' }]
    vi.mocked(oxidnsApi.getSettings).mockResolvedValue({ data: { ...settings(), additional_files: files, extension_settings: { keep: false } } } as any)
    await render()
    await click('附加文件')
    expect((document.querySelector('[aria-label="文件 1 路径"]') as HTMLInputElement).value).toBe('certs/example.pem')
    await click('配置')
    await click('保存草稿')
    expect(savedPayload().additional_files).toEqual(files)
    expect(savedPayload().extension_settings).toEqual({ keep: false })
    expect(loadYaml(savedPayload().native_config)).toEqual(loadYaml(config))
  })

  it('removes the template tool and applies common changes to the shared draft without saving', async () => {
    const wrapper = await render()
    expect(document.querySelector('[aria-label="模板工具"]')).toBeNull()
    wrapper.getComponent(OxidnsCommonSettings).vm.$emit('change', { worker_threads: 3 })
    await new Promise(resolve => setTimeout(resolve, 180)); await flushPromises(); await settleValidation()
    expect(oxidnsApi.applyCommonSettings).toHaveBeenCalledWith(expect.objectContaining({ native_config: config }), { worker_threads: 3 }, 'oxi-profile')
    expect(oxidnsApi.saveSettings).not.toHaveBeenCalled()
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('3')
    await click('保存草稿')
    expect((loadYaml(savedPayload().native_config) as any).runtime.worker_threads).toBe(3)
    expect(savedPayload().template_settings).toEqual({ worker_threads: 2 })
  })

  it('orders rapid edits and blocks saving until the queue is drained', async () => {
    await render()
    let resolveFirst!: (value: any) => void
    vi.mocked(oxidnsApi.applyCommonSettings).mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve }))
    await input(worker, '3')
    expect(button('保存草稿').disabled).toBe(true)
    expect(button('YAML', views()).disabled).toBe(true)
    await input(worker, '4')
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('4')
    const first = { ...settings(), native_config: config.replace('worker_threads: 8', 'worker_threads: 3') }
    resolveFirst({ data: { settings: first, ...projection(first) } }); await flushPromises(); await settleValidation()
    expect(oxidnsApi.applyCommonSettings).toHaveBeenCalledTimes(2)
    expect(vi.mocked(oxidnsApi.applyCommonSettings).mock.calls[1][0]).toEqual(first)
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('4')
    expect(button('保存草稿').disabled).toBe(false)
    await click('保存草稿')
    expect((loadYaml(savedPayload().native_config) as any).runtime.worker_threads).toBe(4)
  })

  it('protects an unsaved common edit while its draft synchronization is still in flight', async () => {
    const wrapper = await render()
    let resolveApply!: (value: any) => void
    vi.mocked(oxidnsApi.applyCommonSettings).mockImplementationOnce(() => new Promise(resolve => { resolveApply = resolve }))
    await input(worker, '3')
    expect(oxidnsApi.applyCommonSettings).toHaveBeenCalledTimes(1)
    expect(wrapper.findComponent(OxidnsNativeEditor).props('modelValue')).toBe(config)
    expect(wrapper.findComponent(SettingsWorkspace).props('dirty')).toBe(true)
    ;(document.querySelector('[aria-label="返回生成工作台"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(confirmState.open).toBe(true)
    settleConfirm('cancel')
    await flushPromises()
    expect(wrapper.emitted('update:open')).toBeUndefined()
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('3')

    const applied = { ...settings(), native_config: config.replace('worker_threads: 8', 'worker_threads: 3') }
    resolveApply({ data: { settings: applied, ...projection(applied) } })
    await flushPromises()
    await settleValidation()
    expect(wrapper.findComponent(SettingsWorkspace).props('dirty')).toBe(true)
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('3')
    expect(oxidnsApi.saveSettings).not.toHaveBeenCalled()
    await click('保存草稿')
    expect((loadYaml(savedPayload().native_config) as any).runtime.worker_threads).toBe(3)
  })

  it('keeps TLS controls and the saved draft consistent after off/on clicks share one patch', async () => {
    const tls = {server_name: 'router.example.test', ca: 'certs/router-ca.pem', insecure: false}
    const args = {address: '192.0.2.1:8729', tls}
    const doc = loadYaml(config) as any
    doc.plugins.push({tag: 'ros_route', type: 'ros_route', args})
    const original = {...settings(), native_config: dumpYaml(doc)}
    const common = () => ({values: {...projection(original).values, ros_route: {enabled: true, args: structuredClone(args)}}, capabilities: {}})
    vi.mocked(oxidnsApi.getSettings).mockResolvedValue({data: original} as any)
    vi.mocked(oxidnsApi.readCommonSettings).mockImplementation(async () => ({data: common()}) as any)
    vi.mocked(oxidnsApi.applyCommonSettings).mockImplementationOnce(async value => ({data: {settings: JSON.parse(JSON.stringify(value)), ...common()}}) as any)
    await render()
    await click('特色功能')
    ;(document.querySelector('[aria-label="RouterOS 主机路由设置"]') as HTMLButtonElement).click()
    await flushPromises()
    const toggle = () => document.getElementById('oxi-ros_route.args.tls') as HTMLButtonElement
    toggle().click(); await flushPromises()
    toggle().click(); await flushPromises()
    await new Promise(resolve => setTimeout(resolve, 180)); await flushPromises(); await settleValidation()
    expect(oxidnsApi.applyCommonSettings).toHaveBeenCalledTimes(1)
    expect(oxidnsApi.applyCommonSettings).toHaveBeenCalledWith(expect.anything(), {'ros_route.args.tls': {}}, 'oxi-profile')
    expect((document.getElementById('oxi-ros_route.args.tls.server_name') as HTMLInputElement).value).toBe(tls.server_name)
    expect((document.getElementById('oxi-ros_route.args.tls.ca') as HTMLInputElement).value).toBe(tls.ca)
    await click('保存草稿')
    expect((loadYaml(savedPayload().native_config) as any).plugins.find((plugin: any) => plugin.tag === 'ros_route').args.tls).toEqual(tls)
  })

  it('retains invalid drafts without turning an empty number into zero', async () => {
    await render()
    await input(worker, '')
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('')
    expect(button('保存草稿').disabled).toBe(true)
    expect(oxidnsApi.applyCommonSettings).not.toHaveBeenCalled()
    await input(worker, '3')
    expect(button('保存草稿').disabled).toBe(false)
    expect(vi.mocked(oxidnsApi.applyCommonSettings).mock.calls[0][1]).toEqual({ worker_threads: 3 })
  })

  it('retains failed changes until retry without saving stale values', async () => {
    await render()
    vi.mocked(oxidnsApi.applyCommonSettings).mockRejectedValueOnce({ response: { data: { message: '当前结构无法自动修改' } } })
    await input(worker, '3')
    expect(document.body.textContent).toContain('当前结构无法自动修改')
    expect(button('保存草稿').disabled).toBe(true)
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('3')
    await click('重试同步'); await settleValidation()
    expect(button('保存草稿').disabled).toBe(false)
    await click('YAML', views())
    expect((loadYaml((document.querySelector(yamlInput) as HTMLTextAreaElement).value) as any).runtime.worker_threads).toBe(3)
  })

  it('discards only unsynchronized edits and restores the actual values', async () => {
    await render()
    await input(worker, '4')
    vi.mocked(oxidnsApi.applyCommonSettings).mockRejectedValueOnce(new Error('Unavailable'))
    await input(worker, '5')
    await click('撤销未同步修改')
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('4')
    expect(button('保存草稿').disabled).toBe(false)
    await click('保存草稿')
    expect((loadYaml(savedPayload().native_config) as any).runtime.worker_threads).toBe(4)
  })

  it('ignores a stale apply response after changing profile', async () => {
    const wrapper = await render()
    let resolveFirst!: (value: any) => void
    vi.mocked(oxidnsApi.applyCommonSettings).mockImplementationOnce(() => new Promise(resolve => { resolveFirst = resolve }))
    await input(worker, '3')
    vi.mocked(oxidnsApi.getSettings).mockResolvedValue({ data: { ...settings(), native_config: config.replace('worker_threads: 8', 'worker_threads: 6') } } as any)
    await wrapper.setProps({ profileId: 'next-profile' }); await flushPromises()
    const first = { ...settings(), native_config: config.replace('worker_threads: 8', 'worker_threads: 3') }
    resolveFirst({ data: { settings: first, ...projection(first) } }); await flushPromises(); await settleValidation()
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('6')
    await click('保存草稿')
    expect(oxidnsApi.saveSettings).toHaveBeenLastCalledWith(expect.objectContaining({ native_config: expect.stringContaining('worker_threads: 6') }), 'next-profile')
  })

  it('allows complete editing when projection fails and retries common settings', async () => {
    vi.mocked(oxidnsApi.readCommonSettings).mockRejectedValueOnce(new Error('Unavailable'))
    await render()
    expect(document.body.textContent).toContain('读取常用设置失败')
    await click('完整参数', views())
    expect(button('完整参数', views()).getAttribute('aria-pressed')).toBe('true')
    await click('常用设置', views())
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('8')
  })

  it('keeps edits on save failure and discards them after cancel/reopen', async () => {
    const wrapper = await render()
    await input(worker, '5')
    vi.mocked(oxidnsApi.saveSettings).mockRejectedValueOnce({ response: { data: { message: '保存暂时失败' } } })
    await click('保存草稿')
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('保存暂时失败')
    expect(button('保存草稿').disabled).toBe(false)
    ;(document.querySelector('[aria-label="返回生成工作台"]') as HTMLButtonElement).click()
    await flushPromises()
    expect(confirmState.open).toBe(true)
    settleConfirm('cancel')
    await flushPromises()
    expect(wrapper.emitted('update:open')).toBeUndefined()
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('5')
    ;(document.querySelector('[aria-label="返回生成工作台"]') as HTMLButtonElement).click()
    await flushPromises()
    settleConfirm('confirm')
    await flushPromises()
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false])
    await wrapper.setProps({ open: false })
    await wrapper.setProps({ open: true })
    await flushPromises()
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('8')
    expect(oxidnsApi.getSettings).toHaveBeenCalledTimes(2)
  })

  it.each(['plugins: [broken', '[1, 2]'])('blocks malformed or non-object draft %s and clears it across profiles', async malformed => {
    const wrapper = await render()
    await click('YAML', views())
    await input(yamlInput, malformed)
    expect(button('保存草稿').disabled).toBe(true)
    vi.mocked(oxidnsApi.getSettings).mockResolvedValue({ data: { ...settings(), native_config: config.replace('worker_threads: 8', 'worker_threads: 4') } } as any)
    await wrapper.setProps({ profileId: 'other-profile' })
    await flushPromises()
    expect(button('保存草稿').disabled).toBe(false)
    await click('保存草稿')
    expect(oxidnsApi.saveSettings).toHaveBeenLastCalledWith(expect.objectContaining({ native_config: expect.stringContaining('worker_threads: 4') }), 'other-profile')
  })


  it('shows the same server parameter errors in all views without preventing draft storage', async () => {
    const wrapper = await render()
    await input(worker, '0')
    expect(oxidnsApi.applyCommonSettings).toHaveBeenLastCalledWith(expect.anything(), {worker_threads: 0}, 'oxi-profile')
    expect((document.querySelector(worker) as HTMLInputElement).value).toBe('0')
    expect(document.body.textContent).toContain('工作线程数必须大于 0')
    expect(button('保存草稿').disabled).toBe(false)
    document.querySelector<HTMLButtonElement>('[aria-label="定位错误 1"]')!.click(); await flushPromises()
    expect(button('完整参数', views()).getAttribute('aria-pressed')).toBe('true')
    await input('[aria-label="全局参数.runtime.worker_threads 值"]', '4')
    expect(document.body.textContent).not.toContain('工作线程数必须大于 0')
    await input('[aria-label="全局参数.runtime.worker_threads 值"]', '0')
    expect(document.body.textContent).toContain('工作线程数必须大于 0')
    await click('YAML', views())
    const text = (document.querySelector(yamlInput) as HTMLTextAreaElement).value
    await input(yamlInput, text.replace('worker_threads: 0', 'worker_threads: 3'))
    expect(document.body.textContent).not.toContain('工作线程数必须大于 0')
    await input(yamlInput, text)
    expect(document.body.textContent).toContain('工作线程数必须大于 0')
    expect(button('保存草稿').disabled).toBe(false)
    await click('保存草稿')
    expect(savedPayload().native_config).toContain('worker_threads: 0')
    expect(wrapper.emitted('saved')).toHaveLength(1)
  })

  it('allows offline draft storage when validation is unavailable or malformed and supports retry', async () => {
    vi.mocked(oxidnsApi.validateSettings).mockRejectedValueOnce(new Error('校验服务不可用'))
    await render()
    expect(document.body.textContent).toContain('校验服务不可用')
    expect(document.body.textContent).toContain('可先保存草稿')
    expect(button('保存草稿').disabled).toBe(false)
    vi.mocked(oxidnsApi.validateSettings).mockResolvedValueOnce({data: {valid: true}} as any)
    await click('重新检查')
    expect(button('保存草稿').disabled).toBe(false)
    expect(document.body.textContent).toContain('无效结果')
    await click('重新检查')
    expect(document.body.textContent).toContain('规则检查通过')
    await click('保存草稿')
    expect(oxidnsApi.saveSettings).toHaveBeenCalledTimes(1)
  })

  it('saves without waiting for a slow validator or triggering an extra check', async () => {
    let resolveValidation!: (value: any) => void
    vi.mocked(oxidnsApi.validateSettings).mockImplementationOnce(() => new Promise(resolve => {resolveValidation = resolve}))
    const wrapper = await render()
    expect(document.body.textContent).toContain('正在检查草稿参数')
    expect(button('保存草稿').disabled).toBe(false)
    await click('保存草稿')
    expect(oxidnsApi.validateSettings).toHaveBeenCalledTimes(1)
    expect(oxidnsApi.saveSettings).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('saved')).toHaveLength(1)
    await wrapper.setProps({open: false})
    resolveValidation({data: {valid: false, issues: [{path: [], code: 'stale', message: '关闭后的错误', severity: 'error'}]}}); await flushPromises()
    expect(document.body.textContent).not.toContain('关闭后的错误')
  })

  it('shows warning scope and keeps valid empty/default values unchanged', async () => {
    const native = 'plugins: [{tag: reverse, type: reverse_lookup, args: {}}, {tag: cache, type: cache, args: null}]\nunknown: {empty: "", nil: null, zero: 0, off: false}\n'
    vi.mocked(oxidnsApi.getSettings).mockResolvedValueOnce({data: {...settings(), native_config: native}} as any)
    vi.mocked(oxidnsApi.validateSettings).mockResolvedValueOnce({data: {valid: true, issues: [{path: ['unknown'], code: 'unknown_field', message: '扩展字段由目标核心验证。', severity: 'warning'}]}} as any)
    await render()
    expect(document.body.textContent).toContain('规则检查通过，存在待目标环境验证项')
    expect(button('保存草稿').disabled).toBe(false)
    await click('保存草稿')
    expect(savedPayload().native_config).toBe(native)
  })

  it('ignores an older validation result after a newer edit was checked', async () => {
    await render()
    await click('YAML', views())
    let resolveFirst!: (value: any) => void
    vi.mocked(oxidnsApi.validateSettings).mockImplementationOnce(() => new Promise(resolve => {resolveFirst = resolve}))
    await input(yamlInput, config.replace('worker_threads: 8', 'worker_threads: 3'))
    expect(button('保存草稿').disabled).toBe(false)
    await input(yamlInput, config.replace('worker_threads: 8', 'worker_threads: 4'))
    expect(document.body.textContent).toContain('规则检查通过')
    resolveFirst({data: {valid: false, issues: [{path: ['runtime'], code: 'stale', message: '过期检查', severity: 'error'}]}}); await flushPromises()
    expect(document.body.textContent).not.toContain('过期检查')
    await click('保存草稿')
    expect(savedPayload().native_config).toContain('worker_threads: 4')
  })

  it('ignores validation results from a previous profile or cancelled dialog', async () => {
    let resolveFirst!: (value: any) => void
    vi.mocked(oxidnsApi.validateSettings).mockImplementationOnce(() => new Promise(resolve => {resolveFirst = resolve}))
    const wrapper = await render()
    await wrapper.setProps({profileId: 'next-profile'}); await flushPromises()
    resolveFirst({data: {valid: false, issues: [{path: [], code: 'stale', message: '其他空间检查', severity: 'error'}]}}); await flushPromises()
    expect(document.body.textContent).not.toContain('其他空间检查')
    let resolveSecond!: (value: any) => void
    vi.mocked(oxidnsApi.validateSettings).mockImplementationOnce(() => new Promise(resolve => {resolveSecond = resolve}))
    await click('YAML', views())
    await input(yamlInput, config.replace('worker_threads: 8', 'worker_threads: 3'))
    await wrapper.setProps({open: false})
    resolveSecond({data: {valid: false, issues: [{path: [], code: 'stale', message: '已取消检查', severity: 'error'}]}}); await flushPromises()
    await wrapper.setProps({open: true}); await flushPromises()
    expect(document.body.textContent).not.toContain('已取消检查')
    expect(button('保存草稿').disabled).toBe(false)
    expect(oxidnsApi.saveSettings).not.toHaveBeenCalled()
  })

  it('checks attached-file changes and locates include diagnostics in the files panel', async () => {
    const file = {path: 'plugins/cache.yaml', content: 'plugins: []', encoding: 'text'}
    vi.mocked(oxidnsApi.getSettings).mockResolvedValueOnce({data: {...settings(), additional_files: [file]}} as any)
    vi.mocked(oxidnsApi.validateSettings).mockResolvedValueOnce({data: {valid: false, issues: [{path: ['plugins', 0, 'args', 'size'], source: file.path, code: 'minimum', message: '缓存容量不能小于零。', severity: 'error'}]}} as any)
    await render()
    document.querySelector<HTMLButtonElement>('[aria-label="定位错误 1"]')!.click(); await flushPromises()
    expect(button('附加文件').getAttribute('aria-pressed')).toBe('true')
    expect(document.activeElement?.getAttribute('aria-label')).toBe('文件 1 路径')
    await input('[aria-label="文件 1 路径"]', 'plugins/updated.yaml')
    expect(vi.mocked(oxidnsApi.validateSettings).mock.calls.at(-1)?.[0]).toMatchObject({additional_files: [{...file, path: 'plugins/updated.yaml'}]})
    expect(button('保存草稿').disabled).toBe(false)
  })

  it('shows structural save-endpoint issues while retaining the editable draft', async () => {
    await render()
    vi.mocked(oxidnsApi.saveSettings).mockRejectedValueOnce({response: {data: {message: '配置结构检查未通过。', issues: [{path: ['include'], code: 'invalid_structure', message: '附加配置结构无效。', severity: 'error'}]}}})
    await click('保存草稿')
    expect(document.body.textContent).toContain('附加配置结构无效')
    expect(button('保存草稿').disabled).toBe(false)
    expect(document.querySelector('[aria-label="定位错误 1"]')).not.toBeNull()
  })
})
