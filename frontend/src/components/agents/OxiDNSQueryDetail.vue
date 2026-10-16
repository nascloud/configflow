<template>
  <Dialog :open="true" @update:open="open => !open && emit('close')">
    <DialogContent class="flex max-h-[calc(100dvh_-_2rem)] min-h-0 min-w-0 max-w-[calc(100vw_-_2rem)] flex-col overflow-hidden sm:max-w-[900px]">
      <div class="shrink-0 min-w-0 pr-6">
      <DialogHeader>
        <DialogTitle>DNS 查询详情</DialogTitle>
        <DialogDescription>查看这次查询的应答和核心实际记录的执行轨迹。</DialogDescription>
      </DialogHeader>
      </div>
      <div class="min-h-0 min-w-0 flex-1 space-y-5 overflow-y-auto overscroll-contain pr-1" tabindex="0" role="region" aria-label="查询详情内容">
      <LoadingRows v-if="loading && !record" :rows="4" />
      <div v-if="error" class="rounded-lg border border-warning-accent/30 bg-warning-soft px-3 py-2 text-sm text-warning-accent" role="alert">
        {{ error }}<Button variant="ghost" class="ml-2 min-h-11" :disabled="loading" @click="load">重试</Button>
      </div>
      <template v-if="record">
        <div class="min-w-0 rounded-lg border border-border bg-background/40 p-4">
          <p v-for="(question, index) in record.questions_json" :key="index" class="m-0 flex items-start gap-2">
            <span class="min-w-0 break-all font-mono text-sm">{{ question.name }}</span><span class="chip shrink-0 font-mono">{{ formatDNSType(question.qtype) }}</span>
          </p>
          <dl class="mb-0 mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-3">
            <div><dt class="text-muted-foreground">时间</dt><dd class="num m-0 mt-1">{{ formatQueryTime(record.created_at_ms) }}</dd></div>
            <div><dt class="text-muted-foreground">客户端</dt><dd class="m-0 mt-1 break-all font-mono">{{ record.client_ip || '—' }}</dd></div>
            <div><dt class="text-muted-foreground">耗时</dt><dd class="num m-0 mt-1">{{ formatMonitorNumber(record.elapsed_ms, 2) }}<span v-if="monitorNumber(record.elapsed_ms)"> ms</span></dd></div>
            <div><dt class="text-muted-foreground">结果</dt><dd class="m-0 mt-1">{{ record.error ? '处理错误' : record.has_response ? formatDNSCode(record.rcode) : '无应答' }}</dd></div>
            <div><dt class="text-muted-foreground">记录器</dt><dd class="m-0 mt-1 break-all font-mono">{{ recorder }}</dd></div>
            <div><dt class="text-muted-foreground">记录编号</dt><dd class="m-0 mt-1 break-all font-mono">{{ record.id }}</dd></div>
          </dl>
        </div>
        <p v-if="record.error" class="m-0 break-words rounded-lg bg-destructive-soft p-3 text-sm text-destructive-accent">{{ record.error }}</p>
        <SectionCard title="应答记录" :padded="false" class="min-w-0">
          <p v-if="!record.answers_json?.length" class="m-0 px-5 py-4 text-sm text-muted-foreground">这次查询没有应答记录。</p>
          <div v-else class="cf-table-wrap max-w-full" tabindex="0" role="region" aria-label="DNS 应答记录">
            <table class="cf-table w-full">
              <thead><tr><th class="text-left">名称</th><th class="text-left">类型</th><th class="text-right">TTL</th><th class="text-left">内容</th></tr></thead>
              <tbody><tr v-for="(answer, index) in record.answers_json" :key="index">
                <td class="max-w-[240px] break-all font-mono text-xs">{{ answer.name }}</td>
                <td class="font-mono text-xs">{{ formatDNSType(answer.rr_type) }}</td>
                <td class="num text-right">{{ answer.ttl }}</td>
                <td class="max-w-[360px] break-all whitespace-pre-wrap font-mono text-xs">{{ answer.payload_text }}</td>
              </tr></tbody>
            </table>
          </div>
        </SectionCard>
        <SectionCard title="执行轨迹" description="显示核心记录的实际执行步骤；没有轨迹时无法判断命中规则或缓存路径。" :padded="false" class="min-w-0">
          <p v-if="!record.steps?.length" class="m-0 px-5 py-4 text-sm text-muted-foreground">这条记录没有执行轨迹。</p>
          <div v-else class="cf-table-wrap max-w-full" tabindex="0" role="region" aria-label="查询执行轨迹">
            <table class="cf-table w-full">
              <thead><tr><th class="text-right">顺序</th><th class="text-left">序列</th><th class="text-right">节点</th><th class="text-left">类型 / 标签</th><th class="text-left">结果</th></tr></thead>
              <tbody><tr v-for="(step, index) in record.steps" :key="index">
                <td class="num text-right">{{ step.event_index }}</td>
                <td class="font-mono text-xs">{{ step.sequence_tag || '—' }}</td>
                <td class="num text-right">{{ step.node_index }}</td>
                <td class="font-mono text-xs">{{ step.kind }}<span v-if="step.tag"> · {{ step.tag }}</span></td>
                <td class="max-w-[260px] break-words text-xs">{{ step.outcome || '—' }}</td>
              </tr></tbody>
            </table>
          </div>
        </SectionCard>
      </template>
      </div>
      <DialogFooter class="shrink-0"><Button variant="outline" class="min-h-11" @click="emit('close')">关闭</Button></DialogFooter>
    </DialogContent>
  </Dialog>
</template>
<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { oxidnsMonitorApi } from '@/api'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import SectionCard from '@/components/common/SectionCard.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import { formatDNSCode, formatDNSType, formatMonitorNumber, formatQueryTime, monitorNumber, type OxiDNSQuery } from '@/types/oxidnsMonitor'
const props = defineProps<{ agentId: string; recordId: string | number; recorder: string }>()
const emit = defineEmits<{ close: [] }>()
const record = ref<OxiDNSQuery | null>(null)
const loading = ref(false)
const error = ref('')
let controller: AbortController | undefined
async function load() {
  controller?.abort()
  const request = controller = new AbortController()
  loading.value = true
  error.value = ''
  try {
    const { data } = await oxidnsMonitorApi.query(props.agentId, props.recordId, props.recorder, { signal: request.signal })
    if (request.signal.aborted) return
    if (!data.success) throw new Error(data.message || '查询详情读取失败。')
    record.value = data.record
  } catch (failure: any) {
    if (!request.signal.aborted) error.value = failure?.response?.data?.message || failure?.message || '查询详情读取失败。'
  } finally {
    if (!request.signal.aborted) loading.value = false
  }
}
watch(() => [props.agentId, props.recordId, props.recorder], () => { record.value = null; void load() }, { immediate: true })
onUnmounted(() => controller?.abort())
</script>
