import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { JSON_SCHEMA, load } from 'js-yaml'
import OxidnsConfigEditor from '@/components/generate/OxidnsConfigEditor.vue'
import OxidnsValueEditor from '@/components/generate/OxidnsValueEditor.vue'
import { Select } from '@/components/ui/select'
import { appendConfigEntry, configNodeToValue, parseConfigDocument, serializeConfigDocument, valueToConfigNode } from '@/lib/oxidnsConfigEditor'

const wrappers: VueWrapper[] = []
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))
function render(modelValue = '') {
  const wrapper = mount(OxidnsConfigEditor, { props: { modelValue } })
  wrappers.push(wrapper)
  return wrapper
}
async function click(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('button').find(item => item.text().trim() === text)
  expect(button, text).toBeDefined()
  await button!.trigger('click')
  await flushPromises()
}
function lastModel(wrapper: VueWrapper): string {
  return wrapper.emitted('update:modelValue')!.at(-1)![0] as string
}
function lastValidity(wrapper: VueWrapper): boolean {
  return wrapper.emitted('validity')!.at(-1)![0] as boolean
}
function parsedModel(wrapper: VueWrapper) {
  return load(lastModel(wrapper), { schema: JSON_SCHEMA })
}
async function changeType(wrapper: VueWrapper, label: string, type: string) {
  const editor = wrapper.findAllComponents(OxidnsValueEditor).find(item => item.props('label') === label)
  expect(editor, label).toBeDefined()
  editor!.findComponent(Select).vm.$emit('update:modelValue', type)
  await flushPromises()
}

describe('OxiDNS extension document semantics', () => {
  it('round-trips unknown fields, nested values, timestamps as text, empty values and array order', () => {
    const original = {
      extension: { date: '2026-10-16', zero: 0, disabled: false, absent: null, blank: '', lines: 'one\ntwo\n', object: {}, list: [] },
      plugins: [{ tag: 'first', type: 'unknown_plugin', args: { mixed: [null, false, 0, '', {}, ['z', 'a']] }, future_option: { a: 2 } }, { tag: 'second', type: 'other', args: {} }]
    }
    const yaml = serializeConfigDocument(valueToConfigNode(original))
    expect(configNodeToValue(parseConfigDocument(yaml))).toEqual(original)
    expect(configNodeToValue(parseConfigDocument('created: 2026-10-16\n'))).toEqual({ created: '2026-10-16' })
    expect(configNodeToValue(parseConfigDocument('# comment only\n'))).toEqual({})
  })

  it('preserves non-cyclic aliases semantically but rejects cycles, unsafe keys and unsupported root values', () => {
    expect(configNodeToValue(parseConfigDocument('base: &value {a: 1}\nother: *value'))).toEqual({ base: { a: 1 }, other: { a: 1 } })
    for (const yaml of ['a: &self {next: *self}', '__proto__: {polluted: true}', 'args: {constructor: x}', '[a, b]', 'null', 'huge: 9007199254740993']) {
      expect(() => parseConfigDocument(yaml), yaml).toThrow()
    }
    expect(({} as any).polluted).toBeUndefined()
    expect(() => valueToConfigNode({ value: new Date() })).toThrow()
    let nested: any = 'leaf'
    for (let depth = 0; depth < 30; depth++) nested = { child: nested }
    expect(() => valueToConfigNode(nested)).toThrow(/复杂/)
  })

  it('never collapses duplicate keys or invalid numeric drafts into a valid saved value', () => {
    const tree = parseConfigDocument('a: 1')
    const extra = appendConfigEntry(tree, 'a', '')
    expect(extra.key).toBe('a_1')
    extra.key = 'a'
    expect(() => serializeConfigDocument(tree)).toThrow(/重复/)
    extra.key = 'b'
    tree.entries[0].value.scalar = ''
    expect(() => serializeConfigDocument(tree)).toThrow(/数字/)
  })
})

