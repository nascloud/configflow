<template>
  <div class="min-w-0">
    <PageHeader title="OxiDNS 监控">
      <template #actions>
        <label class="flex min-h-11 items-center gap-2 text-[13px] text-muted-foreground">
          <Switch v-model="autoRefresh" aria-label="每 10 秒自动刷新监控" />自动刷新 · 10 秒
        </label>
        <Button variant="outline" class="min-h-11" :disabled="overviewLoading || queriesLoading || devicesLoading" @click="refreshAll">
          <RefreshCw class="size-4" :class="{ 'animate-spin': overviewLoading || queriesLoading || devicesLoading }" />刷新
        </Button>
      </template>
    </PageHeader>
    <div class="mb-5 min-w-0 space-y-2">
      <label id="oxidns-device-label" class="block text-xs font-medium text-muted-foreground">监控设备</label>
      <Select :model-value="agentId" :disabled="!devices?.length || switchingDevice" @update:model-value="switchDevice(String($event))">
        <SelectTrigger class="h-auto min-h-14 w-full min-w-0 sm:max-w-md" aria-labelledby="oxidns-device-label">
          <SelectValue class="min-w-0 flex-1 text-left" :placeholder="devicesLoading ? '正在读取设备…' : '选择 OxiDNS 设备'">
            <span v-if="currentDevice" class="flex min-w-0 flex-1 items-center gap-3">
              <span class="min-w-0 flex-1">
                <span class="block truncate font-medium" :title="currentDevice.name || currentDevice.id">{{ currentDevice.name || currentDevice.id }}</span>
                <span class="block truncate font-mono text-xs text-muted-foreground" :title="currentDevice.host">{{ currentDevice.host || '—' }}</span>
              </span>
              <StatusDot class="shrink-0" :tone="currentDevice.status === 'online' ? 'success' : 'muted'" :label="currentDevice.status === 'online' ? '在线' : '离线'" />
            </span>
            <span v-else class="truncate text-muted-foreground">{{ devicesLoading ? '正在读取设备…' : '选择 OxiDNS 设备' }}</span>
          </SelectValue>
        </SelectTrigger>
        <SelectContent class="max-w-[calc(100vw-2rem)]">
          <SelectItem v-for="device in devices || []" :key="device.id" :value="device.id" :text-value="device.name || device.id" class="min-h-14 [&>span:last-child]:min-w-0 [&>span:last-child]:flex-1">
            <span class="min-w-0 flex-1">
              <span class="block truncate font-medium" :title="device.name || device.id">{{ device.name || device.id }}</span>
              <span class="block truncate font-mono text-xs text-muted-foreground">{{ device.host || '—' }}</span>
            </span>
            <StatusDot class="shrink-0" :tone="device.status === 'online' ? 'success' : 'muted'" :label="device.status === 'online' ? '在线' : '离线'" />
          </SelectItem>
        </SelectContent>
      </Select>
      <div v-if="devicesError || switchError" role="alert" class="flex flex-wrap items-center gap-x-3 text-sm text-warning-accent">
        <span>{{ devicesError || switchError }}</span>
        <Button v-if="devicesError" variant="ghost" class="min-h-11" :disabled="devicesLoading" @click="loadDevices">重试设备列表</Button>
      </div>
    </div>
    <SectionCard v-if="devices && !devices.length">
      <EmptyState :icon="Server" title="暂无 OxiDNS Agent" description="接入 OxiDNS Agent 后，即可在这里查看查询记录、缓存命中率和运行统计。">
        <Button class="min-h-11" @click="router.push('/agents')">前往 Agent 接入</Button>
      </EmptyState>
    </SectionCard>
    <template v-else>
      <div v-if="overview" class="mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-[12.5px] text-muted-foreground">
        <StatusDot :tone="coreRunning ? 'success' : 'warning'" :label="coreRunning ? '核心运行中' : '核心状态：' + overview.core.status" />
        <span v-if="overview.core.version" class="font-mono">OxiDNS {{ overview.core.version }}</span>
        <span>已运行 {{ uptime }}</span>
        <span class="ml-auto text-xs">采样 {{ sampleTime }}</span>
      </div>
      <p v-if="overviewError && overview" role="alert" class="mb-4 rounded-lg border border-warning-accent/30 bg-warning-soft p-3 text-sm text-warning-accent">
        {{ overviewError }} 保留上次成功读取的数据，采样时间见上方。
      </p>
      <LoadingRows v-if="overviewLoading && !overview" :rows="5" />
      <SectionCard v-else-if="!overview">
        <EmptyState :icon="TriangleAlert" title="暂时无法读取 OxiDNS 监控" :description="overviewError || '等待核心返回监控数据。'">
          <Button variant="outline" class="min-h-11" :disabled="overviewLoading" @click="refresh(true)">重试</Button>
        </EmptyState>
      </SectionCard>
      <Tabs v-else v-model="tab" class="min-w-0">
        <TabsList class="mb-4 min-h-11">
          <TabsTrigger value="overview" class="min-h-11 px-5">运行概览</TabsTrigger>
          <TabsTrigger value="queries" class="min-h-11 px-5">查询记录</TabsTrigger>
        </TabsList>
        <TabsContent value="overview"><OxiDNSOverviewPanel :overview="overview" :qps="qps" /></TabsContent>
        <TabsContent value="queries">
          <SectionCard v-if="!overview.recorders.length">
            <EmptyState :icon="Search" title="尚未启用查询记录" description="在此 Agent 绑定的配置空间中，进入「配置生成 → OxiDNS → 设置」启用查询记录，保存后推送到此 Agent。" />
          </SectionCard>
          <OxiDNSQueryTable v-else :agent-id="agentId" :recorders="overview.recorders" :recorder="recorder" :filters="filters" :data="queries"
            :loading="queriesLoading" :error="queriesError" :page="page" :has-next="hasNext"
            @filter="applyFilters" @recorder="selectRecorder" @next="nextPage" @previous="previousPage" @retry="loadQueries(true)" @detail="selectedRecord = $event" />
        </TabsContent>
      </Tabs>
    </template>
    <OxiDNSQueryDetail v-if="selectedRecord" :agent-id="agentId" :record-id="selectedRecord.id" :recorder="recorder" @close="selectedRecord = null" />
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { RefreshCw, Search, Server, TriangleAlert } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import PageHeader from '@/components/common/PageHeader.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import StatusDot from '@/components/common/StatusDot.vue'
import OxiDNSOverviewPanel from '@/components/agents/OxiDNSOverview.vue'
import OxiDNSQueryTable from '@/components/agents/OxiDNSQueryTable.vue'
import OxiDNSQueryDetail from '@/components/agents/OxiDNSQueryDetail.vue'
import { useOxiDNSMonitor } from '@/composables/useOxiDNSMonitor'
import { oxidnsMonitorPath, rememberOxiDNSDevice, useOxiDNSDevices } from '@/composables/useOxiDNSDevices'
import { monitorNumber, type OxiDNSQuery } from '@/types/oxidnsMonitor'
const route = useRoute()
const router = useRouter()
const agentId = computed(() => String(route.params.agentId || ''))
const tab = ref(route.query.tab === 'queries' ? 'queries' : 'overview')
const selectedRecord = ref<OxiDNSQuery | null>(null)
const { overview, overviewLoading, overviewError, qps, queries, queriesLoading, queriesError, recorder, filters,
  page, hasNext, autoRefresh, refresh, loadQueries, selectRecorder, applyFilters, nextPage, previousPage } = useOxiDNSMonitor(agentId, tab)
