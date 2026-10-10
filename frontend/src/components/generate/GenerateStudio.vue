<template>
  <div class="mb-5">
    <!-- 目标切换放进页头右侧，与标题同一行 -->
    <Teleport defer to="#generate-header-actions">
      <Segmented v-model="active" label="生成目标" :options="targetOptions" :disabled="running" />
    </Teleport>

    <div class="grid grid-cols-[300px_minmax(0,1fr)] items-start gap-3.5 max-[1180px]:grid-cols-[minmax(0,1fr)]">
      <div class="flex min-w-0 flex-col gap-3.5">
        <!-- 编译步骤：计数来自真实数据，前四步在请求返回前推进，最后一步随结果写出 -->
        <SectionCard :padded="false" class="p-[18px]" role="list" aria-label="生成步骤">
          <div
            v-for="(step, i) in steps"
            :key="step.title"
            role="listitem"
            :class="cn('gen-step relative grid grid-cols-[26px_1fr] gap-3 pb-5 last:pb-0', stepState(i))"
          >
            <span
              class="gen-bullet grid size-[26px] place-items-center rounded-full border-[1.5px] border-border-strong bg-card font-mono text-[11px] text-muted-foreground transition-all duration-300"
            >
              <Check v-if="stepState(i) === 'done'" class="size-3.5" :stroke-width="2.5" />
              <X v-else-if="stepState(i) === 'fail'" class="size-3.5" :stroke-width="2.5" />
              <template v-else>{{ i + 1 }}</template>
            </span>
            <div class="min-w-0">
              <b class="mt-[3px] block text-[13px] font-semibold">{{ step.title }}</b>
              <span class="block truncate font-mono text-[12px] text-muted-foreground">{{ step.meta }}</span>
            </div>
          </div>
        </SectionCard>

        <!-- 当前目标的订阅地址与设置（原「生成目标」卡片的全部功能） -->
        <SectionCard v-if="current" :padded="false" class="p-[18px]">
          <div class="flex items-center gap-2">
            <component :is="current.icon" class="size-4 text-primary-accent" :stroke-width="2" aria-hidden="true" />
            <b class="text-[13px] font-semibold">{{ current.title }} 订阅地址</b>
          </div>
          <div class="mt-2.5 flex items-center gap-1.5">
            <Input
              :model-value="current.urlDisplay"
              readonly
              class="h-9 bg-background/50 font-mono text-[11.5px]"
              :aria-label="`${current.title} 配置 URL`"
            />
            <Button
              variant="outline"
              size="icon"
              class="size-9 shrink-0"
              :title="`复制 ${current.title} 配置 URL`"
              :aria-label="`复制 ${current.title} 配置 URL`"
              @click="emit('copy', current.url, current.title)"
            >
              <Copy class="size-4" />
            </Button>
          </div>
          <p class="mt-1.5 mb-0 text-[11.5px] text-muted-foreground">客户端可直接订阅此地址。</p>
          <div class="mt-3 flex flex-wrap gap-1.5">
            <Button
              v-for="action in current.actions"
              :key="action.label"
              variant="outline"
              size="sm"
              :disabled="action.loading"
              @click="action.run"
            >
              <Loader2 v-if="action.loading" class="size-3.5 animate-spin" />
              <component :is="action.icon" v-else class="size-3.5" />
              {{ action.label }}
            </Button>
          </div>
        </SectionCard>
      </div>

      <!-- 代码面板 -->
      <SectionCard :padded="false" class="relative overflow-hidden">
        <div class="flex items-center gap-2.5 border-b border-border px-3.5 py-3">
          <span class="flex gap-1.5" aria-hidden="true">
            <i class="size-2.5 rounded-full bg-accent" /><i class="size-2.5 rounded-full bg-accent" /><i class="size-2.5 rounded-full bg-accent" />
          </span>
          <span class="font-mono text-[12px] text-muted-foreground">{{ fileName }}</span>
          <span v-if="lines.length && !running" class="font-mono text-[11px] text-muted-foreground/80">· {{ lines.length }} 行</span>
          <span class="flex-1" />
          <Button variant="ghost" size="sm" class="h-[30px]" :disabled="!output || running" @click="copyOutput">
            <Copy class="size-3.5" />
            复制
          </Button>
          <Button
            variant="outline"
            size="sm"
            class="h-[30px]"
            :disabled="!output || running || pushing || !pushTargets.length"
            :title="pushTitle"
            @click="pushToAgents"
          >
            <Loader2 v-if="pushing" class="size-3.5 animate-spin" />
            <Send v-else class="size-3.5" />
            推送
          </Button>
          <Button size="sm" class="h-[30px] shadow-glow" :disabled="running" @click="generate">
            <Play class="size-3.5" />
            生成
          </Button>
        </div>

        <pre
          ref="codeEl"
          class="gen-code m-0 h-[520px] overflow-auto bg-background/70 py-4 font-mono text-[12.5px] leading-[1.7] max-md:h-[60dvh]"
          :aria-busy="running"
        ><span
          v-for="(line, i) in lines"
          :key="i"
          class="gen-line block whitespace-pre pr-[18px]"
          :class="i >= freshFrom && 'is-new'"
          v-html="line || ' '"
        /><span v-if="running && phase === 'write'" class="gen-caret" aria-hidden="true" /></pre>

        <div
          v-if="!lines.length && !running"
          class="absolute inset-x-0 top-[55px] bottom-0 grid place-content-center gap-2.5 bg-background/70 text-center text-muted-foreground"
        >
          <BrandMark class="mx-auto size-11 text-primary" />
          <b class="font-display text-[24px] font-medium text-foreground/80">{{ error ? '生成失败' : '准备就绪' }}</b>
          <span class="text-[12.5px]">{{ error || `按「生成」开始编译，或 ${metaKey} + Enter` }}</span>
        </div>
      </SectionCard>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch, type Component } from 'vue'
