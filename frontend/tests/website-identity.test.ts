import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import WebsiteDialog from '@/components/domain-discovery/WebsiteDialog.vue'
import api from '@/api'

vi.mock('@/api', () => ({ default: { post: vi.fn(), put: vi.fn() } }))
vi.mock('@/lib/feedback', () => ({ notify: { success: vi.fn(), error: vi.fn() } }))

const website = { service: null, page: null, note: '原备注' }
const slot = { template: '<div><slot /></div>' }
const render = () => mount(WebsiteDialog, {
  props: { domain: 'example.com', website, busy: false },
  global: { stubs: { Dialog: slot, DialogContent: slot, DialogHeader: slot, DialogFooter: slot,
    DialogTitle: slot, DialogDescription: slot } }
})

beforeEach(() => vi.clearAllMocks())

describe('website identity dialog', () => {
  it('clears a note and reports the saved value without touching probe data', async () => {
    vi.mocked(api.put).mockResolvedValue({ data: { note: '' } })
    const wrapper = render()
    await wrapper.get('input').setValue('')
    await wrapper.findAll('button').find(button => button.text().includes('保存备注'))!.trigger('click')
    await flushPromises()
    expect(api.put).toHaveBeenCalledWith('/domain-discovery/website/note', { domain: 'example.com', note: '' })
    expect(wrapper.emitted('saved')?.[0]).toEqual(['example.com', ''])
    expect(api.post).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('starts a job and disables repeated identification while a probe is running', async () => {
    const job = { id: 'job1', kind: 'website', status: 'running' }
    vi.mocked(api.post).mockResolvedValue({ data: { job } })
    const wrapper = render()
    const button = wrapper.findAll('button').find(button => button.text().includes('识别网站'))!
    await button.trigger('click')
    await flushPromises()
    expect(api.post).toHaveBeenCalledWith('/domain-discovery/website/identify', { domains: ['example.com'] })
    expect(wrapper.emitted('job')?.[0]).toEqual([job])
    await wrapper.setProps({ busy: true })
    expect(button.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('shows probe failure and resets the note when opening a different domain', async () => {
    const wrapper = render()
    await wrapper.get('input').setValue('未保存')
    await wrapper.setProps({ domain: 'other.test', website: { ...website, note: '', page: {
      name: '', description: '', reason: '页面要求人机验证，无法识别', host: 'api.other.test', checked_at: new Date().toISOString()
    } } })
    expect(wrapper.get('input').element.value).toBe('')
    expect(wrapper.text()).toContain('页面要求人机验证，无法识别')
    expect(wrapper.text()).toContain('api.other.test')
    wrapper.unmount()
  })
})
