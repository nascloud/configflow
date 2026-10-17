<template>
  <div class="min-w-0">
    <div class="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
      <div v-for="item in summary" :key="item.label" class="rounded-xl border border-border bg-card px-4 py-3">
        <div class="text-[12px] text-muted-foreground">{{ item.label }}</div>
        <div class="num mt-1 text-[18px] text-foreground">{{ item.value }}</div>
      </div>
    </div>

    <Toolbar v-model:search="search" placeholder="搜索主机、IP、规则或策略">
      <template #filters>
        <Segmented v-model="sortBy" :options="SORT_OPTIONS" label="排序" />
      </template>
      <template #actions>
        <label class="flex min-h-11 items-center gap-2 text-[13px] text-muted-foreground">
          <Switch v-model="live" aria-label="每 2 秒刷新连接" />实时 · 2 秒
        </label>
        <Button variant="outline" class="min-h-11" :disabled="!rows.length || closingAll" @click="closeAll">
          <Loader2 v-if="closingAll" class="size-3.5 animate-spin" /><X v-else class="size-3.5" />{{ search ? '断开筛选结果' : '全部断开' }}
        </Button>
      </template>
    </Toolbar>

    <p v-if="error && snapshot" role="alert" class="mb-4 rounded-lg border border-warning-accent/30 bg-warning-soft p-3 text-sm text-warning-accent">
      {{ error }} 以下为上次成功读取的连接。
    </p>
    <LoadingRows v-if="!snapshot && !error" :rows="6" />
    <SectionCard v-else-if="!snapshot">
      <EmptyState :icon="TriangleAlert" title="读取连接失败" :description="error">
        <Button variant="outline" class="min-h-11" @click="load">重试</Button>
      </EmptyState>
    </SectionCard>
    <SectionCard v-else-if="!rows.length">
      <EmptyState :icon="Cable" :title="search ? '没有匹配的连接' : '当前没有活动连接'" />
    </SectionCard>
    <div v-else class="cf-table-wrap">
      <table class="cf-table min-w-[1080px] table-fixed">
        <thead>
          <tr>
            <th class="w-[24%]">主机</th>
            <th class="w-[11%]">来源</th>
            <th class="w-[17%]">规则</th>
            <th class="w-[20%]">链路</th>
            <th class="w-[10%] cf-table__right">速率 ↓ / ↑</th>
            <th class="w-[10%] cf-table__right">流量 ↓ / ↑</th>
            <th class="w-[6%] cf-table__right">时长</th>
            <th class="w-14"><span class="cf-sr">操作</span></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in visibleRows" :key="row.id">
            <td>
              <div class="truncate font-mono text-xs text-foreground" :title="row.target">{{ row.target }}</div>
              <div class="mt-0.5 text-[11.5px] text-muted-foreground">{{ row.metadata.network.toUpperCase() }} · {{ row.metadata.type }}<template v-if="row.metadata.process"> · {{ row.metadata.process }}</template></div>
            </td>
            <td class="truncate font-mono text-xs text-muted-foreground">{{ row.metadata.sourceIP }}</td>
            <td>
              <div class="truncate text-xs">{{ row.rule }}</div>
              <div v-if="row.rulePayload" class="truncate font-mono text-[11.5px] text-muted-foreground" :title="row.rulePayload">{{ row.rulePayload }}</div>
            </td>
            <td class="truncate text-xs" :title="row.chain">{{ row.chain }}</td>
            <td class="cf-table__right num text-xs">
              <div>{{ speedText(row.downSpeed) }}</div><div class="text-muted-foreground">{{ speedText(row.upSpeed) }}</div>
            </td>
            <td class="cf-table__right num text-xs">
              <div>{{ formatBytes(row.download) }}</div><div class="text-muted-foreground">{{ formatBytes(row.upload) }}</div>
            </td>
            <td class="cf-table__right num text-xs text-muted-foreground">{{ durationText(row.start) }}</td>
            <td class="cf-table__right">
              <Button variant="ghost" size="icon" class="size-11" :aria-label="`断开 ${row.target}`" title="断开" @click="closeOne(row.id)"><X class="size-4" /></Button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="rows.length > visibleRows.length" class="cf-table__foot m-0 text-muted-foreground">
        共 {{ rows.length }} 条，仅显示前 {{ visibleRows.length }} 条，可用搜索缩小范围。
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Cable, Loader2, TriangleAlert, X } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import Toolbar from '@/components/common/Toolbar.vue'
import Segmented from '@/components/common/Segmented.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import { mihomoPanelApi } from '@/api'
import { confirmDanger, notify } from '@/lib/feedback'
import { formatBytes } from '@/lib/format'
import { panelError, type MihomoConnection, type MihomoConnectionsSnapshot } from '@/types/mihomo'

type SortKey = 'start' | 'speed' | 'total'
const SORT_OPTIONS: Array<{ value: SortKey; label: string }> = [
  { value: 'start', label: '最新' },
  { value: 'speed', label: '速率' },
  { value: 'total', label: '流量' }
]
const POLL_MS = 2000
const ROW_LIMIT = 300

const props = defineProps<{ agentId: string; active: boolean }>()

const snapshot = ref<MihomoConnectionsSnapshot | null>(null)
const error = ref('')
const search = ref('')
const sortBy = ref<SortKey>('start')
const live = ref(true)
const closingAll = ref(false)
const now = ref(Date.now())
const speeds = ref(new Map<string, { up: number; down: number }>())
const totalSpeed = ref({ up: 0, down: 0 })
let previous: { at: number; totals: { up: number; down: number }; byId: Map<string, { up: number; down: number }> } | null = null

