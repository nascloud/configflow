import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { agentApi } from '@/api'
import { useAgentUpgrades } from '@/composables/useAgentUpgrades'
vi.mock('@/api', () => ({ agentApi: { getUpgrade: vi.fn(), update: vi.fn() } }))
const wrappers: ReturnType<typeof mount>[] = []
function setup() {
 let flow!: ReturnType<typeof useAgentUpgrades>
 const success = vi.fn()
 wrappers.push(mount(defineComponent({setup(){flow=useAgentUpgrades(success);return()=>null}})))
 return {flow,success}
}
const state = (status: string) => ({update_id:'same-upgrade',target_version:'1.3.0-go',previous_version:'1.0.8-go',status})
beforeEach(()=>{vi.useFakeTimers();vi.resetAllMocks()})
afterEach(()=>{wrappers.splice(0).forEach(w=>w.unmount());vi.useRealTimers()})
it('acceptance and temporary disconnection never imply success', async()=>{
 const {flow,success}=setup()
 vi.mocked(agentApi.update).mockResolvedValue({data:state('queued')} as any)
 vi.mocked(agentApi.getUpgrade).mockRejectedValueOnce(new Error('restart')).mockResolvedValueOnce({data:state('succeeded')} as any)
 await flow.start('a');expect(success).not.toHaveBeenCalled()
 await vi.advanceTimersByTimeAsync(1500);expect(flow.busy('a')).toBe(true);expect(success).not.toHaveBeenCalled()
 await vi.advanceTimersByTimeAsync(1500);expect(success).toHaveBeenCalledTimes(1);expect(flow.busy('a')).toBe(false)
 flow.track('a',state('checking'));expect(flow.upgrades.a.status).toBe('succeeded')
})
it('restores a persisted task and keeps failed rollback blocked', async()=>{
 const {flow,success}=setup()
 vi.mocked(agentApi.getUpgrade).mockResolvedValue({data:state('rollback_failed')} as any)
 flow.track('a',state('unknown'))
 await vi.advanceTimersByTimeAsync(1500);expect(agentApi.getUpgrade).not.toHaveBeenCalled()
 await vi.advanceTimersByTimeAsync(13500)
 expect(flow.busy('a')).toBe(true);expect(success).not.toHaveBeenCalled()
 await vi.advanceTimersByTimeAsync(10000);expect(agentApi.getUpgrade).toHaveBeenCalledTimes(1)
})
it('closing the page stops polling without submitting another update',async()=>{
 const {flow}=setup();flow.track('a',state('checking'))
 wrappers.splice(0).forEach(w=>w.unmount())
 await vi.advanceTimersByTimeAsync(10000)
 expect(agentApi.getUpgrade).not.toHaveBeenCalled();expect(agentApi.update).not.toHaveBeenCalled()
})
it('prevents duplicate submission while the first request is pending', async()=>{
 const {flow}=setup()
 let resolve!: (value: any)=>void
 vi.mocked(agentApi.update).mockImplementation(()=>new Promise(r=>{resolve=r}))
 const first=flow.start('a');await flow.start('a')
 expect(agentApi.update).toHaveBeenCalledTimes(1);expect(flow.busy('a')).toBe(true)
 resolve({data:state('queued')});await first
 expect(flow.busy('a')).toBe(true)
})
it('manual query returns the latest state even while background polling is pending', async()=>{
 const {flow}=setup()
 vi.mocked(agentApi.getUpgrade).mockResolvedValue({data:state('failed')} as any)
 flow.track('a',state('unknown'))
 const pending=flow.query('a');expect(flow.querying.has('a')).toBe(true)
 expect((await pending)?.status).toBe('failed')
 expect(flow.querying.has('a')).toBe(false);expect(flow.busy('a')).toBe(false)
 await vi.advanceTimersByTimeAsync(20000);expect(agentApi.getUpgrade).toHaveBeenCalledTimes(1)
})
it('manual query surfaces request errors and resumes polling', async()=>{
 const {flow}=setup()
 vi.mocked(agentApi.getUpgrade).mockRejectedValueOnce(new Error('offline')).mockResolvedValue({data:state('unknown')} as any)
 flow.track('a',state('unknown'))
 await expect(flow.query('a')).rejects.toThrow('offline')
 expect(flow.querying.has('a')).toBe(false)
 await vi.advanceTimersByTimeAsync(15000);expect(agentApi.getUpgrade).toHaveBeenCalledTimes(2)
})