import { useRouter } from 'vue-router'
import { Check, Copy, Loader2, Play, Send, X } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import BrandMark from '@/components/common/BrandMark.vue'
import Segmented from '@/components/common/Segmented.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import { agentApi, generateApi, ruleSetApi, statsApi } from '@/api'
import { consumeAction } from '@/lib/actions'
import { notify } from '@/lib/feedback'
import { cn } from '@/lib/utils'
import { useLive } from '@/stores/live'

type TargetKey = 'mihomo' | 'surge' | 'loon' | 'mosdns'

export interface StudioTarget {
  key: TargetKey
  title: string
  icon: Component
  url: string
  urlDisplay: string
  actions: Array<{ label: string; icon: Component; run: () => void; loading?: boolean }>
}

const props = defineProps<{ targets: StudioTarget[] }>()
const emit = defineEmits<{ (e: 'copy', url: string, title: string): void }>()

const router = useRouter()
const live = useLive()
const metaKey = /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘' : 'Ctrl'

const active = ref<TargetKey>('mihomo')
const targetOptions = computed(() => props.targets.map(t => ({ value: t.key, label: t.title })))
const current = computed(() => props.targets.find(t => t.key === active.value))

const FILES: Record<TargetKey, string> = { mihomo: 'config.yaml', surge: 'surge.conf', loon: 'loon.conf', mosdns: 'mosdns.yaml' }
const fileName = computed(() => FILES[active.value])

/* ---------- 步骤（计数取真实数据） ---------- */
const counts = ref({ subscriptions: 0, nodes: 0, proxyGroups: 0, rules: 0, ruleSets: 0 })

const steps = computed(() => {
  const c = counts.value
  if (active.value === 'mosdns') {
    return [
      { title: '收集资源', meta: `${c.ruleSets} 规则集 · ${c.rules} 条规则` },
      { title: '读取分流规则', meta: '直连 / 代理规则集' },
      { title: '组装 DNS 上游', meta: '国内 / 国外 / Fallback' },
      { title: '编译分流序列', meta: '缓存 · Hosts · 默认转发' },
      { title: '写出配置', meta: fileName.value }
    ]
  }
  return [
    { title: '收集资源', meta: `${c.subscriptions} 订阅 · ${c.nodes} 节点` },
    { title: '解析节点', meta: 'Sub-Store 转换' },
    { title: '组装策略组', meta: `${c.proxyGroups} 组 · 正则筛选` },
    { title: '编译规则', meta: `${c.rules} 条 · ${c.ruleSets} 规则集` },
    { title: '写出配置', meta: fileName.value }
  ]
})

/** -1 未开始；0..4 进行中；5 全部完成 */
const stepIndex = ref(-1)
const failedAt = ref(-1)
const stepState = (i: number) => {
  if (failedAt.value === i) return 'fail'
  if (i < stepIndex.value) return 'done'
  if (i === stepIndex.value) return 'run'
  return ''
}

