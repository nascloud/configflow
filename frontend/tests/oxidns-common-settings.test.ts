import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import OxidnsCommonSettings from '@/components/generate/OxidnsCommonSettings.vue'
import OxidnsFields from '@/components/generate/OxidnsFields.vue'
import OxidnsMatchEditor from '@/components/generate/OxidnsMatchEditor.vue'
import MultiSelect from '@/components/common/MultiSelect.vue'
import { featureGroups } from '@/lib/oxidns'

const wrappers: ReturnType<typeof mount>[] = []
const values = () => ({ worker_threads: 8, listen: '127.0.0.1:5353', local_dns: '- addr: 192.0.2.53\n  extension: false\n', remote_dns: '192.0.2.54', fallback_dns: '', block_qtypes: [65], local_concurrent: 2,
  direct_rules: ['direct'], proxy_rules: [], direct_rulesets: [], proxy_rulesets: [], custom_matches: [], custom_hosts: '', custom_match_position: 'head',
  api: {enabled: true, listen: '127.0.0.1:9199'}, log: {level: 'info'},
  ...Object.fromEntries(featureGroups.map(feature => [feature.key, {enabled: false, args: {}}])) })
async function render(extra: Record<string, any> = {}) {
  const wrapper = mount(OxidnsCommonSettings, {attachTo: document.body, props: {values: values(), capabilities: {}, rules: [{id: 'direct', name: 'Direct'}, {id: 'proxy', name: 'Proxy'}], ruleSets: [], ...extra}})
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); document.body.innerHTML = '' })
async function click(text: string) {
  const target = Array.from(document.querySelectorAll('button')).find(button => button.textContent?.trim() === text)
  expect(target, text).toBeDefined()
  target!.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, button: 0})); target!.click()
  await flushPromises()
}
async function input(id: string, value: string) {
  const target = document.getElementById(id) as HTMLInputElement
  expect(target, id).not.toBeNull()
  target.value = value; target.dispatchEvent(new Event('input', {bubbles: true}))
  await flushPromises()
}
const lastChange = (wrapper: ReturnType<typeof mount>) => wrapper.emitted('change')?.at(-1)?.[0]

