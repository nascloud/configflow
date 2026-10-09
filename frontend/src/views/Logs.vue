<template>
  <div class="logs-page">
    <PageHeader
      title="日志"
      description="查看运行记录、查找错误。可按关键词和日志级别筛选，开启自动刷新后每 5 秒更新一次。"
    >
      <template #actions>
        <Button variant="outline" class="logs-control border-border/60 bg-background/40" :disabled="loading" @click="loadLogs()">
          <RefreshCw class="size-4" :class="loading && 'animate-spin'" />
          刷新
        </Button>
        <Button
          variant="outline"
          class="logs-control border-destructive-accent/30 bg-destructive-soft/40 text-destructive-accent"
          @click="clearLogs"
        >
          <Trash2 class="size-4" />
          清空
        </Button>
      </template>
    </PageHeader>

    <!-- 只在日志页按可用宽度分组，避免公共工具栏换行后仍将操作推到右侧。 -->
    <div class="logs-toolbar mb-4 rounded-xl border border-border bg-card p-3" role="group" aria-label="日志筛选与操作">
      <div class="logs-search relative min-w-0">
        <Search class="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <Input v-model="searchKeyword" class="logs-control bg-background pl-9 text-[13px]" placeholder="搜索关键词…" aria-label="搜索日志关键词" />
      </div>

      <div class="logs-filters">
        <Select v-model="logLevel" @update:model-value="loadLogs()">
          <SelectTrigger class="logs-control min-w-0 w-full border-input bg-card text-[13px] dark:border-transparent" aria-label="日志级别">
            <SelectValue placeholder="日志级别" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem v-for="level in LEVELS" :key="level.value" :value="level.value">
              {{ level.label }}
            </SelectItem>
          </SelectContent>
        </Select>

        <Select v-model="logLines" @update:model-value="loadLogs()">
          <SelectTrigger class="logs-control min-w-0 w-full border-input bg-card text-[13px] dark:border-transparent" aria-label="显示行数">
            <SelectValue placeholder="显示行数" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem v-for="option in LINE_OPTIONS" :key="option.value" :value="option.value">
              {{ option.label }}
            </SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div class="logs-actions">
        <label
          class="logs-control flex min-w-0 cursor-pointer items-center justify-center gap-1.5 rounded-lg border border-input bg-background px-2 text-[12.5px] whitespace-nowrap text-foreground dark:border-border/50 dark:bg-background/40 dark:text-muted-foreground"
          title="每 5 秒自动刷新日志"
        >
          <Switch :model-value="autoRefresh" aria-label="自动刷新" @update:model-value="toggleAutoRefresh" />
          <span>自动刷新</span>
          <StatusDot tone="success" :pulse="autoRefresh" :class="!autoRefresh && 'invisible'" aria-hidden="true" />
        </label>
        <Button variant="ghost" size="sm" class="logs-control min-w-0 w-full" title="滚动到底部" @click="scrollToBottom">
          <ArrowDownToLine class="size-4" />
          到底部
        </Button>
      </div>
    </div>

    <div v-if="logInfo || totalLines" class="mb-3 flex flex-wrap items-center gap-2">
      <Badge v-if="logInfo" variant="outline" class="min-w-0 max-w-full gap-1.5 font-mono text-[11px]">
        <FileText class="size-3" aria-hidden="true" />
        <span class="min-w-0 whitespace-normal [overflow-wrap:anywhere]">{{ logInfo.path }}</span>
      </Badge>
      <Badge v-if="logInfo" variant="outline" class="num font-mono text-[11px]">
        {{ logInfo.size_mb }} MB
      </Badge>
      <Badge variant="outline" class="num font-mono text-[11px]">总行数 {{ totalLines }}</Badge>
      <Badge v-if="filteredLines !== totalLines" variant="brand" class="num font-mono text-[11px]">
        筛选后 {{ filteredLines }}
      </Badge>
    </div>

    <SectionCard :padded="false" :class="loading && 'scanline'">
      <!-- 窄屏完整换行并随页面滚动；桌面保留独立滚动的终端视图。 -->
      <div
        ref="logContainer"
        class="logs-container rounded-xl bg-background/45 font-mono text-[12px] leading-[1.7]"
      >
        <EmptyState
          v-if="!logs.length"
          :icon="ScrollText"
          title="暂无日志"
          description="当前没有可显示的记录。可清除关键词、选择全部级别，或稍后刷新。"
        />

        <div v-else class="logs-list py-2">
          <div
            v-for="(line, index) in parsedLogs"
            :key="index"
            class="log-row transition-colors hover:bg-accent/40"
          >
            <span class="num shrink-0 py-0.5 text-right text-muted-foreground select-none dark:text-muted-foreground/50">
              {{ index + 1 }}
            </span>
            <!-- Vue 模板会压缩标签间的空白，各段之间用 gap 而不是空格分隔 -->
            <span
              v-if="line.level"
              class="log-body min-w-0 py-0.5"
            >
              <span class="log-time text-muted-foreground dark:text-muted-foreground/80">{{ line.time }}</span>
              <span class="log-logger text-info-accent">{{ line.logger }}</span>
              <span class="log-level" :class="levelClass(line.level)">{{ line.level }}</span>
              <span class="log-message min-w-0 whitespace-pre-wrap text-foreground/90">
                {{ line.message }}
              </span>
            </span>
            <span v-else class="log-message min-w-0 py-0.5 whitespace-pre-wrap text-foreground/75">
              {{ line.raw }}
            </span>
          </div>
        </div>
        <div ref="logEnd" data-testid="logs-end" class="logs-end" aria-hidden="true" />
      </div>
    </SectionCard>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ArrowDownToLine, FileText, RefreshCw, ScrollText, Search, Trash2 } from '@lucide/vue'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import EmptyState from '@/components/common/EmptyState.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusDot from '@/components/common/StatusDot.vue'