const { devices, loading: devicesLoading, error: devicesError, load: loadDevices } = useOxiDNSDevices()
const currentDevice = computed(() => devices.value?.find(device => device.id === agentId.value) || overview.value?.agent)
const switchingDevice = ref(false)
const switchError = ref('')
watch([agentId, recorder], () => { selectedRecord.value = null })
watch([agentId, devices], () => {
  if (devices.value?.some(device => device.id === agentId.value)) rememberOxiDNSDevice(agentId.value)
})
onMounted(loadDevices)
async function switchDevice(id: string) {
  if (id === agentId.value || switchingDevice.value || !devices.value?.some(device => device.id === id)) return
  selectedRecord.value = null
  switchingDevice.value = true
  switchError.value = ''
  try {
    await router.replace({ path: oxidnsMonitorPath(id), query: { ...route.query, tab: tab.value } })
    rememberOxiDNSDevice(id)
  } catch (failure: any) {
    switchError.value = failure?.message || '切换设备失败，请重试。'
  } finally {
    switchingDevice.value = false
  }
}
async function refreshAll() {
  await Promise.allSettled([loadDevices(), refresh(true)])
}
const coreRunning = computed(() => ['running', 'online', 'healthy', 'ready', 'ok'].includes(overview.value?.core.status || ''))
const sampleTime = computed(() => overview.value ? new Date(overview.value.collected_at).toLocaleTimeString() : '—')
const uptime = computed(() => {
  const value = overview.value?.core.uptime_ms
  if (!monitorNumber(value)) return '—'
  const seconds = Math.max(0, Math.floor(value / 1000))
  if (seconds < 60) return seconds + ' 秒'
  if (seconds < 3600) return Math.floor(seconds / 60) + ' 分钟'
  if (seconds < 86400) return Math.floor(seconds / 3600) + ' 小时 ' + Math.floor(seconds % 3600 / 60) + ' 分钟'
  return Math.floor(seconds / 86400) + ' 天 ' + Math.floor(seconds % 86400 / 3600) + ' 小时'
})
</script>
