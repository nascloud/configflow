import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { load, dump, JSON_SCHEMA } from 'js-yaml'
import OxidnsNativeEditor from '@/components/generate/OxidnsNativeEditor.vue'
import OxidnsConfigNavigation from '@/components/generate/OxidnsConfigNavigation.vue'
import OxidnsSchemaValueEditor from '@/components/generate/OxidnsSchemaValueEditor.vue'

const wrappers: VueWrapper[] = []
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))
const fixture = {
  runtime: { worker_threads: 2, future_runtime: false }, log: { level: 'info' },
  api: { http: { listen: '127.0.0.1:9090', webui: { enabled: false } } },
  future: { zero: 0, off: false, nil: null, list: [0, false, null] },
  plugins: [
    { tag: 'forward_local', type: 'forward', args: { concurrent: 0, upstreams: [{ addr: '223.5.5.5', enable_pipeline: false, future_value: 0 }] } },
    { tag: 'forward_remote', type: 'forward', args: { upstreams: [{ addr: '1.1.1.1' }] } },
    { tag: 'cache_main', type: 'cache', args: { size: 0, lazy_cache_ttl: 0 } },
    { tag: 'udp', type: 'udp_server', args: { listen: '127.0.0.1:5353', entry: 'main' } },
    { tag: 'custom', type: 'future_type', args: { off: false, zero: 0 } },
  ],
}
function render(document: any = fixture) {
  const wrapper = mount(OxidnsNativeEditor, { props: { modelValue: dump(document) } })
  wrappers.push(wrapper)
  return wrapper
}
async function click(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('button').find(button => button.text().trim() === text)
  expect(button, text).toBeDefined()
  await button!.trigger('click'); await flushPromises()
}
function lastModel(wrapper: VueWrapper): any { return load(wrapper.emitted('update:modelValue')!.at(-1)![0] as string, { schema: JSON_SCHEMA }) }
function lastError(wrapper: VueWrapper) { return wrapper.emitted('error')!.at(-1)![0] as string }
function findNavigation(wrapper: VueWrapper, code: string) {
  const flatten = (items: any[]): any[] => items.flatMap(item => [item, ...flatten(item.children ?? [])])
  return flatten(wrapper.getComponent(OxidnsConfigNavigation).props('items')).find(item => item.code === code || item.id === code)
}

