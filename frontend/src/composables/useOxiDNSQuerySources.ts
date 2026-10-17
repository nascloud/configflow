import { onMounted, onUnmounted, ref, watch } from 'vue'
import { oxidnsMonitorApi } from '@/api'
import { querySourceLabel, type OxiDNSQueries, type OxiDNSQuery } from '@/types/oxidnsMonitor'

interface Source { label: string; failed?: boolean }
interface Input { agentId: string; recorder: string; instanceId?: string | null; data: OxiDNSQueries | null }
const keyOf = (record: OxiDNSQuery) => JSON.stringify([record.id, record.created_at_ms])

/** Native list rows omit steps. Fetch this page's summaries with bounded concurrency and reuse them on refresh. */
export function useOxiDNSQuerySources(input: Input) {
  const cache = ref(new Map<string, Source>())
  let scope = ''
  let controller: AbortController | undefined
  function load() {
    controller?.abort()
    const nextScope = JSON.stringify([input.agentId, input.recorder, input.instanceId])
    if (scope !== nextScope) { cache.value.clear(); scope = nextScope }
    if (!input.agentId || !input.recorder || document.hidden) return
    const request = controller = new AbortController()
    const agentId = input.agentId
    const recorder = input.recorder
    const pending = (input.data?.records || []).filter(row => row.has_response && !row.error && !row.steps && !cache.value.has(keyOf(row)))
    let next = 0
    async function worker() {
      while (!request.signal.aborted && next < pending.length) {
        const row = pending[next++]
        let source: Source
        try {
          const { data } = await oxidnsMonitorApi.query(agentId, row.id, recorder, { signal: request.signal })
          if (!data.success || String(data.record?.id) !== String(row.id) || data.record?.created_at_ms !== row.created_at_ms) throw new Error('记录已变化')
          source = { label: querySourceLabel(data.record) }
        } catch {
          source = { label: '读取失败', failed: true }
        }
        if (request.signal.aborted) return
        cache.value.set(keyOf(row), source)
        // Bound memory across pagination. Scope also includes the core instance.
        while (cache.value.size > 200) cache.value.delete(cache.value.keys().next().value!)
      }
    }
    for (let i = 0; i < Math.min(4, pending.length); i++) void worker()
  }
  const sourceOf = (row: OxiDNSQuery): Source => row.steps || !row.has_response || row.error
    ? { label: querySourceLabel(row) } : cache.value.get(keyOf(row)) || { label: '读取中…' }
  function retry(row: OxiDNSQuery) { cache.value.delete(keyOf(row)); load() }
  const onVisible = () => { if (document.hidden) controller?.abort(); else load() }
  watch(() => [input.agentId, input.recorder, input.instanceId, input.data], load, { immediate: true })
  onMounted(() => document.addEventListener('visibilitychange', onVisible))
  onUnmounted(() => { controller?.abort(); document.removeEventListener('visibilitychange', onVisible) })
  return { sourceOf, retry }
}
