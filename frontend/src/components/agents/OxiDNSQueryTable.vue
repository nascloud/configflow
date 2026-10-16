<template>
  <div class="space-y-4">
    <form @submit.prevent="emit('filter', { ...draft })">
      <Toolbar v-model:search="draft.qname" placeholder="查询域名" class="[&_input]:min-h-11">
        <template #filters>
          <Select :model-value="recorder" @update:model-value="value => emit('recorder', String(value))">
            <SelectTrigger class="min-h-11 w-full sm:w-[190px]" aria-label="查询记录器"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem v-for="tag in recorders" :key="tag" :value="tag">{{ tag }}</SelectItem></SelectContent>
          </Select>
          <Select v-model="draft.range">
            <SelectTrigger class="min-h-11 w-[140px]" aria-label="查询时间范围"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="1h">最近 1 小时</SelectItem><SelectItem value="24h">最近 24 小时</SelectItem><SelectItem value="all">全部时间</SelectItem></SelectContent>
          </Select>
          <Select v-model="draft.status">
            <SelectTrigger class="min-h-11 w-[140px]" aria-label="查询结果筛选"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem value="all">全部结果</SelectItem><SelectItem value="error">处理错误</SelectItem><SelectItem value="has_response">有应答</SelectItem><SelectItem value="no_response">无应答</SelectItem></SelectContent>
          </Select>
        </template>
        <template #actions>
          <Button type="submit" class="min-h-11" :disabled="loading"><Search class="size-4" />查询</Button>
        </template>
      </Toolbar>
      <div class="grid grid-cols-2 gap-3 md:grid-cols-4">
        <label class="text-xs text-muted-foreground">客户端 IP<Input v-model="draft.client_ip" class="mt-1.5 min-h-11 bg-card font-mono" placeholder="例如 192.0.2.10" /></label>
        <label class="text-xs text-muted-foreground">查询类型<Input v-model="draft.qtype" class="mt-1.5 min-h-11 bg-card font-mono" placeholder="例如 A、AAAA" /></label>
        <label class="text-xs text-muted-foreground">响应码<Input v-model="draft.rcode" class="mt-1.5 min-h-11 bg-card font-mono" placeholder="例如 NOERROR" /></label>
        <label class="text-xs text-muted-foreground">匹配器标签<Input v-model="draft.matcher_tag" class="mt-1.5 min-h-11 bg-card font-mono" placeholder="按实际执行标签查询" /></label>
      </div>
    </form>
    <p v-if="error" class="m-0 rounded-lg border border-warning-accent/30 bg-warning-soft px-3 py-2.5 text-sm text-warning-accent" role="alert">
      {{ error }}<span v-if="data"> 保留上次成功读取的结果。</span>
      <Button variant="ghost" class="ml-2 min-h-11" :disabled="loading" @click="emit('retry')">重试</Button>
    </p>
    <LoadingRows v-if="loading && !data" :rows="5" />
    <EmptyState v-else-if="!data && error" title="查询记录读取失败" description="请检查核心连接后重试。" />
    <EmptyState v-else-if="data && !data.records.length" :icon="Search" title="没有匹配的查询记录" description="可以调整时间范围或筛选条件。查询记录由核心记录器提供。" />
    <DataTableShell v-else-if="data" :footer="'本页 ' + data.records.length + ' 条 · 第 ' + page + ' 页'">
      <TableHeader><TableRow><TableHead>时间</TableHead><TableHead>查询</TableHead><TableHead>客户端</TableHead><TableHead>结果</TableHead><TableHead class="text-right">耗时</TableHead><TableHead class="text-right">详情</TableHead></TableRow></TableHeader>
      <TableBody>
        <TableRow v-for="record in data.records" :key="record.id">
          <TableCell class="num text-xs">{{ formatQueryTime(record.created_at_ms) }}</TableCell>
          <TableCell class="max-w-[320px]">
            <div v-for="(question, index) in record.questions_json" :key="index" class="flex items-start gap-2">
              <span class="min-w-0 truncate font-mono text-xs" :title="question.name">{{ question.name }}</span><span class="chip shrink-0 font-mono">{{ formatDNSType(question.qtype) }}</span>
            </div>
            <span v-if="!record.questions_json?.length" class="text-muted-foreground">—</span>
          </TableCell>
          <TableCell class="font-mono text-xs">{{ record.client_ip || '—' }}</TableCell>
          <TableCell>
            <span :class="['chip', record.error ? 'chip-bad' : record.has_response ? 'chip-ok' : 'chip-warn']">
              {{ record.error ? '处理错误' : record.has_response ? formatDNSCode(record.rcode) : '无应答' }}
            </span>
          </TableCell>
          <TableCell class="num text-right">{{ formatMonitorNumber(record.elapsed_ms, 2) }}<span v-if="monitorNumber(record.elapsed_ms)"> ms</span></TableCell>
          <TableCell class="text-right"><Button variant="ghost" class="min-h-11" :aria-label="'查看查询 ' + record.id + ' 详情'" @click="emit('detail', record)">查看</Button></TableCell>
        </TableRow>
      </TableBody>
    </DataTableShell>
    <div v-if="data || page > 1" class="flex flex-wrap items-center justify-between gap-3">
      <p class="m-0 text-xs text-muted-foreground">{{ page > 1 ? '浏览历史页时暂停记录自动刷新，保留当前页。' : '记录按时间倒序排列，每页最多 50 条。' }}</p>
      <div class="flex items-center gap-2">
        <Button variant="outline" class="min-h-11" :disabled="loading || page <= 1" @click="emit('previous')">上一页</Button>
        <Button variant="outline" class="min-h-11" :disabled="loading || !hasNext" @click="emit('next')">下一页</Button>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
import { reactive, watch } from 'vue'
import { Search } from '@lucide/vue'
import Toolbar from '@/components/common/Toolbar.vue'
import DataTableShell from '@/components/common/DataTableShell.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { formatDNSType, formatDNSCode, formatMonitorNumber, formatQueryTime, monitorNumber, type OxiDNSQueries, type OxiDNSQuery, type OxiDNSQueryFilters } from '@/types/oxidnsMonitor'
const props = defineProps<{
  recorders: string[]; recorder: string; filters: OxiDNSQueryFilters; data: OxiDNSQueries | null
  loading: boolean; error: string; page: number; hasNext: boolean
}>()
const emit = defineEmits<{
  filter: [value: OxiDNSQueryFilters]; recorder: [value: string]; detail: [value: OxiDNSQuery]
  next: []; previous: []; retry: []
}>()
const draft = reactive({ ...props.filters })
watch(() => props.filters, value => Object.assign(draft, value))
</script>