/* ---------- 生成 ---------- */
const running = ref(false)
const phase = ref<'steps' | 'write' | ''>('')
const output = ref('')
const lines = ref<string[]>([])
const freshFrom = ref(0)
const error = ref('')
const codeEl = ref<HTMLElement | null>(null)

const wait = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms))
const frame = () => new Promise(resolve => requestAnimationFrame(() => resolve(null)))

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/** 轻量高亮：YAML 键 / 字符串 / 数字与布尔 / 注释；Surge / Loon 的 [段] 与 key = value */
const highlight = (raw: string): string => {
  if (/^\s*#/.test(raw) || /^\s*\/\//.test(raw)) return `<span class="y-c">${esc(raw)}</span>`
  if (/^\[.*\]\s*$/.test(raw)) return `<span class="y-k">${esc(raw)}</span>`
  let s = esc(raw)
  s = s.replace(/(&quot;|")([^"]*)\1|'([^']*)'/g, m => `<span class="y-s">${m}</span>`)
  s = s.replace(/^(\s*-?\s*)([\w.-]+)(:)(?=\s|$)/, '$1<span class="y-k">$2</span><span class="y-d">$3</span>')
  s = s.replace(/^([\w .-]+?)(\s=\s)/, '<span class="y-k">$1</span><span class="y-d">$2</span>')
  s = s.replace(/(:\s|=\s|,\s?)(-?\d+(?:\.\d+)?|true|false|null)(?=\s*(?:,|$|\}))/g, '$1<span class="y-n">$2</span>')
  s = s.replace(/\b(DIRECT|REJECT|MATCH|FINAL)\b/g, '<span class="y-n">$1</span>')
  return s
}

const loadCounts = async () => {
  const [stats, ruleSets] = await Promise.allSettled([statsApi.getOverview(), ruleSetApi.getAll()])
  if (stats.status === 'fulfilled' && stats.value.data?.success) {
    const d = stats.value.data.data
    counts.value = {
      ...counts.value,
      subscriptions: d.subscriptions?.total ?? 0,
      nodes: d.nodes?.total ?? 0,
      proxyGroups: d.proxyGroups?.total ?? 0,
      rules: d.rules?.total ?? 0
    }
  }
  if (ruleSets.status === 'fulfilled') counts.value = { ...counts.value, ruleSets: (ruleSets.value.data || []).length }
}

const generate = async () => {
  if (running.value) return
  running.value = true
  phase.value = 'steps'
  error.value = ''
  failedAt.value = -1
  lines.value = []
  output.value = ''
  freshFrom.value = 0
  const target = active.value
  const api = {
    mihomo: generateApi.previewMihomo,
    surge: generateApi.previewSurge,
    loon: generateApi.previewLoon,
    mosdns: generateApi.previewMosdns
  }[target]

  const request = api()
    .then(res => ({ ok: true as const, content: String(res.data?.content ?? res.data ?? '') }))
    .catch((err: any) => ({ ok: false as const, message: err.response?.data?.message || '生成失败' }))
  loadCounts()

  try {
    for (let i = 0; i < 4; i++) {
      stepIndex.value = i
      await wait(320 + Math.random() * 180)
    }
    const result = await request
    if (!result.ok) {
      failedAt.value = 3
      error.value = result.message
      notify.error(`${FILES[target]} 生成失败`, result.message)
      return
    }

    stepIndex.value = 4
    phase.value = 'write'
    output.value = result.content
    const all = result.content.replace(/\n$/, '').split('\n')
    // 约 150 帧写完：短配置逐行出现，长配置按块追加，避免卡顿
    const perFrame = Math.max(1, Math.ceil(all.length / 150))
    for (let i = 0; i < all.length; i += perFrame) {
      freshFrom.value = lines.value.length
      lines.value.push(...all.slice(i, i + perFrame).map(highlight))
      await frame()
      if (codeEl.value) codeEl.value.scrollTop = codeEl.value.scrollHeight
    }
    stepIndex.value = 5
    freshFrom.value = lines.value.length
    await nextTick()
    if (codeEl.value) codeEl.value.scrollTop = 0
    notify.success(`${FILES[target]} 已生成 · ${all.length} 行`)
  } finally {
    running.value = false
    phase.value = ''
  }
}

