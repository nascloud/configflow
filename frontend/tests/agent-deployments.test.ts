import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { agentApi } from '@/api'
import { notify } from '@/lib/feedback'
import { useAgentDeployments } from '@/composables/useAgentDeployments'

vi.mock('@/api', () => ({ agentApi: { getDeployment: vi.fn(), activateDeployment: vi.fn(), pushConfig: vi.fn() } }))
vi.mock('@/lib/feedback', () => ({ notify: { success: vi.fn(), error: vi.fn() } }))
const wrappers: ReturnType<typeof mount>[] = []
function render() {
  let flow!: ReturnType<typeof useAgentDeployments>
  const onSuccess = vi.fn()
  wrappers.push(mount(defineComponent({ setup() { flow = useAgentDeployments(onSuccess); return () => null } })))
  return { flow, onSuccess }
}
beforeEach(() => { vi.useFakeTimers(); vi.clearAllMocks() })
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.useRealTimers() })

describe('Agent deployment lifecycle feedback', () => {
  it('does not claim success on receipt; waits for core health checks to pass', async () => {
    const { flow, onSuccess } = render()
    vi.mocked(agentApi.getDeployment)
      .mockResolvedValueOnce({ data: { deployment_id: 'publish-1', status: 'checking' } } as any)
      .mockResolvedValueOnce({ data: { deployment_id: 'publish-1', status: 'succeeded' } } as any)
    flow.track('agent-1', { deployment_id: 'publish-1', status: 'receiving' })
    await vi.advanceTimersByTimeAsync(0)
    expect(flow.deployments['agent-1'].status).toBe('checking')
    expect(notify.success).not.toHaveBeenCalled()
    expect(flow.busy('agent-1')).toBe(true)
    await vi.advanceTimersByTimeAsync(2000)
    expect(notify.success).toHaveBeenCalledTimes(1)
    expect(onSuccess).toHaveBeenCalledTimes(1)
    expect(flow.busy('agent-1')).toBe(false)
  })

  it('keeps the same deployment across transport failures and reports rollback', async () => {
    const { flow } = render()
    vi.mocked(agentApi.getDeployment)
      .mockRejectedValueOnce(new Error('connection lost'))
      .mockResolvedValueOnce({ data: { deployment_id: 'publish-2', status: 'rolled_back', error: 'Core failed to start' } } as any)
    flow.track('agent-1', { deployment_id: 'publish-2', status: 'starting' })
    await vi.advanceTimersByTimeAsync(0)
    expect(flow.deployments['agent-1'].query_error).toBe(true)
    expect(flow.busy('agent-1')).toBe(true)
    expect(notify.error).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(5000)
    expect(agentApi.getDeployment).toHaveBeenLastCalledWith('agent-1', 'publish-2')
    expect(notify.error).toHaveBeenCalledWith('发布失败，已回滚', 'Core failed to start')
    expect(notify.success).not.toHaveBeenCalled()
  })

  it('activates a staged version through its existing deployment rather than a restart', async () => {
    const { flow } = render()
    flow.track('agent-1', { deployment_id: 'staged-1', status: 'ready' })
    vi.mocked(agentApi.activateDeployment).mockResolvedValueOnce({ data: { deployment_id: 'staged-1', status: 'starting' } } as any)
    await flow.activate('agent-1')
    expect(agentApi.activateDeployment).toHaveBeenCalledWith('agent-1', 'staged-1')
    expect(flow.busy('agent-1')).toBe(true)
    expect(notify.success).not.toHaveBeenCalled()
  })

  it('retries when the backend returns a cached state while the Agent is unreachable', async () => {
    const { flow } = render()
    vi.mocked(agentApi.getDeployment)
      .mockResolvedValueOnce({ data: { deployment_id: 'pending-1', status: 'checking', pending: true } } as any)
      .mockResolvedValueOnce({ data: { deployment_id: 'pending-1', status: 'succeeded' } } as any)
    flow.track('agent-1', { deployment_id: 'pending-1', status: 'checking' })
    await vi.advanceTimersByTimeAsync(0)
    expect(flow.deployments['agent-1'].query_error).toBe(true)
    expect(notify.success).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(5000)
    expect(flow.deployments['agent-1'].query_error).toBe(false)
    expect(notify.success).toHaveBeenCalledTimes(1)
  })

  it('resumes persisted tasks, and stops polling when the page is closed', async () => {
    const { flow } = render()
    vi.mocked(agentApi.getDeployment).mockResolvedValue({ data: { deployment_id: 'resume-1', status: 'checking' } } as any)
    flow.track('agent-1', { deployment_id: 'resume-1', status: 'starting' }, false)
    await vi.advanceTimersByTimeAsync(0)
    wrappers.splice(0).forEach(w => w.unmount())
    await flushPromises()
    await vi.advanceTimersByTimeAsync(10000)
    expect(agentApi.getDeployment).toHaveBeenCalledTimes(1)
    expect(notify.success).not.toHaveBeenCalled()
  })

  it('does not regress a completed publication after a late list response or poll failure', async () => {
    const { flow, onSuccess } = render()
    let rejectPoll!: (reason: unknown) => void
    vi.mocked(agentApi.getDeployment).mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectPoll = reject }))
    flow.track('agent-1', { deployment_id: 'complete-1', status: 'checking' })
    await vi.advanceTimersByTimeAsync(0)
    flow.track('agent-1', { deployment_id: 'complete-1', status: 'succeeded' })
    flow.track('agent-1', { deployment_id: 'complete-1', status: 'ready' }, false)
    rejectPoll(new Error('old poll failed'))
    await flushPromises()
    await vi.advanceTimersByTimeAsync(5000)
    expect(flow.deployments['agent-1'].status).toBe('succeeded')
    expect(flow.deployments['agent-1'].query_error).toBe(false)
    expect(agentApi.getDeployment).toHaveBeenCalledTimes(1)
    expect(onSuccess).toHaveBeenCalledTimes(1)
  })

  it('keeps a missing publication unknown and retries with its original identity', async () => {
    const { flow } = render()
    vi.mocked(agentApi.getDeployment)
      .mockRejectedValueOnce({ response: { status: 404 } })
      .mockResolvedValueOnce({ data: { deployment_id: 'missing-1', status: 'succeeded' } } as any)
    vi.mocked(agentApi.pushConfig).mockResolvedValueOnce({ data: { deployment_id: 'missing-1', status: 'checking' } } as any)
    flow.track('agent-1', { deployment_id: 'missing-1', status: 'unknown' })
    await vi.advanceTimersByTimeAsync(0)
    expect(flow.deployments['agent-1']).toMatchObject({ status: 'unknown', not_recorded: true })
    expect(flow.busy('agent-1')).toBe(true)
    expect(notify.error).not.toHaveBeenCalled()
    await flow.retryPublish('agent-1')
    expect(agentApi.pushConfig).toHaveBeenCalledExactlyOnceWith('agent-1', 'missing-1')
    expect(flow.deployments['agent-1'].status).toBe('checking')
    expect(flow.deployments['agent-1'].not_recorded).toBeFalsy()
    await vi.advanceTimersByTimeAsync(2000)
    expect(flow.deployments['agent-1'].status).toBe('succeeded')
  })

  it('prevents duplicate retry clicks and keeps querying the same ID when the retry times out', async () => {
    const { flow } = render()
    let rejectRetry!: (reason: unknown) => void
    vi.mocked(agentApi.getDeployment).mockRejectedValue({ response: { status: 404 } })
    vi.mocked(agentApi.pushConfig).mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRetry = reject }))
    flow.track('agent-1', { deployment_id: 'retry-1', status: 'unknown' })
    await vi.advanceTimersByTimeAsync(0)
    const retry = flow.retryPublish('agent-1')
    await flow.retryPublish('agent-1')
    expect(flow.deployments['agent-1'].retrying).toBe(true)
    expect(agentApi.pushConfig).toHaveBeenCalledExactlyOnceWith('agent-1', 'retry-1')
    rejectRetry(new Error('timeout'))
    await retry
    expect(flow.deployments['agent-1']).toMatchObject({ status: 'unknown', retrying: false, query_error: true })
    await vi.advanceTimersByTimeAsync(5000)
    expect(agentApi.getDeployment).toHaveBeenLastCalledWith('agent-1', 'retry-1')
    expect(flow.deployments['agent-1'].not_recorded).toBe(true)
    expect(notify.error).not.toHaveBeenCalled()
  })
})
