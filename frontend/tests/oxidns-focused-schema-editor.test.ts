import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import OxidnsSchemaValueEditor from '@/components/generate/OxidnsSchemaValueEditor.vue'
import OxidnsParameterPicker from '@/components/generate/OxidnsParameterPicker.vue'
import { DropdownMenuItem } from '@/components/ui/dropdown-menu'
import { Select } from '@/components/ui/select'
import { configNodeToValue, valueToConfigNode, type ConfigValueNode } from '@/lib/oxidnsConfigEditor'
import type { OxidnsSchema } from '@/lib/oxidnsSchema'

const wrappers: VueWrapper[] = []
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))
const text = (label: string, required = false): OxidnsSchema => ({ type: 'string', label, help: `${label}的说明`, required })
function render(node: ConfigValueNode, schema?: OxidnsSchema, layout: 'default' | 'plugin' = 'default') {
  const wrapper = mount(OxidnsSchemaValueEditor, { props: { node, schema, label: '配置', focused: true, layout } })
  wrappers.push(wrapper)
  return wrapper
}
async function pick(wrapper: VueWrapper, label: string, key: string) {
  const picker = wrapper.findAllComponents(OxidnsParameterPicker).find(component => component.props('label') === label)
  expect(picker).toBeDefined()
  picker!.vm.$emit('select', key)
  await flushPromises()
}