const copyOutput = async () => {
  try {
    await navigator.clipboard.writeText(output.value)
    notify.success('已复制到剪贴板')
  } catch {
    notify.error('复制失败，请在面板中手动选择')
  }
}

/* ---------- 推送：当前配置空间里同类型且在线的 Agent ---------- */
const pushing = ref(false)
const pushTargets = computed(() =>
  active.value === 'loon'
    ? []
    : live.boundAgents.value.filter(a => a.service_type === active.value && live.isOnline(a))
)
const pushTitle = computed(() => {
  if (active.value === 'loon') return 'Loon 没有对应的 Agent'
  if (!pushTargets.value.length) return `没有在线的 ${current.value?.title} Agent`
  return `推送到 ${pushTargets.value.map(a => a.name).join('、')}`
})

const pushToAgents = async () => {
  if (!pushTargets.value.length || pushing.value) return
  pushing.value = true
  const results = await Promise.allSettled(pushTargets.value.map(a => agentApi.pushConfig(a.id)))
  pushing.value = false
  const ok = results.filter(r => r.status === 'fulfilled' && (r.value as any).data?.success !== false).length
  const failed = results.length - ok
  if (failed) notify.warning(`已推送 ${ok} 台，${failed} 台失败`)
  else notify.success(`已推送到 ${ok} 台 Agent`)
  live.refresh()
}

/* ---------- 快捷键与跨页动作 ---------- */
const onKeydown = (event: KeyboardEvent) => {
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
    event.preventDefault()
    generate()
  }
}

const runPending = () => {
  if (consumeAction(router, 'generate-mihomo')) {
    active.value = 'mihomo'
    generate()
  }
}

watch(active, () => {
  if (running.value) return
  lines.value = []
  output.value = ''
  stepIndex.value = -1
  failedAt.value = -1
  error.value = ''
})

watch(() => router?.currentRoute.value.query.run, run => run === 'generate-mihomo' && runPending())

onMounted(() => {
  live.start()
  loadCounts()
  document.addEventListener('keydown', onKeydown)
  runPending()
})

onUnmounted(() => {
  live.stop()
  document.removeEventListener('keydown', onKeydown)
})
</script>

<style scoped>
.gen-step::before {
  content: '';
  position: absolute;
  top: 26px;
  bottom: 2px;
  left: 12px;
  width: 2px;
  background: var(--border-strong);
}

.gen-step::after {
  content: '';
  position: absolute;
  top: 26px;
  left: 12px;
  width: 2px;
  height: 0;
  background: var(--primary);
  transition: height 0.5s var(--ease-flow);
}

.gen-step:last-child::before,
.gen-step:last-child::after {
  display: none;
}

.gen-step.done::after {
  height: calc(100% - 28px);
}

.gen-step.run .gen-bullet {
  border-color: var(--primary);
  color: var(--primary-accent);
  box-shadow: 0 0 0 5px var(--primary-soft);
}

.gen-step.done .gen-bullet {
  border-color: var(--primary);
  background: var(--primary);
  color: var(--primary-foreground);
}

.gen-step.fail .gen-bullet {
  border-color: var(--destructive);
  background: var(--destructive);
  color: var(--destructive-foreground);
}

.gen-code {
  counter-reset: ln;
}

.gen-line::before {
  counter-increment: ln;
  content: counter(ln);
  display: inline-block;
  width: 46px;
  padding-right: 14px;
  text-align: right;
  color: var(--muted-foreground);
  opacity: 0.6;
}

.gen-line.is-new {
  animation: line-in 0.35s var(--ease-flow);
}

@keyframes line-in {
  from {
    background: var(--primary-soft);
  }
}

.gen-caret {
  display: inline-block;
  width: 8px;
  height: 15px;
  margin-left: 60px;
  vertical-align: -3px;
  background: var(--primary-accent);
  animation: caret-blink 1s steps(1) infinite;
}

@keyframes caret-blink {
  50% {
    opacity: 0;
  }
}

.gen-code :deep(.y-k) {
  color: var(--primary-accent);
}

.gen-code :deep(.y-s) {
  color: var(--success-accent);
}

.gen-code :deep(.y-n) {
  color: var(--info-accent);
}

.gen-code :deep(.y-c) {
  color: var(--muted-foreground);
  font-style: italic;
}

.gen-code :deep(.y-d) {
  color: var(--muted-foreground);
}
</style>