describe('OxiDNS current common settings', () => {
  it('shows all seven former control groups without tool, preview or save actions', async () => {
    await render()
    for (const label of ['核心设置', '分流规则', 'DNS 上游', '缓存', '特色功能', 'WebUI / 日志', '高级']) {
      expect(document.querySelector(`[role="tab"][data-state]`) && [...document.querySelectorAll('[role="tab"]')].some(tab => tab.textContent?.trim() === label)).toBe(true)
    }
    expect(document.querySelector('[role="dialog"]')).toBeNull()
    expect(document.body.textContent).not.toContain('预览变更')
    expect(document.body.textContent).not.toContain('模板工具')
    expect(document.body.textContent).not.toContain('保存设置')
    await click('特色功能')
    for (const group of featureGroups) expect(document.querySelector(`[aria-label="${group.title}说明"]`)).not.toBeNull()
  })

  it('edits one field at a time without mutating the supplied current projection', async () => {
    const original = values()
    const wrapper = await render({values: original})
    await input('oxi-worker_threads', '6')
    expect(lastChange(wrapper)).toEqual({worker_threads: 6})
    expect(original.worker_threads).toBe(8)
    await wrapper.setProps({values: {...original, worker_threads: 6, listen: '127.0.0.1:5354'}})
    expect((document.getElementById('oxi-worker_threads') as HTMLInputElement).value).toBe('6')
    expect((document.getElementById('oxi-listen') as HTMLInputElement).value).toBe('127.0.0.1:5354')
  })

  it('keeps incomplete numbers across sections and external updates without sending zero', async () => {
    const wrapper = await render()
    await input('oxi-worker_threads', '')
    expect(wrapper.emitted('change')).toBeUndefined()
    expect(wrapper.emitted('error')?.at(-1)?.[0]).toContain('请填写整数')
    await click('DNS 上游')
    await input('oxi-local_concurrent', '3')
    await wrapper.setProps({values: {...values(), local_concurrent: 3, worker_threads: 4}})
    await click('核心设置')
    expect((document.getElementById('oxi-worker_threads') as HTMLInputElement).value).toBe('')
    expect(wrapper.emitted('error')?.at(-1)?.[0]).toContain('请填写整数')
    await input('oxi-worker_threads', '5')
    expect(lastChange(wrapper)).toEqual({worker_threads: 5})
    expect(wrapper.emitted('error')?.at(-1)?.[0]).toBe('')
  })

  it('shows custom capability help and navigates without showing a stale input', async () => {
    const wrapper = await render({capabilities: {worker_threads: {editable: false, reason: '运行时字段使用扩展结构', target: 'runtime'}, query_recorder: {editable: false, reason: '多个手工实例', target: 'plugins'}}})
    expect(document.getElementById('oxi-worker_threads')).toBeNull()
    expect(document.querySelector('[aria-label="工作线程数配置状态说明"]')).not.toBeNull()
    const disabledPreset = [...document.querySelectorAll('button')].find(button => button.textContent?.trim() === '填入已验证性能参数')!
    expect(disabledPreset.disabled).toBe(false)
    await click('完整参数')
    expect(wrapper.emitted('navigate')?.at(-1)).toEqual(['runtime'])
    await click('特色功能')
    expect(document.querySelector('[aria-label="查询记录设置"]')?.parentElement?.textContent).toContain('自定义配置')
    expect(document.getElementById('oxi-enable-query_recorder')).toBeNull()
    expect(wrapper.emitted('change')).toBeUndefined()
  })

  it('emits a single performance batch preserving upstream and rule selections', async () => {
    const wrapper = await render()
    await click('填入已验证性能参数')
    const changes = lastChange(wrapper) as Record<string, any>
    expect(changes).toMatchObject({worker_threads: 2, cache_lazy_ttl: 300, probe_cache_size: 2048})
    expect(changes).not.toHaveProperty('local_dns')
    expect(changes).not.toHaveProperty('direct_rules')
    expect(wrapper.emitted('change')).toHaveLength(1)
  })

  it('sends rule selections and feature toggles as current field changes', async () => {
    const wrapper = await render()
    await click('分流规则')
    wrapper.findAllComponents(MultiSelect)[2].vm.$emit('update:modelValue', ['direct', 'proxy'])
    await flushPromises()
    expect(lastChange(wrapper)).toEqual({direct_rules: ['direct', 'proxy']})
    await click('特色功能')
    const trigger = document.querySelector('[aria-label="查询记录设置"]') as HTMLButtonElement
    trigger.click(); await flushPromises()
    const enabled = document.getElementById('oxi-enable-query_recorder') as HTMLButtonElement
    enabled.click(); await flushPromises()
    expect(lastChange(wrapper)).toEqual({'query_recorder.enabled': true})
    expect(document.getElementById('oxi-query_recorder.args.path')).not.toBeNull()
  })

  it('retains upstream extension values and invalid YAML while changing another group', async () => {
    const wrapper = await render()
    await click('DNS 上游')
    const local = document.querySelector('section[aria-label="直连 DNS"]')!
    const toggle = [...local.querySelectorAll('button')].find(button => button.textContent?.trim() === 'YAML')!
    toggle.click(); await flushPromises()
    const yaml = local.querySelector('textarea')!
    expect(yaml.value).toContain('extension: false')
    yaml.value = '- addr: ['; yaml.dispatchEvent(new Event('input', {bubbles: true})); await flushPromises()
    expect(wrapper.emitted('change')).toBeUndefined()
    await click('核心设置')
    await input('oxi-worker_threads', '3')
    await wrapper.setProps({values: {...values(), worker_threads: 3}})
    await click('DNS 上游')
    expect(yaml.value).toBe('- addr: [')
    expect(wrapper.emitted('error')?.at(-1)?.[0]).not.toBe('')
  })

  it('edits only actual custom matches and rejects paused metadata in YAML', async () => {
    const wrapper = await render({values: {...values(), custom_matches: [{matches: 'qtype 65', exec: 'reject 0'}]}})
    await click('高级')
    expect(wrapper.getComponent(OxidnsMatchEditor).props('allowDisable')).toBe(false)
    expect(document.querySelector('[aria-label="启用匹配项 1"]')).toBeNull()
    const editor = document.querySelector('[data-testid="oxidns-match-editor"]')!
    const toggle = [...editor.querySelectorAll('button')].find(button => button.textContent?.trim() === 'YAML')!
    toggle.click(); await flushPromises()
    const yaml = editor.querySelector('textarea')!
    yaml.value = '- matches: qtype 65\n  exec: reject 0\n  enabled: false'; yaml.dispatchEvent(new Event('input', {bubbles: true})); await flushPromises()
    expect(wrapper.emitted('error')?.at(-1)?.[0]).toContain('不支持 enabled')
    expect(wrapper.emitted('change')).toBeUndefined()
  })

  it('distinguishes absent native defaults from an explicit false value', async () => {
    await render({values: {...values(), worker_threads: null, always_standby: null, log: {level: null}}, capabilities: {worker_threads: {editable: true, absent: true}, always_standby: {editable: true, absent: true}}})
    expect((document.getElementById('oxi-worker_threads') as HTMLInputElement).placeholder).toBe('未配置（使用核心默认）')
    await click('DNS 上游')
    expect(document.getElementById('oxi-always_standby')?.parentElement?.textContent).toContain('未配置 · 核心默认')
    await click('WebUI / 日志')
    expect(document.getElementById('oxi-log.level')?.textContent).toContain('未配置（使用核心默认）')
  })

  it('keeps inactive feature drafts without preventing the feature from being disabled', async () => {
    const wrapper = await render({values: {...values(), query_recorder: {enabled: true, args: {path: 'state/query.db', retention_days: 7}}}})
    await click('特色功能')
    ;(document.querySelector('[aria-label="查询记录设置"]') as HTMLButtonElement).click(); await flushPromises()
    await input('oxi-query_recorder.args.retention_days', '')
    expect(wrapper.emitted('error')?.at(-1)?.[0]).toContain('请填写整数')
    ;(document.getElementById('oxi-enable-query_recorder') as HTMLButtonElement).click(); await flushPromises()
    expect(lastChange(wrapper)).toEqual({'query_recorder.enabled': false})
    expect(wrapper.emitted('error')?.at(-1)?.[0]).toBe('')
    ;(document.getElementById('oxi-enable-query_recorder') as HTMLButtonElement).click(); await flushPromises()
    expect((document.getElementById('oxi-query_recorder.args.retention_days') as HTMLInputElement).value).toBe('')
    expect(wrapper.emitted('error')?.at(-1)?.[0]).toContain('请填写整数')
  })

  it('applies only editable performance fields and separately disables navigation', async () => {
    const wrapper = await render({navigationDisabled: true, capabilities: {aggregate_rules: {editable: false, reason: '当前无需合并'}, worker_threads: {editable: false, reason: '手工结构'}}})
    await click('填入已验证性能参数')
    const changes = lastChange(wrapper) as Record<string, any>
    expect(changes.cache_lazy_ttl).toBe(300)
    expect(changes).not.toHaveProperty('aggregate_rules')
    expect(changes).not.toHaveProperty('worker_threads')
    const native = [...document.querySelectorAll('button')].filter(button => ['全部功能', '完整参数'].includes(button.textContent?.trim() || ''))
    expect(native.length).toBeGreaterThan(0)
    expect(native.every(button => button.disabled)).toBe(true)
    await input('oxi-listen', '127.0.0.1:5454')
    expect(lastChange(wrapper)).toEqual({listen: '127.0.0.1:5454'})
  })

  it('sends TLS removal as JSON-safe null and provides full native access', async () => {
    const wrapper = await render()
    const fields = wrapper.findAllComponents(OxidnsFields).find(field => field.props('fields').some((item: any) => item.path === 'ros_route.args.tls'))!
    fields.vm.$emit('change', 'ros_route.args.tls', undefined)
    await flushPromises()
    expect(lastChange(wrapper)).toEqual({'ros_route.args.tls': null})
    await click('全部功能')
    expect(wrapper.emitted('full')).toHaveLength(1)
  })
})