const rows = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  const list = (snapshot.value?.connections || []).map(connection => {
    const meta = connection.metadata
    const host = meta.host || meta.sniffHost || meta.destinationIP
    const speed = speeds.value.get(connection.id)
    return {
      ...connection,
      target: `${host}:${meta.destinationPort}`,
      chain: [...connection.chains].reverse().join(' → '),
      upSpeed: speed?.up || 0,
      downSpeed: speed?.down || 0
    }
  })
  const filtered = keyword ? list.filter(row => [row.target, row.metadata.destinationIP, row.metadata.sourceIP, row.metadata.process,
    row.rule, row.rulePayload, row.chain].some(value => value?.toLowerCase().includes(keyword))) : list
  const key = {
    start: (row: typeof list[number]) => Date.parse(row.start) || 0,
    speed: (row: typeof list[number]) => row.downSpeed + row.upSpeed,
    total: (row: typeof list[number]) => row.download + row.upload
  }[sortBy.value]
  return filtered.sort((a, b) => key(b) - key(a))
})
const visibleRows = computed(() => rows.value.slice(0, ROW_LIMIT))

const summary = computed(() => [
  { label: '活动连接', value: String(snapshot.value?.connections?.length ?? '—') },
  { label: '实时速率 ↓ / ↑', value: `${speedText(totalSpeed.value.down)} / ${speedText(totalSpeed.value.up)}` },
  { label: '累计下载', value: formatBytes(snapshot.value?.downloadTotal) },
  { label: '累计上传', value: formatBytes(snapshot.value?.uploadTotal) }
])

function speedText(bytesPerSecond: number) {
  return bytesPerSecond > 0 ? formatBytes(bytesPerSecond) + '/s' : '—'
}

function durationText(start: string) {
  const seconds = Math.max(0, Math.floor((now.value - Date.parse(start)) / 1000))
  if (!Number.isFinite(seconds)) return '—'
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`
  return `${Math.floor(seconds / 86400)}d`
}

function updateSpeeds(data: MihomoConnectionsSnapshot) {
  const at = Date.now()
  const byId = new Map((data.connections || []).map(c => [c.id, { up: c.upload, down: c.download }]))
  const totals = { up: data.uploadTotal, down: data.downloadTotal }
  const next = new Map<string, { up: number; down: number }>()
  if (previous) {
    const seconds = Math.max((at - previous.at) / 1000, 0.5)
    for (const [id, current] of byId) {
      const before = previous.byId.get(id)
      if (before) next.set(id, { up: Math.max(0, (current.up - before.up) / seconds), down: Math.max(0, (current.down - before.down) / seconds) })
    }
    totalSpeed.value = { up: Math.max(0, (totals.up - previous.totals.up) / seconds), down: Math.max(0, (totals.down - previous.totals.down) / seconds) }
  }
  speeds.value = next
  previous = { at, totals, byId }
  now.value = at
}

let timer: ReturnType<typeof setTimeout> | null = null
let controller: AbortController | null = null
let disposed = false

async function load() {
  controller?.abort()
  controller = new AbortController()
  try {
    const { data } = await mihomoPanelApi.connections(props.agentId, { signal: controller.signal })
    const result: MihomoConnectionsSnapshot = data.data || { connections: [], uploadTotal: 0, downloadTotal: 0 }
    updateSpeeds(result)
    snapshot.value = result
    error.value = ''
  } catch (failure: any) {
    if (failure?.code === 'ERR_CANCELED') return
    error.value = panelError(failure, '暂时无法读取连接。')
  }
}

function schedule() {
  if (timer) clearTimeout(timer)
  timer = null
  if (disposed || !props.active || !live.value) return
  timer = setTimeout(async () => {
    if (document.visibilityState === 'visible') await load()
    schedule()
  }, POLL_MS)
}

async function closeOne(id: string) {
  try {
    await mihomoPanelApi.closeConnection(props.agentId, id)
    if (snapshot.value?.connections) snapshot.value.connections = snapshot.value.connections.filter(c => c.id !== id)
  } catch (failure) {
    notify.error('断开连接失败', panelError(failure))
  }
}

async function closeAll() {
  const targets: MihomoConnection[] = search.value.trim() ? rows.value : []
  const description = targets.length ? `将断开当前筛选出的 ${targets.length} 条连接。` : '将断开 Mihomo 上的全部活动连接，正在进行的下载和会话会中断。'
  if (!await confirmDanger(description, { title: '断开连接', confirmText: '断开' })) return
  closingAll.value = true
  try {
    if (targets.length) {
      const results = await Promise.allSettled(targets.map(c => mihomoPanelApi.closeConnection(props.agentId, c.id)))
      const failed = results.filter(result => result.status === 'rejected').length
      if (failed) notify.warning(`有 ${failed} 条连接断开失败`)
      else notify.success(`已断开 ${targets.length} 条连接`)
    } else {
      await mihomoPanelApi.closeAllConnections(props.agentId)
      notify.success('已断开全部连接')
    }
    await load()
  } catch (failure) {
    notify.error('断开连接失败', panelError(failure))
  } finally {
    closingAll.value = false
  }
}

watch(() => [props.active, live.value], ([active, polling]) => {
  // 暂停后再恢复时旧样本已过时，不能拿来算速率
  if (!active || !polling) previous = null
  else load()
  schedule()
})
onMounted(async () => {
  if (props.active) await load()
  schedule()
})
onUnmounted(() => {
  disposed = true
  if (timer) clearTimeout(timer)
  controller?.abort()
})
</script>
