import { beforeAll, beforeEach, afterEach, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import Nodes from '@/views/Nodes.vue'
import ConfirmHost from '@/components/feedback/ConfirmHost.vue'
import api, { nodeApi, subStoreUrlApi } from '@/api'
import { notify, settleConfirm } from '@/lib/feedback'
import { setActiveProfileId } from '@/profileContext'
vi.mock('@/api', () => ({
 default: { get: vi.fn(), put: vi.fn(), delete: vi.fn() },
 nodeApi: { getAll: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn() },
 subStoreUrlApi: { get: vi.fn() }, profileApi: { list: vi.fn() }
}))
let rows: any[], groups: any[], wrapper: any, host: any
beforeAll(() => {
 Object.defineProperty(Element.prototype, 'scrollIntoView', { configurable: true, value: vi.fn() })
 Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })) })
})
beforeEach(() => {
 vi.resetAllMocks(); setActiveProfileId('default')
 rows = [{ id: 'exit', name: 'Exit', enabled: true, proxy_string: '{"type":"http","server":"example.test","port":80}' },
 { id: 'relay', name: 'Relay', enabled: true, proxy_string: 'http://example.test:80' },
 { id: 'disabled', name: 'Disabled', enabled: false, proxy_string: '' }]
 groups = [{ id: 'static', name: 'Static', type: 'select', enabled: true, manual_nodes: ['relay'] },
 { id: 'dynamic', name: 'Dynamic', enabled: true, subscriptions: ['sub'] }]
 vi.mocked(nodeApi.getAll).mockImplementation(async () => ({ data: JSON.parse(JSON.stringify(rows)) }) as any)
 vi.mocked(api.get).mockImplementation(async (path) => ({ data: path === '/proxy-groups' ? groups : [] }) as any)
 vi.mocked(subStoreUrlApi.get).mockResolvedValue({ data: { sub_store_url: 'https://synthetic.test' } } as any)
 vi.mocked(nodeApi.update).mockResolvedValue({ data: {} } as any)
 vi.spyOn(notify, 'error').mockImplementation(() => 0)
 vi.spyOn(notify, 'success').mockImplementation(() => 0)
})
afterEach(() => { wrapper?.unmount(); host?.unmount(); settleConfirm('cancel'); document.body.innerHTML = ''; vi.restoreAllMocks() })
async function render() { wrapper = mount(Nodes, { attachTo: document.body }); await flushPromises(); return wrapper }
async function click(selector: string) { const e = document.querySelector(selector) as HTMLElement; expect(e).toBeTruthy(); e.click(); await flushPromises() }
async function button(text: string) { const e = [...document.querySelectorAll('button')].find(e => e.textContent?.trim() === text); expect(e, text).toBeTruthy(); e!.click(); await flushPromises() }
it.each([
 ['node', 'relay', '节点 · Relay'],
 ['group', 'static', '策略组 · Static']
])('editing persisted %s reference displays its target before opening and on reopening', async (type, id, label) => {
 // API JSON object key order is not a stable selector identity.
 rows[0].dialer_ref = { id, type }
 rows[0].proxy_string = '{"type":"http","server":"example.test","port":80,"dialer-proxy":"DIRECT"}'
 const original = JSON.stringify(rows)
 await render(); await click('[aria-label="编辑 Exit"]')
 expect(document.querySelector('[data-testid="dialer-trigger"]')!.textContent?.trim()).toBe(label)
 expect(document.body.textContent).toContain('原始 dialer-proxy：DIRECT；当前由稳定引用覆盖')
 const trigger = document.querySelector('[data-testid="dialer-trigger"]')!
 trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })); await flushPromises()
 const selected = [...document.querySelectorAll('[role="option"]')].find(e => e.textContent?.trim() === label)
 expect(selected?.getAttribute('aria-selected')).toBe('true')
 trigger.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await flushPromises()
 await button('取消')
 expect(nodeApi.update).not.toHaveBeenCalled()
 expect(JSON.stringify(rows)).toBe(original)
 await click('[aria-label="编辑 Exit"]')
 expect(document.querySelector('[data-testid="dialer-trigger"]')!.textContent?.trim()).toBe(label)
 await button('保存')
 expect(nodeApi.update).toHaveBeenCalledWith('exit', expect.objectContaining({ dialer_ref: { type, id } }))
 expect(JSON.stringify(rows)).toBe(original)
})