describe('one OxiDNS configuration across common, complete and YAML views', () => {
  it('shares exact nodes across views, applies YAML edits back to common controls and preserves native values', async () => {
    const wrapper = render()
    expect(wrapper.find('[aria-label="OxiDNS 常用设置"]').exists()).toBe(true)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect((wrapper.get('[aria-label="全局参数.api.http.listen 值"]').element as HTMLTextAreaElement).value).toBe('127.0.0.1:9090')
    const node = wrapper.findAllComponents(OxidnsSchemaValueEditor).find(item => item.props('label') === '全局参数.runtime')!.props('node')
    await wrapper.get('[aria-label="全局参数.runtime.worker_threads 值"]').setValue('0')
    const updated = structuredClone(fixture); updated.runtime.worker_threads = 0
    expect(lastModel(wrapper)).toEqual(updated)
    await wrapper.get('[aria-label="运行时 完整参数"]').trigger('click')
    expect(wrapper.getComponent(OxidnsConfigNavigation).props('selectedId')).toBe(node.id)
    expect(wrapper.findAllComponents(OxidnsSchemaValueEditor).find(item => item.props('label') === '全局参数.runtime')!.props('node')).toBe(node)
    await wrapper.get('[aria-label="全局参数.runtime.worker_threads 值"]').setValue('4')
    await click(wrapper, 'YAML')
    const textarea = wrapper.get('[aria-label="OxiDNS 完整配置 YAML"]')
    const yamlModel: any = load((textarea.element as HTMLTextAreaElement).value)
    expect(yamlModel.runtime.worker_threads).toBe(4)
    yamlModel.runtime.worker_threads = 3
    await textarea.setValue(dump(yamlModel)); await click(wrapper, '常用设置')
    expect((wrapper.get('[aria-label="全局参数.runtime.worker_threads 值"]').element as HTMLInputElement).value).toBe('3')
    expect(lastModel(wrapper).future).toEqual(fixture.future)
    expect(lastModel(wrapper).plugins).toEqual(fixture.plugins)
    expect(lastError(wrapper)).toBe('')
  })

  it('shows every upstream instance, edits its original arguments and retains the selected common section', async () => {
    const wrapper = render(); await click(wrapper, 'DNS 上游')
    expect(wrapper.find('[aria-label="常用 forward_local"]').exists()).toBe(true)
    expect(wrapper.find('[aria-label="常用 forward_remote"]').exists()).toBe(true)
    await wrapper.get('[aria-label="插件 2.args.upstreams[1].addr 值"]').setValue('9.9.9.9')
    expect(lastModel(wrapper).plugins[1].args.upstreams[0].addr).toBe('9.9.9.9')
    expect(lastModel(wrapper).plugins[0]).toEqual(fixture.plugins[0])
    expect(lastModel(wrapper).plugins[4]).toEqual(fixture.plugins[4])
    await click(wrapper, '完整参数'); await click(wrapper, '常用设置')
    expect((wrapper.get('[aria-label="插件 2.args.upstreams[1].addr 值"]').element as HTMLInputElement).value).toBe('9.9.9.9')
    await click(wrapper, '缓存')
    expect(wrapper.find('[aria-label="插件 3.args.size 值"]').exists()).toBe(true)
    await click(wrapper, '监听服务')
    expect(wrapper.find('[aria-label="插件 4.args.listen 值"]').exists()).toBe(true)
  })

  it('leaves missing groups absent until explicitly added and writes no unrelated defaults', async () => {
    const wrapper = render({ future: { zero: 0 }, plugins: [] })
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await wrapper.get('[aria-label="常用设置 添加运行时"]').trigger('click')
    expect(lastModel(wrapper)).toEqual({ future: { zero: 0 }, runtime: {}, plugins: [] })
    await click(wrapper, 'DNS 上游')
    const addForward = wrapper.findAll('button').find(button => button.attributes('aria-label')?.startsWith('常用设置 添加') && button.text().includes('forward'))!
    await addForward.trigger('click')
    expect(lastModel(wrapper).plugins).toEqual([{ tag: 'forward', type: 'forward', args: {} }])
    expect(lastError(wrapper)).toContain('upstreams')
  })

  it('retains incomplete numbers across common/full views and returns to the exact unfinished input', async () => {
    const wrapper = render()
    await wrapper.get('[aria-label="全局参数.runtime.worker_threads 值"]').setValue('-')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    const yamlButton = () => wrapper.findAll('button').find(button => button.text().trim() === 'YAML')!
    expect(yamlButton().attributes('disabled')).toBeDefined()
    await click(wrapper, '完整参数')
    wrapper.getComponent(OxidnsConfigNavigation).vm.$emit('select', findNavigation(wrapper, 'api').id)
    await flushPromises(); await click(wrapper, '常用设置')
    expect((wrapper.get('[aria-label="全局参数.runtime.worker_threads 值"]').element as HTMLInputElement).value).toBe('-')
    await click(wrapper, '返回未完成输入')
    expect(wrapper.find('[aria-label="OxiDNS 常用设置"]').exists()).toBe(false)
    const input = wrapper.get('[aria-label="全局参数.runtime.worker_threads 值"]')
    expect((input.element as HTMLInputElement).value).toBe('-')
    await input.setValue('2')
    expect(lastError(wrapper)).toBe('')
    expect(yamlButton().attributes('disabled')).toBeUndefined()
  })

  it('shares required validation, locates missing fields from common settings and accepts optional empty args', async () => {
    const wrapper = render({ plugins: [{ tag: 'http', type: 'http_server', args: {} }, { tag: 'reverse', type: 'reverse_lookup', args: {} }] })
    expect(lastError(wrapper)).toContain('args.listen')
    await click(wrapper, '补全必填参数')
    expect(wrapper.find('[aria-label="OxiDNS 常用设置"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('必填 · 待填写')
    expect(wrapper.getComponent(OxidnsSchemaValueEditor).props('label')).toBe('插件 1.args')
    await click(wrapper, 'YAML')
    await wrapper.get('[aria-label="OxiDNS 完整配置 YAML"]').setValue(dump({ plugins: [{ tag: 'http', type: 'http_server', args: { listen: ':5353', entries: [] } }, { tag: 'reverse', type: 'reverse_lookup', args: {} }] }))
    await click(wrapper, '常用设置')
    expect(lastError(wrapper)).toBe('')
    expect(lastModel(wrapper).plugins[1].args).toEqual({})
  })

  it('adds absent args only on request and preserves other fields and plugin order', async () => {
    const wrapper = render({ plugins: [{ tag: 'lookup', type: 'reverse_lookup', future: false }, { tag: 'extra', type: 'future', args: 0 }] })
    await click(wrapper, '缓存')
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    await wrapper.get('[aria-label="lookup 添加参数分组"]').trigger('click')
    expect(lastModel(wrapper)).toEqual({ plugins: [{ tag: 'lookup', type: 'reverse_lookup', future: false, args: {} }, { tag: 'extra', type: 'future', args: 0 }] })
  })
})
