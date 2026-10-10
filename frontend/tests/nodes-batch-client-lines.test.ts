import { beforeAll, beforeEach, afterEach, it, expect, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import * as yaml from 'js-yaml'
import Nodes from '@/views/Nodes.vue'
import api, { nodeApi } from '@/api'
import { notify } from '@/lib/feedback'

vi.mock('@/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  nodeApi: { getAll: vi.fn(), create: vi.fn(), update: vi.fn(), delete: vi.fn(), latency: vi.fn(), parseLines: vi.fn() },
  profileApi: { list: vi.fn() }
}))

const snellLine = 'HK Snell = snell, 1.2.3.4, 443, psk=abcd, version=4, obfs=http, obfs-host=bing.com'
const snellProxy = { name: 'HK Snell', type: 'snell', server: '1.2.3.4', port: 443, psk: 'abcd', version: 4 }
let wrapper: VueWrapper | undefined

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', { configurable: true, value: vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })) })
})
beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(nodeApi.getAll).mockResolvedValue({ data: [] } as any)
  vi.mocked(nodeApi.latency).mockResolvedValue({ data: { results: {} } } as any)
  vi.mocked(nodeApi.create).mockResolvedValue({ data: {} } as any)
  vi.mocked(api.get).mockResolvedValue({ data: [] } as any)
  vi.spyOn(notify, 'success').mockImplementation(() => 0)
  vi.spyOn(notify, 'warning').mockImplementation(() => 0)
  vi.spyOn(notify, 'error').mockImplementation(() => 0)
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = undefined
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

async function openBatchAndSubmit(text: string) {
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: Nodes }] })
  await router.push('/')
  await router.isReady()
  wrapper = mount(Nodes, { attachTo: document.body, global: { plugins: [router] } })
  await flushPromises()
  const clickButton = async (label: string) => {
    const button = [...document.querySelectorAll('button')].find(el => el.textContent?.trim() === label)
    expect(button, label).toBeTruthy()
    button!.click()
    await flushPromises()
  }
  await clickButton('批量添加')
  const textarea = document.querySelector('#batch-nodes') as HTMLTextAreaElement
  textarea.value = text
  textarea.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
  const submit = [...document.querySelectorAll('[role="dialog"] button')].find(el => el.textContent?.trim() === '批量添加') as HTMLElement
  submit.click()
  await flushPromises()
}

it('批量添加的 snell 节点行解析成结构化节点，保留名称', async () => {
  vi.mocked(nodeApi.parseLines).mockResolvedValue({ data: { success: true, results: [{ proxy: snellProxy }] } } as any)

  await openBatchAndSubmit(`${snellLine}\nss://YWVzLTEyOC1nY206cGFzcw@5.6.7.8:8388#SS`)

  expect(nodeApi.parseLines).toHaveBeenCalledWith([snellLine])
  const created = vi.mocked(nodeApi.create).mock.calls.map(call => call[0] as any)
  expect(created).toHaveLength(2)
  expect(created[0].name).toBe('HK Snell')
  expect(yaml.load(created[0].proxy_string)).toEqual(snellProxy)
  // URI 仍按原样保存，不经过节点行解析
  expect(created[1]).toMatchObject({ name: 'SS', proxy_string: 'ss://YWVzLTEyOC1nY206cGFzcw@5.6.7.8:8388#SS' })
})

it('无法解析的节点行计为失败，不保存原始文本', async () => {
  vi.mocked(nodeApi.parseLines).mockResolvedValue({ data: { success: true, results: [{ error: '无法识别的节点格式' }] } } as any)

  await openBatchAndSubmit(snellLine)

  expect(nodeApi.create).not.toHaveBeenCalled()
  expect(notify.error).toHaveBeenCalled()
})
