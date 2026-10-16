import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { JSON_SCHEMA, load } from 'js-yaml'
import OxidnsNativeEditor from '@/components/generate/OxidnsNativeEditor.vue'
import OxidnsSchemaValueEditor from '@/components/generate/OxidnsSchemaValueEditor.vue'
import OxidnsParameterPicker from '@/components/generate/OxidnsParameterPicker.vue'
import { Select } from '@/components/ui/select'
import { configNodeToValue, valueToConfigNode } from '@/lib/oxidnsConfigEditor'

// Small official-shaped catalog keeps these tests focused on editor semantics.
// The complete, generated documentation catalog has its own coverage tests.
vi.mock('@/lib/oxidnsSchema', () => ({
  oxidnsGlobalSchema: { type: 'object', help: '全局配置', properties: {
    runtime: { type: 'object', label: '运行时', help: '线程设置', properties: { worker_threads: { type: 'number', label: '线程数', help: '工作线程', default: 2 } } },
    api: { type: 'object', help: 'API', properties: { password: { type: 'string', help: '密码' } } },
  } },
  oxidnsPluginCatalog: [
    { type: 'forward', category: 'executor', label: '转发', help: '向上游转发查询', documentation: 'https://oxidns.org/plugin-reference/executor/resolution/', args: { type: 'object', label: '参数', help: '转发参数', properties: {
      concurrent: { type: 'number', label: '并发数', help: '并发上游数', default: 1 },
      upstreams: { type: 'array', help: '上游列表', items: { type: 'object', help: '单个上游', properties: { addr: { type: 'string', help: '上游地址' }, enable_pipeline: { type: 'boolean', help: '连接流水线', default: false } } } },
    } } },
    { type: 'sequence', category: 'executor', label: '执行序列', help: '依次执行规则', documentation: 'https://oxidns.org/', args: { type: 'array', help: '序列条目', items: { type: 'object', help: '规则', properties: { exec: { type: 'string', help: '执行动作' } } } } },
  ],
}))

const wrappers: VueWrapper[] = []
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))
function render(modelValue = 'plugins: []\n') {
  const wrapper = mount(OxidnsNativeEditor, { props: { modelValue } })
  wrappers.push(wrapper)
  return wrapper
}
async function click(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('button').find(button => button.text().trim() === text)
  expect(button, text).toBeDefined()
  await button!.trigger('click')
  await flushPromises()
}
async function select(wrapper: VueWrapper, ariaLabel: string, value: string) {
  const control = wrapper.findAllComponents(Select).find(select => select.find(`[aria-label="${ariaLabel}"]`).exists())
  expect(control, ariaLabel).toBeDefined()
  control!.vm.$emit('update:modelValue', value)
  await flushPromises()
}
async function pick(wrapper: VueWrapper, label: string, key: string) {
  const picker = wrapper.findAllComponents(OxidnsParameterPicker).find(component => component.props('label') === label)
  expect(picker, label).toBeDefined()
  expect(picker!.props('fields').some((field: any) => field.key === key && field.schema.help)).toBe(true)
  picker!.vm.$emit('select', key)
  await flushPromises()
}
function model(wrapper: VueWrapper): any { return load(wrapper.emitted('update:modelValue')!.at(-1)![0] as string, { schema: JSON_SCHEMA }) }
function error(wrapper: VueWrapper): string { return wrapper.emitted('error')!.at(-1)![0] as string }