import api from '@/api'
import { confirmDanger, notify } from '@/lib/feedback'

const LEVELS = [
  { label: '全部级别', value: 'all' },
  { label: 'DEBUG', value: 'DEBUG' },
  { label: 'INFO', value: 'INFO' },
  { label: 'WARNING', value: 'WARNING' },
  { label: 'ERROR', value: 'ERROR' },
  { label: 'CRITICAL', value: 'CRITICAL' }
]

const LINE_OPTIONS = [
  { label: '100 行', value: '100' },
  { label: '200 行', value: '200' },
  { label: '500 行', value: '500' },
  { label: '1000 行', value: '1000' },
  { label: '全部', value: '10000' }
]

const logs = ref<string[]>([])
const logInfo = ref<any>(null)
const totalLines = ref(0)
const filteredLines = ref(0)

const searchKeyword = ref('')
/* Select 的值必须是非空字符串，用 'all' 表示不过滤，请求时再转空 */
const logLevel = ref('all')
const logLines = ref('100')

const autoRefresh = ref(false)
const refreshTimer = ref<number | null>(null)
const REFRESH_INTERVAL = 5000

const loading = ref(false)
const logContainer = ref<HTMLElement>()
const logEnd = ref<HTMLElement>()

/* 日志行解析：拆成时间 / 来源 / 级别 / 正文四段分别着色，
 * 取代原先的 v-html 拼接（scoped 样式对 v-html 内容不生效，颜色实际从未出现）。 */
const LOG_LINE = /^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[,.]\d+)\s+-\s+([\w.]+)\s+-\s+(\w+)\s+-\s+([\s\S]*)$/

const parsedLogs = computed(() =>
  logs.value.map(raw => {
    const m = LOG_LINE.exec(raw)
    if (!m) return { raw, time: '', logger: '', level: '', message: '' }
    return { raw, time: m[1], logger: m[2], level: m[3], message: m[4] }
  })
)

const levelClass = (level: string): string =>
  ({
    ERROR: 'text-destructive-accent font-semibold',
    CRITICAL: 'text-destructive-accent font-semibold',
    WARNING: 'text-warning-accent font-semibold',
    INFO: 'text-success-accent',
    DEBUG: 'text-muted-foreground'
  })[level] ?? 'text-muted-foreground'

const loadLogs = async (scrollToEnd = false) => {
  loading.value = true
  try {
    const params: Record<string, string | number> = { lines: Number(logLines.value) }
    if (searchKeyword.value) params.search = searchKeyword.value
    if (logLevel.value !== 'all') params.level = logLevel.value

    const response = await api.get('/logs/tail', { params })

    if (response.data.success) {
      logs.value = response.data.logs
      totalLines.value = response.data.total_lines
      filteredLines.value = response.data.filtered_lines

      if (scrollToEnd) {
        await nextTick()
        scrollToBottom()
      }
    } else {
      notify.error(response.data.message || '加载日志失败')
    }
  } catch (error: any) {
    console.error('Failed to load logs:', error)
    notify.error(error.response?.data?.error || '加载日志失败')
  } finally {
    loading.value = false
  }
}

