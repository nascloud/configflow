import { beforeAll, beforeEach, afterEach, it, expect, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { AxiosHeaders, type AxiosResponse } from 'axios'
import type { Component } from 'vue'
import type { ProxyNode, ProxyGroup } from '@/types'
import { createMemoryHistory, createRouter } from 'vue-router'
import Resources from '@/views/Resources.vue'
import Nodes from '@/views/Nodes.vue'
import ConfirmHost from '@/components/feedback/ConfirmHost.vue'
import api, { nodeApi, profileApi, proxyGroupApi, subStoreUrlApi } from '@/api'
import { notify, settleConfirm } from '@/lib/feedback'
import { setActiveProfileId } from '@/profileContext'

vi.mock('@/api', () => ({
  default: { get: vi.fn(), put: vi.fn(), delete: vi.fn() },
  nodeApi: { getAll: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
  proxyGroupApi: { getAll: vi.fn() },
  subStoreUrlApi: { get: vi.fn() },
  profileApi: { list: vi.fn(), getResources: vi.fn(), saveResources: vi.fn(), getNodeDialers: vi.fn(), saveNodeDialers: vi.fn() }
}))
type GroupFixture = Pick<ProxyGroup, 'id' | 'name'> & Partial<ProxyGroup> & { proxies_order?: { type: string; id: string }[] }
let rows: ProxyNode[], groups: GroupFixture[]
let refs: { subscriptions: string[]; nodes: string[]; subscription_aggregations: string[] }
let dialers: Record<string, { type: 'node' | 'group'; id: string }>
let wrapper: VueWrapper | undefined, host: VueWrapper | undefined
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value))
function response<T>(data: T): AxiosResponse<T> {
  return { data, status: 200, statusText: 'OK', headers: new AxiosHeaders(), config: { headers: new AxiosHeaders() } }
}
beforeAll(() => {
  Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })) })
})
beforeEach(() => {
  vi.resetAllMocks()
  setActiveProfileId('default')
  rows = [
    { id: 'exit', name: 'Exit', enabled: true, proxy_string: '{"type":"http","server":"example.test","port":80}' },
    { id: 'relay', name: 'Relay', enabled: true, proxy_string: 'http://example.test:80' },
    { id: 'disabled', name: 'Disabled', enabled: false, proxy_string: '' },
    { id: 'unselected', name: 'Unselected', enabled: true, proxy_string: '' }
  ]
  refs = { subscriptions: ['sub'], nodes: ['exit', 'relay', 'disabled'], subscription_aggregations: [] }
  dialers = {}
  groups = [
    { id: 'static', name: 'Static', type: 'select', enabled: true, manual_nodes: ['relay'] },
    { id: 'dynamic', name: 'Dynamic', enabled: true, subscriptions: ['sub'] }
  ]
  vi.mocked(nodeApi.getAll).mockImplementation(async () => response(clone(rows)))
  vi.mocked(api.get).mockImplementation(async path => response(path === '/subscriptions' ? [{ id: 'sub', name: 'Subscription' }] : [{ id: 'agg', name: 'Aggregation' }]))
  vi.mocked(profileApi.getResources).mockImplementation(async () => response(clone(refs)))
  vi.mocked(profileApi.getNodeDialers).mockImplementation(async () => response(clone(dialers)))
  vi.mocked(proxyGroupApi.getAll).mockImplementation(async () => response(clone(groups)))
  vi.mocked(profileApi.saveResources).mockResolvedValue(response({}))
  vi.mocked(profileApi.saveNodeDialers).mockResolvedValue(response({}))
  vi.mocked(subStoreUrlApi.get).mockResolvedValue(response({ sub_store_url: 'https://synthetic.test' }))
  vi.mocked(nodeApi.update).mockResolvedValue(response({}))
  vi.spyOn(notify, 'error').mockImplementation(() => 0)
  vi.spyOn(notify, 'success').mockImplementation(() => 0)
})
afterEach(() => {
  wrapper?.unmount()
  host?.unmount()
  wrapper = host = undefined
  settleConfirm('cancel')
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})
async function render(component: Component = Resources) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component }] })
  await router.push('/')
  await router.isReady()
  wrapper = mount(component, { attachTo: document.body, global: { plugins: [router] } })
  await flushPromises()
  return wrapper
}
async function click(selector: string) {
  const element = document.querySelector(selector) as HTMLElement
  expect(element).toBeTruthy()
  element.click()
  await flushPromises()
}
async function button(text: string) {
  const element = [...document.querySelectorAll('button')].find(element => element.textContent?.trim() === text)
  expect(element, text).toBeTruthy()
  element!.click()
  await flushPromises()
}
async function editExit() { await click('[aria-label="编辑拨号代理 Exit"]') }
function label() { return document.querySelector('[data-testid="dialer-trigger"]')!.textContent?.trim() }
async function openOptions() {
  document.querySelector('[data-testid="dialer-trigger"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
  await flushPromises()
  return [...document.querySelectorAll('[role="option"]')]
}
async function selectOption(option: Element) {
  ;(option as HTMLElement).focus()
  option.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  await flushPromises()
}
async function search(value: string) {
  const input = document.querySelector('[aria-label="搜索拨号代理"]') as HTMLInputElement
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}

it.each<[('node' | 'group'), string, string]>([
  ['node', 'relay', '节点 · Relay'],
  ['group', 'static', '策略组 · Static']
])('persisted %s reference resolves by ID, cancels without mutation, and saves only to its profile', async (type, id, text) => {
  dialers.exit = { id, type }
  rows[0].proxy_string = '{"type":"http","server":"example.test","port":80,"dialer-proxy":"DIRECT"}'
  const original = clone(rows)
  await render()
  await editExit()
  expect(label()).toBe(text)
  expect(document.body.textContent).toContain('原始 dialer-proxy：DIRECT；当前由稳定引用覆盖')
  const options = await openOptions()
  expect(options.find(option => option.textContent?.trim() === text)?.getAttribute('aria-selected')).toBe('true')
  document.querySelector('[data-testid="dialer-trigger"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  await flushPromises()
  await button('取消')
  expect(profileApi.saveNodeDialers).not.toHaveBeenCalled()
  await editExit()
  expect(label()).toBe(text)
  await button('保存')
  expect(profileApi.saveNodeDialers).toHaveBeenCalledWith('default', { exit: { type, id } })
  expect(nodeApi.update).not.toHaveBeenCalled()
  expect(rows).toEqual(original)
})

it.each<[('node' | 'group'), string, string]>([
  ['node', 'relay', '节点 · Relay'],
  ['group', 'static', '策略组 · Static']
])('selected %s label survives search and target rename without changing its ID', async (type, id, text) => {
  dialers.exit = { id, type }
  await render()
  await editExit()
  await search('no matches')
  expect(label()).toBe(text)
  await button('取消')
  if (type === 'node') rows[1].name = 'Renamed relay'
  else groups[0].name = 'Renamed group'
  wrapper!.unmount()
  await render()
  await editExit()
  expect(label()).toBe(type === 'node' ? '节点 · Renamed relay' : '策略组 · Renamed group')
  await button('保存')
  expect(profileApi.saveNodeDialers).toHaveBeenCalledWith('default', { exit: { type, id } })
})

it.each<[('node' | 'group'), string, string]>([
  ['node', 'missing', '节点 · missing（不可用）'],
  ['node', 'disabled', '节点 · Disabled（不可用）'],
  ['node', 'unselected', '节点 · Unselected（不可用）'],
  ['group', 'missing', '策略组 · missing（不可用）'],
  ['group', 'static', '策略组 · Static（不可用）']
])('unavailable %s %s is explicit and never silently cleared', async (type, id, text) => {
  dialers.exit = { id, type }
  if (type === 'group' && id === 'static') groups[0].enabled = false
  await render()
  await editExit()
  expect(label()).toBe(text)
  expect(document.body.textContent).toContain('当前稳定引用目标不可用，请重新选择或清除覆盖；不会自动清除。')
  await button('保存')
  expect(profileApi.saveNodeDialers).toHaveBeenCalledWith('default', { exit: { type, id } })
})

it('does not offer editing or saving until the entire profile resource snapshot resolves', async () => {
  dialers.exit = { id: 'static', type: 'group' }
  const pendingGroups = Promise.withResolvers<AxiosResponse<GroupFixture[]>>()
  vi.mocked(proxyGroupApi.getAll).mockReturnValue(pendingGroups.promise)
  await render()
  expect(document.querySelector('[aria-label="编辑拨号代理 Exit"]')).toBeNull()
  const save = [...document.querySelectorAll('button')].find(element => element.textContent?.trim() === '保存选择')!
  expect(save.disabled).toBe(true)
  expect(profileApi.saveNodeDialers).not.toHaveBeenCalled()
  pendingGroups.resolve(response(groups))
  await flushPromises()
  await editExit()
  expect(label()).toBe('策略组 · Static')
})

it('picker saves a selected stable node and excludes self, disabled, unselected and dynamic candidates', async () => {
  await render()
  await editExit()
  const options = await openOptions()
  expect(options.map(option => option.textContent?.trim())).toEqual(['不覆盖（保留原始值）', '节点 · Relay', '策略组 · Static'])
  await selectOption(options[1])
  await button('保存')
  expect(profileApi.saveNodeDialers).toHaveBeenCalledWith('default', { exit: { type: 'node', id: 'relay' } })
  expect(nodeApi.update).not.toHaveBeenCalled()
})

it('search filters candidates and static groups exclude nested dynamic, disabled, unselected and cyclic members', async () => {
  groups.push(
    { id: 'nested', name: 'Nested dynamic', proxies_order: [{ type: 'strategy', id: 'dynamic' }] },
    { id: 'disabled-member', name: 'Disabled member', manual_nodes: ['disabled'] },
    { id: 'unselected-member', name: 'Unselected member', manual_nodes: ['unselected'] },
    { id: 'cycle', name: 'Cycle', include_groups: ['cycle'] }
  )
  await render()
  await editExit()
  await search('Static')
  let options = await openOptions()
  expect(options.map(option => option.textContent?.trim())).toEqual(['不覆盖（保留原始值）', '策略组 · Static'])
  await selectOption(options[1])
  await search('')
  options = await openOptions()
  expect(options.map(option => option.textContent?.trim())).toEqual(['不覆盖（保留原始值）', '节点 · Relay', '策略组 · Static'])
  document.querySelector('[data-testid="dialer-trigger"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
  await flushPromises()
  await button('保存')
  expect(profileApi.saveNodeDialers).toHaveBeenCalledWith('default', { exit: { type: 'group', id: 'static' } })
})

it('clearing override preserves other mappings and raw proxy without mutating shared nodes', async () => {
  dialers = { exit: { type: 'node', id: 'relay' }, disabled: { type: 'group', id: 'static' } }
  rows[0].proxy_string = '{"type":"http","server":"example.test","port":80,"dialer-proxy":"Relay"}'
  const original = clone(rows)
  await render()
  await editExit()
  expect(document.body.textContent).toContain('原始 dialer-proxy：Relay；当前由稳定引用覆盖')
  await button('清除覆盖（恢复原始值）')
  expect(label()).toBe('不覆盖（保留原始值）')
  expect(document.body.textContent).toContain('原始 dialer-proxy：Relay；将保留原始值')
  await button('取消')
  expect(profileApi.saveNodeDialers).not.toHaveBeenCalled()
  await editExit()
  expect(label()).toBe('节点 · Relay')
  await button('清除覆盖（恢复原始值）')
  await button('保存')
  expect(profileApi.saveNodeDialers).toHaveBeenCalledWith('default', { disabled: { type: 'group', id: 'static' } })
  expect(rows).toEqual(original)
  expect(nodeApi.update).not.toHaveBeenCalled()
})

it.each([
  { source: 'params-only', proxyString: '', expected: '原始 dialer-proxy：ParamsRelay；将保留原始值' },
  { source: 'URI', proxyString: 'http://example.test:80', expected: null },
  { source: 'structured without raw dialer', proxyString: '{"type":"http","server":"example.test","port":80}', expected: null },
  { source: 'structured with raw dialer', proxyString: '{"type":"http","server":"example.test","port":80,"dialer-proxy":"StringRelay"}', expected: '原始 dialer-proxy：StringRelay；将保留原始值' }
])('raw dialer display follows backend source precedence for $source', async ({ proxyString, expected }) => {
  rows[0].proxy_string = proxyString
  rows[0].params = { 'dialer-proxy': 'ParamsRelay' }
  dialers.exit = { type: 'node', id: 'relay' }
  const original = clone(rows)
  await render()
  await editExit()
  await button('清除覆盖（恢复原始值）')
  if (expected) expect(document.body.textContent).toContain(expected)
  else expect(document.body.textContent).not.toContain('原始 dialer-proxy：')
  await button('保存')
  expect(profileApi.saveNodeDialers).toHaveBeenCalledWith('default', {})
  expect(nodeApi.update).not.toHaveBeenCalled()
  expect(rows).toEqual(original)
})

it('includes saved aggregation dependencies in dialer candidates without adding direct source references', async () => {
  refs.subscription_aggregations = ['agg']
  vi.mocked(api.get).mockImplementation(async path => response(path === '/subscriptions'
    ? [{ id: 'sub', name: 'Subscription' }]
    : [{ id: 'agg', name: 'Aggregation', nodes: ['unselected'] }]))
  await render()
  expect(document.querySelector('[aria-label="编辑拨号代理 Unselected"]')).not.toBeNull()
  await editExit()
  const options = await openOptions()
  const dependency = options.find(option => option.textContent?.trim() === '节点 · Unselected')
  expect(dependency).toBeTruthy()
  await selectOption(dependency!)
  await button('保存')
  expect(profileApi.saveNodeDialers).toHaveBeenCalledWith('default', { exit: { type: 'node', id: 'unselected' } })
  expect(profileApi.saveResources).not.toHaveBeenCalled()
  expect(nodeApi.update).not.toHaveBeenCalled()
})

it('saves reference-only resource selection without copying catalog or clearing dialers', async () => {
  dialers.exit = { type: 'node', id: 'relay' }
  await render()
  expect(profileApi.getResources).toHaveBeenCalledWith('default')
  expect(profileApi.getNodeDialers).toHaveBeenCalledWith('default')
  await click('[aria-label="选择聚合 Aggregation"]')
  await click('[aria-label="选择节点 Unselected"]')
  expect(document.querySelector('[aria-label="编辑拨号代理 Unselected"]')).toBeNull()
  await button('保存选择')
  expect(profileApi.saveResources).toHaveBeenCalledWith('default', {
    subscriptions: ['sub'], nodes: ['exit', 'relay', 'disabled', 'unselected'], subscription_aggregations: ['agg']
  })
  expect(document.querySelector('[aria-label="编辑拨号代理 Unselected"]')).not.toBeNull()
  expect(profileApi.saveNodeDialers).not.toHaveBeenCalled()
  expect(nodeApi.update).not.toHaveBeenCalled()
})

it('resource conflict keeps persisted dialer choices and displays backend reference guidance', async () => {
  vi.mocked(profileApi.saveResources).mockRejectedValue({ response: { data: { message: 'Relay 仍被配置策略组引用' } } })
  await render()
  await click('[aria-label="选择节点 Relay"]')
  await button('保存选择')
  expect(notify.error).toHaveBeenCalledWith('Relay 仍被配置策略组引用')
  await editExit()
  expect((await openOptions()).map(option => option.textContent?.trim())).toContain('节点 · Relay')
})

it('retains captured profile identity for both saves after active profile changes', async () => {
  setActiveProfileId('profile-a')
  await render()
  expect(proxyGroupApi.getAll).toHaveBeenCalledWith('profile-a')
  setActiveProfileId('profile-b')
  await click('[aria-label="选择聚合 Aggregation"]')
  await button('保存选择')
  expect(profileApi.saveResources).toHaveBeenCalledWith('profile-a', expect.anything())
  await editExit()
  await selectOption((await openOptions())[1])
  await button('保存')
  expect(profileApi.saveNodeDialers).toHaveBeenCalledWith('profile-a', { exit: { type: 'node', id: 'relay' } })
})

it('dialer save failure shows validation guidance and keeps the draft open without shared writes', async () => {
  await render()
  await editExit()
  await selectOption((await openOptions())[1])
  vi.mocked(profileApi.saveNodeDialers).mockRejectedValue({ response: { data: { message: '拨号代理依赖存在循环' } } })
  await button('保存')
  expect(notify.error).toHaveBeenCalledWith('拨号代理依赖存在循环')
  expect(document.querySelector('[role="dialog"]')).not.toBeNull()
  expect(label()).toBe('节点 · Relay')
  expect(nodeApi.update).not.toHaveBeenCalled()
})

it('shared node editing preserves raw dialer-proxy and never loads or submits profile dialers', async () => {
  rows[0].proxy_string = '{"type":"http","server":"example.test","port":80,"dialer-proxy":"DIRECT"}'
  await render(Nodes)
  await click('[aria-label="编辑 Exit"]')
  expect(document.querySelector('[data-testid="dialer-trigger"]')).toBeNull()
  await button('保存')
  const payload = vi.mocked(nodeApi.update).mock.calls[0][1]
  expect(JSON.parse(payload.proxy_string)['dialer-proxy']).toBe('DIRECT')
  expect(payload).not.toHaveProperty('dialer_ref')
  expect(profileApi.getNodeDialers).not.toHaveBeenCalled()
  expect(profileApi.saveNodeDialers).not.toHaveBeenCalled()
  expect(proxyGroupApi.getAll).not.toHaveBeenCalled()
})

it('shared batch delete conflict never rewrites groups and shows backend guidance', async () => {
  await render(Nodes)
  host = mount(ConfirmHost, { attachTo: document.body })
  vi.mocked(nodeApi.delete).mockRejectedValue({ response: { data: { message: '节点仍被拨号代理引用' } } })
  await button('全选')
  await button('删除 4 项')
  await button('删除')
  expect(api.put).not.toHaveBeenCalled()
  expect(notify.error).toHaveBeenCalledWith('节点仍被拨号代理引用')
})

it('shared node disable conflict restores enabled row and shows backend guidance', async () => {
  await render(Nodes)
  vi.mocked(nodeApi.update).mockRejectedValue({ response: { data: { message: '节点仍被拨号代理引用' } } })
  await click('[aria-label="停用 Relay"]')
  expect(document.querySelector('[aria-label="停用 Relay"]')).not.toBeNull()
  expect(notify.error).toHaveBeenCalledWith('节点仍被拨号代理引用')
})