it.each([
 ['node', 'relay', '节点 · Relay'],
 ['group', 'static', '策略组 · Static']
])('selected %s label survives search filtering and follows target rename without changing its ID', async (type, id, label) => {
 rows[0].dialer_ref = { id, type }
 await render(); await click('[aria-label="编辑 Exit"]')
 const search = document.querySelector('[aria-label="搜索拨号代理"]') as HTMLInputElement
 search.value = 'no matches'; search.dispatchEvent(new Event('input', { bubbles: true })); await flushPromises()
 expect(document.querySelector('[data-testid="dialer-trigger"]')!.textContent?.trim()).toBe(label)
 await button('取消')
 if (type === 'node') {
  rows[1].name = 'Renamed relay'
  // Remount to read renamed resource data through the API seam, without writes.
  wrapper.unmount(); await render()
 } else groups[0].name = 'Renamed group'
 await click('[aria-label="编辑 Exit"]')
 expect(document.querySelector('[data-testid="dialer-trigger"]')!.textContent?.trim()).toBe(type === 'node' ? '节点 · Renamed relay' : '策略组 · Renamed group')
 await button('保存')
 expect(nodeApi.update).toHaveBeenCalledWith('exit', expect.objectContaining({ dialer_ref: { type, id } }))
})

it.each([
 ['node', 'missing', '节点 · missing（不可用）'],
 ['node', 'disabled', '节点 · Disabled（不可用）'],
 ['group', 'missing', '策略组 · missing（不可用）'],
 ['group', 'static', '策略组 · Static（不可用）']
])('unavailable persisted %s %s is explicit and never automatically cleared', async (type, id, label) => {
 rows[0].dialer_ref = { id, type }
 if (type === 'group' && id === 'static') groups[0].enabled = false
 await render(); await click('[aria-label="编辑 Exit"]')
 expect(document.querySelector('[data-testid="dialer-trigger"]')!.textContent?.trim()).toBe(label)
 expect(document.body.textContent).toContain('当前稳定引用目标不可用，请重新选择或清除覆盖；不会自动清除。')
 await button('保存')
 expect(nodeApi.update).toHaveBeenCalledWith('exit', expect.objectContaining({ dialer_ref: { type, id } }))
})

it('persisted group remains an explicit loading reference until the group API resolves', async () => {
 rows[0].dialer_ref = { id: 'static', type: 'group' }
 let resolveGroups!: (value: any) => void
 vi.mocked(api.get).mockImplementation(() => new Promise(resolve => { resolveGroups = resolve }) as any)
 await render(); await click('[aria-label="编辑 Exit"]')
 expect(document.querySelector('[data-testid="dialer-trigger"]')!.textContent?.trim()).toBe('策略组 · static（加载中）')
 expect(document.querySelector('[role="alert"]')).toBeNull()
 expect(nodeApi.update).not.toHaveBeenCalled()
 resolveGroups({ data: groups }); await flushPromises()
 expect(document.querySelector('[data-testid="dialer-trigger"]')!.textContent?.trim()).toBe('策略组 · Static')
 await button('取消')
})

