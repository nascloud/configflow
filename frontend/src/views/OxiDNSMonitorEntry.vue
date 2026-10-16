<template>
  <div class="min-w-0">
    <PageHeader title="OxiDNS 监控">
      <template #actions>
        <Button variant="outline" class="min-h-11" :disabled="loading" @click="load">
          <RefreshCw class="size-4" :class="{ 'animate-spin': loading }" />刷新
        </Button>
      </template>
    </PageHeader>
    <p v-if="error && agents !== null" class="mb-4 rounded-lg border border-warning-accent/30 bg-warning-soft p-3 text-sm text-warning-accent" role="alert">
      {{ error }} 保留上次读取的设备列表。
    </p>
    <SectionCard v-if="loading && agents === null" :padded="false"><LoadingRows :rows="3" /></SectionCard>
    <SectionCard v-else-if="error && agents === null">
      <EmptyState :icon="TriangleAlert" title="设备列表读取失败" :description="error">
        <Button variant="outline" class="min-h-11" :disabled="loading" @click="load">重试</Button>
      </EmptyState>
    </SectionCard>
    <SectionCard v-else-if="agents && !agents.length">
      <EmptyState :icon="Server" title="暂无 OxiDNS Agent" description="接入 OxiDNS Agent 后，即可在这里查看查询记录、缓存命中率和运行统计。">
        <Button class="min-h-11" @click="router.push('/agents')">前往 Agent 接入</Button>
      </EmptyState>
    </SectionCard>
    <template v-else-if="agents">
      <p class="mb-4 mt-0 text-sm text-muted-foreground">选择要查看的 OxiDNS 设备。</p>
      <DataTableShell :footer="'共 ' + agents.length + ' 台 OxiDNS 设备'">
        <TableHeader><TableRow><TableHead>设备</TableHead><TableHead>地址</TableHead><TableHead>Agent 状态</TableHead><TableHead class="text-right">操作</TableHead></TableRow></TableHeader>
        <TableBody>
          <TableRow v-for="agent in agents" :key="agent.id">
            <TableCell class="max-w-[280px]">
              <p class="m-0 truncate font-medium" :title="agent.name || agent.id">{{ agent.name || agent.id }}</p>
            </TableCell>
            <TableCell class="font-mono text-xs">{{ agent.host || '—' }}</TableCell>
            <TableCell><StatusDot :tone="agent.status === 'online' ? 'success' : 'muted'" :label="agent.status === 'online' ? '在线' : '离线'" /></TableCell>
            <TableCell class="text-right"><Button variant="outline" class="min-h-11 whitespace-nowrap" :aria-label="'查看 ' + (agent.name || agent.id) + ' 的监控'" @click="router.push(monitorPath(agent.id))">查看监控<ArrowRight class="size-4" /></Button></TableCell>
          </TableRow>
        </TableBody>
      </DataTableShell>
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, RefreshCw, Server, TriangleAlert } from '@lucide/vue'
import { agentApi } from '@/api'
import type { Agent } from '@/types'
import PageHeader from '@/components/common/PageHeader.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import DataTableShell from '@/components/common/DataTableShell.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import StatusDot from '@/components/common/StatusDot.vue'
import { Button } from '@/components/ui/button'
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'

type MonitorAgent = Pick<Agent, 'id' | 'name' | 'host' | 'status'>
const router = useRouter()
const agents = ref<MonitorAgent[] | null>(null)
const loading = ref(false)
const error = ref('')
let epoch = 0
let disposed = false
const monitorPath = (id: string) => '/agents/' + encodeURIComponent(id) + '/oxidns'

async function load() {
  const requestEpoch = ++epoch
  loading.value = true
  error.value = ''
  try {
    const { data } = await agentApi.getAll()
    if (disposed || requestEpoch !== epoch) return
    if (!Array.isArray(data)) throw new Error('设备列表格式异常，请稍后重试。')
    agents.value = data.filter((agent: Agent) => agent && agent.service_type === 'oxidns')
    if (agents.value.length === 1) await router.replace(monitorPath(agents.value[0].id))
  } catch (failure: any) {
    if (!disposed && requestEpoch === epoch) error.value = failure?.response?.data?.message || failure?.message || '暂时无法读取设备列表。'
  } finally {
    if (!disposed && requestEpoch === epoch) loading.value = false
  }
}
onMounted(load)
onUnmounted(() => { disposed = true; epoch++ })
</script>