describe('complete OxiDNS editor', () => {
  it('does not emit defaults or rewrite comments on mode changes; preserves unknown fields when edited', async () => {
    const original = '# retained until a form edit\nfuture: {off: false, zero: 0, empty: "", nullish: null}\nplugins:\n  - tag: custom\n    type: future_type\n    args: {list: [0, false, "", null]}\n    future_flag: true\n'
    const wrapper = render(original)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect(wrapper.find('[aria-label="插件 1.tag 值"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="插件 1 future_type说明"]').exists()).toBe(true)
    await click(wrapper, 'YAML')
    expect((wrapper.get('[aria-label="OxiDNS 完整配置 YAML"]').element as HTMLTextAreaElement).value).toBe(original)
    await click(wrapper, '表单')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await wrapper.get('[aria-label="插件 1 展开设置"]').trigger('click')
    await wrapper.get('[aria-label="插件 1.tag 值"]').setValue('renamed')
    expect(model(wrapper)).toEqual({ future: { off: false, zero: 0, empty: '', nullish: null }, plugins: [{ tag: 'renamed', type: 'future_type', args: { list: [0, false, '', null] }, future_flag: true }] })
    expect(error(wrapper)).toBe('')
  })

  it('adds native plugin types with native argument shapes and only explicit parameters', async () => {
    const wrapper = render()
    await pick(wrapper, '原生插件', 'forward')
    expect(model(wrapper)).toEqual({ plugins: [{ tag: 'forward', type: 'forward', args: {} }] })
    expect(wrapper.find('[aria-label="插件 1.args 展开参数"]').exists()).toBe(false)
    await pick(wrapper, '插件 1.args', 'concurrent')
    expect(model(wrapper).plugins[0].args).toEqual({ concurrent: 1 })
    await wrapper.get('[aria-label="插件 1.args.concurrent 值"]').setValue('0')
    expect(model(wrapper).plugins[0].args).toEqual({ concurrent: 0 })
    await pick(wrapper, '原生插件', 'sequence')
    expect(model(wrapper).plugins[1]).toEqual({ tag: 'sequence', type: 'sequence', args: [] })
    await pick(wrapper, '原生插件', 'forward')
    expect(model(wrapper).plugins[2].tag).toBe('forward_2')
  })

  it('shows upstream fields after a single plugin expansion and adds a described parameter in one choice', async () => {
    const wrapper = render('plugins: [{tag: forward_local, type: forward, args: {concurrent: 2, upstreams: [{addr: "223.5.5.5"}, {addr: "1.1.1.1"}]}}]')
    await wrapper.get('[aria-label="插件 1 展开设置"]').trigger('click')
    expect(wrapper.get('[aria-label="插件 1.args.concurrent 值"]').exists()).toBe(true)
    expect(wrapper.get('[aria-label="插件 1.args.upstreams[1].addr 值"]').exists()).toBe(true)
    expect(wrapper.get('[aria-label="插件 1.args.upstreams[2].addr 值"]').exists()).toBe(true)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await pick(wrapper, '插件 1.args.upstreams[1]', 'enable_pipeline')
    expect(model(wrapper).plugins[0].args.upstreams[0]).toEqual({ addr: '223.5.5.5', enable_pipeline: false })
    expect(wrapper.get('[aria-label="插件 1.args.upstreams[1].enable_pipeline说明"]').exists()).toBe(true)
    expect(wrapper.find('[aria-label="插件 1.args.upstreams[1] 添加所选参数"]').exists()).toBe(false)
    await wrapper.get('[aria-label="插件 1.args.upstreams[1] 展开参数"]').trigger('click')
    await wrapper.get('[aria-label="插件 1.args.concurrent 值"]').setValue('3')
    expect(wrapper.find('[aria-label="插件 1.args.upstreams[1].addr 值"]').exists()).toBe(false)
    await wrapper.get('[aria-label="插件 1.args.upstreams 下移条目 1"]').trigger('click')
    expect(wrapper.get('[aria-label="插件 1.args.upstreams[2] 展开参数"]').attributes('aria-expanded')).toBe('false')
    await click(wrapper, 'YAML')
    expect(load((wrapper.get('[aria-label="OxiDNS 完整配置 YAML"]').element as HTMLTextAreaElement).value)).toEqual(model(wrapper))
  })

  it('retains malformed YAML and invalid numeric drafts, blocking unsafe mode changes', async () => {
    const wrapper = render('plugins: [broken')
    expect(error(wrapper)).not.toBe('')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await click(wrapper, '表单')
    expect(wrapper.find('[aria-label="OxiDNS 完整配置 YAML"]').exists()).toBe(true)
    await wrapper.get('[aria-label="OxiDNS 完整配置 YAML"]').setValue('runtime: {worker_threads: 2}\nplugins: []')
    await click(wrapper, '表单')
    await wrapper.get('[aria-label="全局参数.runtime 展开参数"]').trigger('click')
    const before = wrapper.emitted('update:modelValue')!.length
    await wrapper.get('[aria-label="全局参数.runtime.worker_threads 值"]').setValue('2e')
    expect(error(wrapper)).toContain('数字')
    expect(wrapper.emitted('update:modelValue')!.length).toBe(before)
    const yaml = wrapper.findAll('button').find(button => button.text().trim() === 'YAML')!
    expect(yaml.attributes('disabled')).toBeDefined()
    await wrapper.get('[aria-label="全局参数.runtime.worker_threads 值"]').setValue('3')
    expect(error(wrapper)).toBe('')
    expect(model(wrapper).runtime.worker_threads).toBe(3)
  })

  it('keeps duplicate plugin tags editable in either mode and blocks empty native configurations', async () => {
    const wrapper = render('')
    expect(error(wrapper)).toContain('不能为空')
    await wrapper.setProps({ modelValue: 'plugins: [{tag: same, type: forward}, {tag: same, type: sequence}]' })
    expect(error(wrapper)).toContain('重复')
    await click(wrapper, 'YAML')
    await wrapper.get('[aria-label="OxiDNS 完整配置 YAML"]').setValue('plugins: [{tag: one, type: forward}, {tag: two, type: sequence}]')
    expect(error(wrapper)).toBe('')
    await click(wrapper, '表单')
    await wrapper.get('[aria-label="上移原生插件 2"]').trigger('click')
    expect(model(wrapper).plugins.map((plugin: any) => plugin.tag)).toEqual(['two', 'one'])
    await wrapper.get('[aria-label="删除原生插件 1"]').trigger('click')
    expect(model(wrapper).plugins).toEqual([{ tag: 'one', type: 'forward' }])
  })

  it('helps collapsed plugin titles without expanding them and masks native password fields', async () => {
    const wrapper = render('api: {password: private}\nplugins: [{tag: dns, type: forward, args: {}}]')
    await wrapper.get('[aria-label="插件 1 forward说明"]').trigger('click')
    expect(wrapper.get('[aria-label="插件 1 展开设置"]').attributes('aria-expanded')).toBe('false')
    await wrapper.get('[aria-label="全局参数.api 展开参数"]').trigger('click')
    expect(wrapper.get('[aria-label="全局参数.api.password 值"]').attributes('type')).toBe('password')
    await wrapper.setProps({ disabled: true })
    expect(wrapper.get('[aria-label="全局参数.api.password 值"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[aria-label="插件 1 展开设置"]').attributes('disabled')).toBeDefined()
  })
})