it('searchable picker saves stable node reference and excludes self, disabled and dynamic candidates', async () => {
 await render(); await click('[aria-label="编辑 Exit"]')
 await click('[data-testid="dialer-trigger"]')
 document.querySelector('[data-testid="dialer-trigger"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }))
 await flushPromises()
 const options = [...document.querySelectorAll('[role="option"]')]
 expect(options.map(e => e.textContent?.trim())).toEqual(['不覆盖（保留原始值）', '节点 · Relay', '策略组 · Static'])
 ;(options[1] as HTMLElement).focus()
 options[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
 await flushPromises(); await button('保存')
 expect(nodeApi.update).toHaveBeenCalledWith('exit', expect.objectContaining({ dialer_ref: { type: 'node', id: 'relay' } }))
 expect(api.put).not.toHaveBeenCalled()
})

it('search filters candidates and group picker excludes nested dynamic and disabled membership', async () => {
 groups.push({ id: 'nested', name: 'Nested dynamic', enabled: true, proxies_order: [{ type: 'strategy', id: 'dynamic' }] })
 groups.push({ id: 'disabled-member', name: 'Disabled member', enabled: true, manual_nodes: ['disabled'] })
 await render(); await click('[aria-label="编辑 Exit"]')
 const search = document.querySelector('[aria-label="搜索拨号代理"]') as HTMLInputElement
 search.value = 'Static'; search.dispatchEvent(new Event('input', { bubbles: true })); await flushPromises()
 document.querySelector('[data-testid="dialer-trigger"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })); await flushPromises()
 let options = [...document.querySelectorAll('[role="option"]')]
 expect(options.map(e => e.textContent?.trim())).toEqual(['不覆盖（保留原始值）', '策略组 · Static'])
 ;(options[1] as HTMLElement).focus(); options[1].dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true })); await flushPromises()
 search.value = ''; search.dispatchEvent(new Event('input', { bubbles: true })); await flushPromises()
 document.querySelector('[data-testid="dialer-trigger"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })); await flushPromises()
 options = [...document.querySelectorAll('[role="option"]')]
 expect(options.map(e => e.textContent?.trim())).toEqual(['不覆盖（保留原始值）', '节点 · Relay', '策略组 · Static'])
 document.querySelector('[data-testid="dialer-trigger"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await flushPromises()
 await button('保存')
 expect(nodeApi.update).toHaveBeenCalledWith('exit', expect.objectContaining({ dialer_ref: { type: 'group', id: 'static' } }))
})

it('clearing override explicitly restores raw value without mutating original row or raw proxy', async () => {
 rows[0].dialer_ref = { type: 'node', id: 'relay' }
 rows[0].proxy_string = '{"type":"http","server":"example.test","port":80,"dialer-proxy":"Relay"}'
 await render(); await click('[aria-label="编辑 Exit"]')
 expect(document.body.textContent).toContain('原始 dialer-proxy：Relay；当前由稳定引用覆盖')
 expect(document.querySelector('[data-testid="dialer-trigger"]')!.textContent?.trim()).toBe('节点 · Relay')
 await button('清除覆盖（恢复原始值）')
 expect(document.querySelector('[data-testid="dialer-trigger"]')!.textContent?.trim()).toBe('不覆盖（保留原始值）')
 expect(document.body.textContent).toContain('原始 dialer-proxy：Relay；将保留原始值')
 await button('取消')
 expect(nodeApi.update).not.toHaveBeenCalled()
 await click('[aria-label="编辑 Exit"]')
 expect(document.querySelector('[data-testid="dialer-trigger"]')!.textContent?.trim()).toBe('节点 · Relay')
 await button('清除覆盖（恢复原始值）')
 await button('保存')
 const payload: any = vi.mocked(nodeApi.update).mock.calls[0][1]
 expect(payload.dialer_ref).toBeNull()
 expect(JSON.parse(payload.proxy_string)['dialer-proxy']).toBe('Relay')
 expect(rows[0].dialer_ref).toEqual({ type: 'node', id: 'relay' })
})

it('batch delete conflict never rewrites groups and shows backend guidance', async () => {
 await render(); host = mount(ConfirmHost, { attachTo: document.body })
 vi.mocked(nodeApi.delete).mockRejectedValue({ response: { data: { message: '节点仍被拨号代理引用' } } })
 await button('全选'); await button('删除 3 项'); await button('删除')
 expect(api.put).not.toHaveBeenCalled()
 expect(notify.error).toHaveBeenCalledWith('节点仍被拨号代理引用')
})

it('disable conflict restores enabled row and shows backend guidance', async () => {
 await render()
 vi.mocked(nodeApi.update).mockRejectedValue({ response: { data: { message: '节点仍被拨号代理引用' } } })
 await click('[aria-label="停用 Relay"]')
 expect(document.querySelector('[aria-label="停用 Relay"]')).not.toBeNull()
 expect(notify.error).toHaveBeenCalledWith('节点仍被拨号代理引用')
})

it('save failure shows backend reference guidance and leaves edit dialog open', async () => {
 await render(); await click('[aria-label="编辑 Exit"]')
 vi.mocked(nodeApi.update).mockRejectedValue({ response: { data: { message: '拨号代理依赖存在循环' } } })
 await button('保存')
 expect(notify.error).toHaveBeenCalledWith('拨号代理依赖存在循环')
 expect(document.querySelector('[role="dialog"]')).not.toBeNull()
 expect(api.put).not.toHaveBeenCalled()
})
