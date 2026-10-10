import { onUnmounted, reactive } from 'vue'
import { agentApi } from '@/api'

export interface AgentUpgrade {
  update_id: string
  target_version: string
  previous_version: string
  status: string
  error?: string
  updated_at?: number
}

export const upgradeLabels: Record<string, string> = {
  queued: '等待更新', downloading: '下载更新', verifying: '校验更新',
  backing_up: '备份旧版本', migrating: '迁移配置和恢复服务', replacing: '替换程序',
  restarting: '重启 Agent', checking: '等待新版确认', rolling_back: '恢复旧版本',
  succeeded: '更新成功', failed: '更新失败', rolled_back: '更新失败，已回滚',
  rollback_failed: '回滚失败，需要处理', unknown: '结果待确认，继续查询',
}
const terminal = new Set(['succeeded', 'failed', 'rolled_back', 'rollback_failed'])

export function useAgentUpgrades(onSuccess: () => void) {
  const upgrades = reactive<Record<string, AgentUpgrade>>({})
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const inflight = new Set<string>()
  const submitting = reactive(new Set<string>())
  let disposed = false
  const busy = (id: string) => submitting.has(id) || !!upgrades[id] && (!terminal.has(upgrades[id].status) || upgrades[id].status === 'rollback_failed')

  function schedule(id: string) {
    if (disposed || timers.has(id) || !busy(id) || upgrades[id].status === 'rollback_failed') return
    timers.set(id, setTimeout(() => { timers.delete(id); void refresh(id) }, 1500))
  }
  function track(id: string, state: AgentUpgrade) {
    const previous = upgrades[id]
    if (previous?.update_id === state.update_id && terminal.has(previous.status) && !terminal.has(state.status)) return
    if (previous?.update_id !== state.update_id && previous?.updated_at && state.updated_at && state.updated_at < previous.updated_at) return
    upgrades[id] = state
    if (state.status === 'succeeded' && previous?.status !== 'succeeded') onSuccess()
    schedule(id)
  }
  async function refresh(id: string) {
    if (disposed || inflight.has(id)) return
    inflight.add(id)
    try {
      const { data } = await agentApi.getUpgrade(id)
      if (!disposed) track(id, data)
    } catch {
      // Losing contact during restart is expected. Keep the durable task pending.
    } finally {
      inflight.delete(id)
      schedule(id)
    }
  }
  async function start(id: string) {
    if (busy(id)) return
    submitting.add(id)
    try {
      const { data } = await agentApi.update(id)
      track(id, data)
    } finally { submitting.delete(id) }
  }
  onUnmounted(() => { disposed = true; timers.forEach(clearTimeout); timers.clear() })
  return { upgrades, busy, track, refresh, start }
}