const loadLogInfo = async () => {
  try {
    const response = await api.get('/logs/info')
    if (response.data.success && response.data.exists) {
      logInfo.value = response.data
    }
  } catch (error) {
    console.error('Failed to load log info:', error)
  }
}

const scrollToBottom = () => {
  const container = logContainer.value
  if (!container) return

  // 使用实际滚动模式，旋转屏幕或调整窗口后无需同步 JS 断点。
  if (window.getComputedStyle(container).overflowY === 'visible') {
    logEnd.value?.scrollIntoView({ block: 'end' })
  } else {
    container.scrollTop = container.scrollHeight
  }
}

const toggleAutoRefresh = (enabled: boolean) => {
  autoRefresh.value = enabled
  if (enabled) {
    refreshTimer.value = window.setInterval(() => loadLogs(true), REFRESH_INTERVAL)
    notify.success('已启用自动刷新', '每 5 秒显示最新日志')
  } else {
    if (refreshTimer.value) {
      clearInterval(refreshTimer.value)
      refreshTimer.value = null
    }
    notify.info('已停止自动刷新')
  }
}

const clearLogs = async () => {
  const ok = await confirmDanger('确定清空整个日志文件吗？这会删除全部日志，不仅是当前筛选结果，且无法恢复。', {
    title: '清空日志',
    confirmText: '清空'
  })
  if (!ok) return

  try {
    const response = await api.post('/logs/clear')
    if (response.data.success) {
      notify.success('日志已清空')
      logs.value = []
      totalLines.value = 0
      filteredLines.value = 0
      loadLogInfo()
    }
  } catch (error: any) {
    console.error('Failed to clear logs:', error)
    notify.error('清空日志失败')
  }
}

/* 关键词改动做防抖，避免每敲一个字符就打一次接口 */
let searchTimer: number
watch(searchKeyword, () => {
  clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => loadLogs(), 300)
})

onMounted(async () => {
  // 手机首次进入保留筛选区可见，桌面仍在独立日志窗口中显示最新记录。
  await loadLogs()
  await nextTick()
  if (logContainer.value && window.getComputedStyle(logContainer.value).overflowY !== 'visible') {
    scrollToBottom()
  }
  await loadLogInfo()
})

onUnmounted(() => {
  clearTimeout(searchTimer)
  if (refreshTimer.value) clearInterval(refreshTimer.value)
})
</script>

<style scoped>
.logs-page {
  container-type: inline-size;
}

.logs-toolbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 0.75rem;
}

.logs-filters,
.logs-actions {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.75rem;
  min-width: 0;
}

.logs-control {
  height: 2.75rem;
}

.logs-container {
  min-width: 0;
  overflow: visible;
}

.log-row {
  display: grid;
  grid-template-columns: 4ch minmax(0, 1fr);
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
}

.log-body {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  grid-template-areas: 'time level' 'logger logger' 'message message';
  gap: 0.125rem 0.5rem;
}

.log-time { grid-area: time; }
.log-logger { grid-area: logger; }
.log-level { grid-area: level; }
.log-body > .log-message { grid-area: message; }

.log-time,
.log-logger,
.log-message {
  overflow-wrap: anywhere;
}

/* 跳至最后一条时，让正文停在移动导航栏及安全区上方。 */
.logs-end {
  scroll-margin-bottom: calc(var(--cf-tabbar-h) + env(safe-area-inset-bottom) + var(--cf-sp-5));
}

@container (min-width: 40rem) {
  .logs-toolbar {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .logs-search {
    grid-column: 1 / -1;
  }
}

@container (min-width: 56rem) {
  .logs-toolbar {
    grid-template-columns: minmax(12rem, 1fr) auto auto;
  }

  .logs-search {
    grid-column: auto;
  }

  .logs-filters {
    grid-template-columns: 8.25rem 7.5rem;
  }

  .logs-actions {
    grid-template-columns: 8.5rem 6rem;
  }

  .logs-control {
    height: 2.25rem;
  }

  .logs-list {
    min-width: max-content;
  }

  .log-row {
    grid-template-columns: 52px minmax(0, 1fr);
    gap: 0.75rem;
    padding-block: 0;
  }

  .log-body {
    display: flex;
    align-items: baseline;
    gap: 0.5rem;
  }
}

/* 与应用壳的移动导航断点一致，手机和平板不再嵌套纵向滚动。 */
@media (min-width: 901px) {
  .logs-container {
    max-height: 70dvh;
    overflow: auto;
  }

  .logs-end {
    scroll-margin-bottom: 0;
  }
}
</style>