describe('focused OxiDNS group form', () => {
  it('navigates to stable child node ids without expanding or modifying their values', async () => {
    const value = { http: { listen: ':9199', auth: { enabled: false, limit: 0, future: null } }, future: ['', false, 0, null] }
    const node = valueToConfigNode(value)
    const wrapper = render(node, { type: 'object', help: '管理 API', properties: {
      http: { type: 'object', label: 'HTTP 服务', help: '监听和认证', properties: { listen: text('监听地址') } },
    } })
    expect(wrapper.find('[aria-label="配置.http.listen 值"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="配置.http 展开参数"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="配置.http说明"]').exists()).toBe(true)
    await wrapper.get('[aria-label="配置.http 编辑分组"]').trigger('click')
    expect(wrapper.emitted('navigate')).toEqual([[node.entries[0].value.id]])
    await wrapper.get('[aria-label="配置.future 编辑分组"]').trigger('click')
    expect(wrapper.emitted('navigate')!.at(-1)).toEqual([node.entries[1].value.id])
    expect(configNodeToValue(node)).toEqual(value)
    expect(wrapper.emitted('change')).toBeUndefined()
  })

  it('shows missing required and common scalar fields without inserting defaults until explicitly added', async () => {
    const node = valueToConfigNode({})
    const wrapper = render(node, { type: 'object', help: 'HTTP 服务', properties: {
      listen: { ...text('监听地址', true), default: ':9199' },
      optional: { ...text('可选字段'), default: 'optional' },
      auth: { type: 'object', help: '认证', required: true, default: { enabled: true } },
      worker_threads: { type: 'number', help: '线程数', default: 2 },
      concurrent: { type: 'number', help: '并发数', default: 1 },
      level: { ...text('日志等级'), default: 'info' },
      file: text('日志文件'),
    } })
    expect(wrapper.text()).toContain('未配置')
    expect(wrapper.get('[aria-label="配置.listen 添加参数"]').exists()).toBe(true)
    expect(wrapper.find('[aria-label="配置.optional 添加参数"]').exists()).toBe(false)
    for (const key of ['worker_threads', 'concurrent', 'level', 'file']) {
      expect(wrapper.get(`[aria-label="配置.${key} 添加参数"]`).exists()).toBe(true)
      expect(wrapper.find(`[aria-label="配置.${key} 值"]`).exists()).toBe(false)
    }
    expect(configNodeToValue(node)).toEqual({})
    expect(wrapper.emitted('change')).toBeUndefined()
    await wrapper.get('[aria-label="配置.listen 添加参数"]').trigger('click')
    expect(configNodeToValue(node)).toEqual({ listen: ':9199' })
    expect(wrapper.get('[aria-label="配置.listen 值"]').element).toHaveProperty('value', ':9199')
    expect(wrapper.emitted('change')).toHaveLength(1)
  })

  it('edits upstreams directly, propagates nested navigation, and keeps data attached while reordering', async () => {
    const node = valueToConfigNode({ tag: 'forward_local', type: 'forward', args: { concurrent: 0, upstreams: [
      { addr: '223.5.5.5', enabled: false, transport: { future: null }, custom: [0, false, '', null] },
      { addr: '1.1.1.1', enabled: true },
    ] } })
    const upstream: OxidnsSchema = { type: 'object', help: '上游', properties: {
      addr: text('上游地址', true), enabled: { type: 'boolean', help: '开关' },
      transport: { type: 'object', help: '扩展传输配置' },
    } }
    const schema: OxidnsSchema = { type: 'object', help: '转发插件', properties: {
      tag: text('标识'), type: text('类型'), args: { type: 'object', help: '转发参数', properties: {
        concurrent: { type: 'number', help: '并发数' }, upstreams: { type: 'array', help: '上游列表', items: upstream },
      } },
    } }
    const wrapper = render(node, schema, 'plugin')
    expect(wrapper.find('[aria-label="配置.args 编辑分组"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="配置.args.upstreams 编辑分组"]').exists()).toBe(false)
    expect(wrapper.find('[aria-label="配置.args.upstreams[1] 展开参数"]').exists()).toBe(false)
    expect(wrapper.get('[aria-label="配置.args.upstreams[1].addr 值"]').exists()).toBe(true)
    expect(wrapper.get('[aria-label="配置.args.upstreams[2].addr 值"]').exists()).toBe(true)
    expect(wrapper.findAll('[aria-label="配置.args.upstreams[1].addr 值"]')).toHaveLength(1)
    const addressHeader = wrapper.get('[aria-label="配置.args.upstreams[1].addr 值"]').element.closest('.oxi-schema-item-header')!
    expect(addressHeader.querySelector('[aria-label="配置.args.upstreams 下移条目 1"]')).not.toBeNull()
    expect(addressHeader.querySelector('[aria-label="配置.args.upstreams 删除条目 1"]')).not.toBeNull()
    expect(addressHeader.querySelector('[aria-label="配置.args.upstreams[1].addr说明"]')).not.toBeNull()
    expect(wrapper.emitted('change')).toBeUndefined()
    const upstreams = node.entries[2].value.entries[1].value
    const firstId = upstreams.items[0].id
    const transportId = upstreams.items[0].entries[2].value.id
    await wrapper.get('[aria-label="配置.args.upstreams[1].transport 编辑分组"]').trigger('click')
    expect(wrapper.emitted('navigate')!.at(-1)).toEqual([transportId])
    await wrapper.get('[aria-label="配置.args.upstreams 下移条目 1"]').trigger('click')
    expect(upstreams.items[1].id).toBe(firstId)
    await wrapper.get('[aria-label="配置.args.upstreams[2].addr 值"]').setValue('223.5.5.5:53')
    expect(configNodeToValue(node).args).toEqual({ concurrent: 0, upstreams: [
      { addr: '1.1.1.1', enabled: true },
      { addr: '223.5.5.5:53', enabled: false, transport: { future: null }, custom: [0, false, '', null] },
    ] })
    await wrapper.get('[aria-label="配置.args.upstreams[2] 更多操作"]').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    const fullItem = wrapper.findAllComponents(DropdownMenuItem).find(item => item.attributes('aria-label') === '配置.args.upstreams[2] 完整条目设置')
    expect(fullItem).toBeDefined()
    fullItem!.vm.$emit('select')
    expect(wrapper.emitted('navigate')!.at(-1)).toEqual([firstId])
  })

  it('keeps empty and non-string upstream addresses in the generic form without losing values', async () => {
    const value = { upstreams: [{ addr: 0, future: false }, {}, { addr: 'line1\nline2', future: null }] }
    const node = valueToConfigNode(value)
    const wrapper = render(node, { type: 'object', help: '转发参数', properties: {
      upstreams: { type: 'array', help: '上游', items: { type: 'object', help: '上游参数', properties: { addr: text('地址', true) } } },
    } })
    const number = wrapper.get('[aria-label="配置.upstreams[1].addr 值"]')
    expect(number.element.closest('.oxi-schema-item-header')).toBeNull()
    expect(number.element).toHaveProperty('value', '0')
    expect(wrapper.get('[aria-label="配置.upstreams[2].addr 添加参数"]').exists()).toBe(true)
    expect(wrapper.get('[aria-label="配置.upstreams[3].addr 值"]').element.tagName).toBe('TEXTAREA')
    expect(configNodeToValue(node)).toEqual(value)
    expect(wrapper.emitted('change')).toBeUndefined()
    await wrapper.get('[aria-label="配置.upstreams[2].addr 添加参数"]').trigger('click')
    expect(wrapper.get('[aria-label="配置.upstreams[2].addr 值"]').element.closest('.oxi-schema-item-header')).not.toBeNull()
    expect(configNodeToValue(node)).toEqual({ upstreams: [{ addr: 0, future: false }, { addr: '' }, { addr: 'line1\nline2', future: null }] })
  })

  it('preserves unknown scalar values and unfinished numeric drafts across group selection', async () => {
    const node = valueToConfigNode({ count: 0, off: false, empty: '', nothing: null, extra: { count: 5 } })
    const schema: OxidnsSchema = { type: 'object', help: '配置', properties: { count: { type: 'number', help: '数量' } } }
    const wrapper = render(node, schema)
    await wrapper.get('[aria-label="配置.count 值"]').setValue('2e')
    const count = node.entries[0].value
    expect(count.scalar).toBe('2e')
    const nested = node.entries[4].value
    await wrapper.setProps({ node: nested, schema: undefined, label: '配置.extra' })
    await wrapper.setProps({ node, schema, label: '配置' })
    expect(wrapper.get('[aria-label="配置.count 值"]').element).toHaveProperty('value', '2e')
    await wrapper.get('[aria-label="配置.count 值"]').setValue('2')
    expect(configNodeToValue(node)).toEqual({ count: 2, off: false, empty: '', nothing: null, extra: { count: 5 } })
  })

  it('edits mixed arrays without expanding nested arrays indefinitely', async () => {
    const values = [0, false, '', null, [[{ future: 1 }]]]
    const node = valueToConfigNode(values)
    const wrapper = render(node)
    expect(wrapper.get('[aria-label="配置[1] 值"]').element).toHaveProperty('value', '0')
    expect(wrapper.find('[aria-label="配置[2] 值"]').exists()).toBe(true)
    expect(wrapper.get('[aria-label="配置[3] 值"]').element).toHaveProperty('value', '')
    expect(wrapper.find('[aria-label="配置[5][1] 编辑分组"]').exists()).toBe(false)
    await wrapper.get('[aria-label="配置[5] 编辑分组"]').trigger('click')
    expect(wrapper.emitted('navigate')).toEqual([[node.items[4].id]])
    expect(configNodeToValue(node)).toEqual(values)
    expect(wrapper.emitted('change')).toBeUndefined()
  })

  it('offers explicit type changes through the more menu', async () => {
    const node = valueToConfigNode({ count: 2 })
    const wrapper = render(node, { type: 'object', help: '配置', properties: { count: { type: 'number', help: '数量' } } })
    await wrapper.get('[aria-label="配置.count 更多操作"]').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    const typeAction = wrapper.findAllComponents(DropdownMenuItem).find(item => item.attributes('aria-label') === '配置.count 更改值类型')
    expect(typeAction).toBeDefined()
    typeAction!.vm.$emit('select')
    await flushPromises()
    const select = wrapper.findAllComponents(Select).find(item => item.find('[aria-label="配置.count 类型"]').exists())
    expect(select).toBeDefined()
    select!.vm.$emit('update:modelValue', 'string')
    await flushPromises()
    await wrapper.get('[aria-label="配置.count 值"]').setValue('${COUNT:-2}')
    expect(configNodeToValue(node)).toEqual({ count: '${COUNT:-2}' })
  })

  it('keeps omitted optional parameters in the picker and honours disabled controls', async () => {
    const node = valueToConfigNode({})
    const wrapper = render(node, { type: 'object', help: '配置', properties: { enabled: { type: 'boolean', help: '连接开关', default: false } } })
    await pick(wrapper, '配置', 'enabled')
    expect(configNodeToValue(node)).toEqual({ enabled: false })
    expect(wrapper.get('[aria-label="配置.enabled说明"]').exists()).toBe(true)
    expect(wrapper.get('[aria-label="配置.enabled 更多操作"]').exists()).toBe(true)
    expect(wrapper.find('[aria-label="配置.enabled 更改值类型"]').exists()).toBe(false)
    await wrapper.setProps({ disabled: true })
    expect(wrapper.get('[aria-label="配置.enabled 更多操作"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[aria-label="配置.enabled 值"]').attributes('disabled')).toBeDefined()
    expect(configNodeToValue(node)).toEqual({ enabled: false })
  })

  it('locks the global object type while allowing parameters and child value types to be edited', async () => {
    const node = valueToConfigNode({ unknown: false })
    const wrapper = render(node, { type: 'object', help: '全局参数', properties: { name: text('名称') } })
    await wrapper.setProps({ typeLocked: true })
    await wrapper.get('[aria-label="配置 更多操作"]').trigger('keydown', { key: 'Enter' })
    await flushPromises()
    expect(wrapper.findAllComponents(DropdownMenuItem).some(item => item.attributes('aria-label') === '配置 更改值类型')).toBe(false)
    expect(wrapper.findAllComponents(DropdownMenuItem).some(item => item.attributes('aria-label') === '配置 添加自定义字段')).toBe(true)
    await pick(wrapper, '配置', 'name')
    await wrapper.get('[aria-label="配置.name 值"]').setValue('dns')
    expect(configNodeToValue(node)).toEqual({ unknown: false, name: 'dns' })
    const nameEditor = wrapper.findAllComponents(OxidnsSchemaValueEditor).find(editor => editor.props('label') === '配置.name')!
    expect(nameEditor.props('typeLocked')).toBe(false)
  })
})
