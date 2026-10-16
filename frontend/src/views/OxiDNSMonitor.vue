<template>
  <div class="min-w-0">
    <Button variant="ghost" class="mb-3 min-h-11 -ml-3 text-muted-foreground" @click="router.push('/agents')"><ArrowLeft class="size-4" />返回 Agent</Button>
    <PageHeader title="OxiDNS 监控">
      <template #actions>
        <label class="flex min-h-11 items-center gap-2 text-[13px] text-muted-foreground">
          <Switch v-model="autoRefresh" aria-label="每 10 秒自动刷新监控" />自动刷新 · 10 秒
        </label>
        <Button variant="outline" class="min-h-11" :disabled="overviewLoading || queriesLoading" @click="refresh(true)">
          <RefreshCw class="size-4" :class="{ 'animate-spin': overviewLoading || queriesLoading }" />刷新
        </Button>
      </template>
    </PageHeader>
    <div v-if="overview" class="mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-[12.5px] text-muted-foreground">
      <span class="min-w-0 max-w-full truncate font-medium text-foreground" :title="overview.agent.name">{{ overview.agent.name }}</span>
      <span class="font-mono">{{ overview.agent.host }}</span>
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
        <OxiDNSQueryTable v-else :recorders="overview.recorders" :recorder="recorder" :filters="filters" :data="queries"
          :loading="queriesLoading" :error="queriesError" :page="page" :has-next="hasNext"
          @filter="applyFilters" @recorder="selectRecorder" @next="nextPage" @previous="previousPage" @retry="loadQueries(true)" @detail="selectedRecord = $event" />
      </TabsContent>
    </Tabs>
    <OxiDNSQueryDetail v-if="selectedRecord" :agent-id="agentId" :record-id="selectedRecord.id" :recorder="recorder" @close="selectedRecord = null" />
  </div>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, RefreshCw, Search, TriangleAlert } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
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
import { monitorNumber, type OxiDNSQuery } from '@/types/oxidnsMonitor'
const route = useRoute()
const router = useRouter()
const agentId = computed(() => String(route.params.agentId || ''))
const tab = ref('overview')
const selectedRecord = ref<OxiDNSQuery | null>(null)
const { overview, overviewLoading, overviewError, qps, queries, queriesLoading, queriesError, recorder, filters,
  page, hasNext, autoRefresh, refresh, loadQueries, selectRecorder, applyFilters, nextPage, previousPage } = useOxiDNSMonitor(agentId, tab)
watch([agentId, recorder], () => { selectedRecord.value = null })
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
