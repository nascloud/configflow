import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import SubStoreRuntime from '@/components/settings/SubStoreRuntime.vue'
import { dependenciesApi, type DependencyStatus } from '@/api'
import { notify } from '@/lib/feedback'

vi.mock('@/api', () => ({ dependenciesApi: { setSubStoreMode: vi.fn() } }))
vi.mock('@/lib/feedback', () => ({ notify: { success: vi.fn(), error: vi.fn() } }))
const wrappers: ReturnType<typeof mount>[] = []
const dependency = (extra = {}) => ({ key: 'sub-store', running: false,
  lifecycle: { managed: true, mode: 'on_demand', state: 'sleeping', idle_seconds: 300, error: '' },
  update: { state: 'idle' }, ...extra } as DependencyStatus)
function render(dep = dependency()) {
  const wrapper = mount(SubStoreRuntime, { props: { dependency: dep }, attachTo: document.body })
  wrappers.push(wrapper)
  return wrapper
}
beforeEach(() => {
  vi.clearAllMocks()
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
})
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); vi.unstubAllGlobals() })

describe('Sub-Store runtime settings', () => {
  it('shows idle as normal and does not wake the service on mount', () => {
    const wrapper = render()
    expect(wrapper.text()).toContain('休眠中')
    expect(wrapper.text()).toContain('空闲 5 分钟')
    expect(wrapper.text()).not.toContain('无法连接')
    expect(dependenciesApi.setSubStoreMode).not.toHaveBeenCalled()
  })

  it('waits for a successful mode change and reports it', async () => {
    let done!: () => void
    vi.mocked(dependenciesApi.setSubStoreMode).mockReturnValue(new Promise(resolve => { done = () => resolve({ data: { success: true } } as any) }))
    const wrapper = render()
    await wrapper.findAll('[role="radio"]')[1].trigger('click')
    expect(dependenciesApi.setSubStoreMode).toHaveBeenCalledWith('always')
    expect(wrapper.text()).toContain('正在保存')
    expect(wrapper.findAll('button').every(button => button.attributes('disabled') !== undefined)).toBe(true)
    done(); await flushPromises()
    expect(wrapper.findAll('[role="radio"]')[1].attributes('aria-checked')).toBe('true')
    expect(wrapper.emitted('changed')).toHaveLength(1)
    expect(notify.success).toHaveBeenCalled()
  })

  it('retains the saved mode when switching fails', async () => {
    vi.mocked(dependenciesApi.setSubStoreMode).mockRejectedValue(new Error('unavailable'))
    const wrapper = render()
    await wrapper.findAll('[role="radio"]')[1].trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[role="radio"]')[0].attributes('aria-checked')).toBe('true')
    expect(wrapper.emitted('changed')).toBeUndefined()
    expect(notify.error).toHaveBeenCalled()
  })

  it('prevents mode changes while updating and explains startup failure', () => {
    const dep = dependency({ update: { state: 'running' } })
    dep.lifecycle = { ...dep.lifecycle!, state: 'failed', error: '启动未就绪，请检查容器日志' }
    const wrapper = render(dep)
    expect(wrapper.text()).toContain('无法连接')
    expect(wrapper.text()).toContain('启动未就绪')
    expect(wrapper.findAll('button').every(button => button.attributes('disabled') !== undefined)).toBe(true)
  })

  it('does not offer local process controls for an external server', () => {
    const dep = dependency()
    dep.lifecycle = { ...dep.lifecycle!, managed: false, mode: 'external', state: 'running' }
    const wrapper = render(dep)
    expect(wrapper.text()).toContain('运行中')
    expect(wrapper.find('[role="radiogroup"]').exists()).toBe(false)
  })
})