describe('schema-driven nested values', () => {
  it('adds nested array/map entries using schema, preserves false, and lets env references use text', async () => {
    const node = valueToConfigNode({ profiles: {} })
    const wrapper = mount(OxidnsSchemaValueEditor, { props: { node, label: '出口', schema: { type: 'object', help: '出口', properties: {
      profiles: { type: 'object', help: '配置', additionalProperties: { type: 'object', help: '配置项', properties: { timeout: { type: 'number', help: '超时' }, enabled: { type: 'boolean', help: '启用', default: false } } } },
    } } } })
    wrappers.push(wrapper)
    await wrapper.get('[aria-label="出口.profiles 展开参数"]').trigger('click')
    await wrapper.get('[aria-label="出口.profiles 添加自定义字段"]').trigger('click')
    await wrapper.get('[aria-label="出口.profiles 字段名 1"]').setValue('proxy')
    await pick(wrapper, '出口.profiles.proxy', 'enabled')
    expect(configNodeToValue(node)).toEqual({ profiles: { proxy: { enabled: false } } })
    await pick(wrapper, '出口.profiles.proxy', 'timeout')
    await wrapper.get('[aria-label="出口.profiles.proxy.timeout 更改值类型"]').trigger('click')
    await select(wrapper, '出口.profiles.proxy.timeout 类型', 'string')
    await wrapper.get('[aria-label="出口.profiles.proxy.timeout 值"]').setValue('${TIMEOUT:-5}')
    expect(configNodeToValue(node)).toEqual({ profiles: { proxy: { enabled: false, timeout: '${TIMEOUT:-5}' } } })
  })
})