describe('OxiDNS structured extension editor', () => {
  it('starts structured and switches both ways without changing unknown values or original YAML text', async () => {
    const original = '# preserved until edited\nfuture: {disabled: false, items: [3, 1, null]}\nplugins:\n  - tag: custom\n    type: custom_type\n    args: {extra: ""}\n    future_flag: true\n'
    const wrapper = render(original)
    expect(wrapper.find('textarea[aria-label="基础配置扩展 YAML"]').exists()).toBe(false)
    await click(wrapper, 'YAML')
    expect((wrapper.get('#oxi-custom-config').element as HTMLTextAreaElement).value).toBe(original)
    await click(wrapper, '逐项编辑')
    await wrapper.get('[aria-label="插件 1.tag 值"]').setValue('renamed')
    const saved = parsedModel(wrapper) as any
    expect(saved.plugins[0]).toEqual({ tag: 'renamed', type: 'custom_type', args: { extra: '' }, future_flag: true })
    expect(saved.future).toEqual({ disabled: false, items: [3, 1, null] })
    await click(wrapper, 'YAML')
    await click(wrapper, '逐项编辑')
    expect(lastValidity(wrapper)).toBe(true)
    expect(parsedModel(wrapper)).toEqual(saved)
  })

  it('adds a plugin and edits nested object/list items without losing empty default fields', async () => {
    const wrapper = render()
    await click(wrapper, '添加插件')
    expect(parsedModel(wrapper)).toEqual({ plugins: [{ tag: '', type: '', args: {} }] })
    await wrapper.get('[aria-label="插件 1.tag 值"]').setValue('extra')
    await wrapper.get('[aria-label="插件 1.type 值"]').setValue('custom')
    await wrapper.get('[aria-label="插件 1.args 添加字段"]').trigger('click')
    await wrapper.get('[aria-label="插件 1.args 字段名 1"]').setValue('upstreams')
    await changeType(wrapper, '插件 1.args.upstreams', 'array')
    await wrapper.get('[aria-label="插件 1.args.upstreams 添加条目"]').trigger('click')
    await changeType(wrapper, '插件 1.args.upstreams[1]', 'object')
    await wrapper.get('[aria-label="插件 1.args.upstreams[1] 添加字段"]').trigger('click')
    await wrapper.get('[aria-label="插件 1.args.upstreams[1] 字段名 1"]').setValue('addr')
    await wrapper.get('[aria-label="插件 1.args.upstreams[1].addr 值"]').setValue('tls://dns.example:853')
    expect(parsedModel(wrapper)).toEqual({ plugins: [{ tag: 'extra', type: 'custom', args: { upstreams: [{ addr: 'tls://dns.example:853' }] } }] })
    expect(lastValidity(wrapper)).toBe(true)
  })

  it('edits numeric and boolean types, retains invalid number input, and preserves list ordering', async () => {
    const wrapper = render('items: [first, second]\n')
    await wrapper.get('[aria-label="全局参数 items 上移条目 2"]').trigger('click')
    expect(parsedModel(wrapper)).toEqual({ items: ['second', 'first'] })
    await click(wrapper, '添加全局参数')
    await changeType(wrapper, '全局参数 parameter', 'number')
    await wrapper.get('[aria-label="全局参数 parameter 值"]').setValue('12.5')
    expect((parsedModel(wrapper) as any).parameter).toBe(12.5)
    const previous = lastModel(wrapper)
    await wrapper.get('[aria-label="全局参数 parameter 值"]').setValue('12e')
    expect(lastValidity(wrapper)).toBe(false)
    expect(lastModel(wrapper)).toBe(previous)
    expect((wrapper.get('[aria-label="全局参数 parameter 值"]').element as HTMLInputElement).value).toBe('12e')
    expect(wrapper.get('[role="alert"]').text()).toContain('数字')
    await changeType(wrapper, '全局参数 parameter', 'boolean')
    expect(lastValidity(wrapper)).toBe(true)
    expect((parsedModel(wrapper) as any).parameter).toBe(false)
  })

  it('retains malformed initial YAML and malformed drafts until corrected, without replacing them with empty data', async () => {
    const invalid = 'plugins:\n - args: [broken'
    const wrapper = render(invalid)
    expect((wrapper.get('#oxi-custom-config').element as HTMLTextAreaElement).value).toBe(invalid)
    expect(lastValidity(wrapper)).toBe(false)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await click(wrapper, '逐项编辑')
    expect(wrapper.find('#oxi-custom-config').exists()).toBe(true)
    const otherInvalid = 'extensions: [still broken'
    await wrapper.get('#oxi-custom-config').setValue(otherInvalid)
    expect(lastModel(wrapper)).toBe(otherInvalid)
    expect(lastValidity(wrapper)).toBe(false)
    await wrapper.get('#oxi-custom-config').setValue('extensions: {value: null}\nplugins: []')
    expect(lastValidity(wrapper)).toBe(true)
    await click(wrapper, '逐项编辑')
    await click(wrapper, '添加插件')
    expect(parsedModel(wrapper)).toEqual({ extensions: { value: null }, plugins: [{ tag: '', type: '', args: {} }] })
  })

  it('keeps non-list plugins in global fields and blocks implicit replacement', async () => {
    const wrapper = render('plugins: {future: true}')
    const add = wrapper.findAll('button').find(button => button.text() === '添加插件')!
    expect(add.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('现有 plugins 不是列表')
    await click(wrapper, 'YAML')
    expect((wrapper.get('#oxi-custom-config').element as HTMLTextAreaElement).value).toBe('plugins: {future: true}')
  })

  it('reloads external model changes even when returning to an earlier emitted value', async () => {
    const wrapper = render()
    await click(wrapper, '添加全局参数')
    const previous = lastModel(wrapper)
    await wrapper.setProps({ modelValue: previous })
    await wrapper.setProps({ modelValue: 'external: true' })
    expect((wrapper.get('[aria-label="全局参数名称"]').element as HTMLInputElement).value).toBe('external')
    await wrapper.setProps({ modelValue: previous })
    expect((wrapper.get('[aria-label="全局参数名称"]').element as HTMLInputElement).value).toBe('parameter')
    expect(lastValidity(wrapper)).toBe(true)
  })
})
