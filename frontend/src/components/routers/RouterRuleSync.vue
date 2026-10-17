<template>
  <SectionCard :title="`远程规则同步 · ${name}`" help="仅同步已成功发布的远程规则集。更新目的 IP 和受管静态路由，不自动应用来源、网关、NAT、DNS 或回流设置的改动。Mihomo 等订阅客户端仍按各自周期刷新，ROS 同步状态不代表它们的实时版本。">
    <template #actions>
      <Button variant="outline" class="min-h-11" :disabled="loading || saving || disabled" @click="load"><RefreshCw class="size-4" />刷新状态</Button>
      <Button variant="outline" class="min-h-11" :disabled="saving || disabled || running" @click="emit('preview')">预览发布</Button>
      <Button class="min-h-11" :disabled="!settings?.auto_rule_sync || !state?.ready || state.status === 'paused' || running || saving || loading || disabled" @click="syncNow">
        <Loader2 v-if="running" class="size-4 animate-spin" /><RefreshCw v-else class="size-4" />{{ running ? '同步中' : '立即同步' }}
      </Button>
    </template>
    <LoadingRows v-if="loading && !settings" :rows="3" />
    <div v-if="settings" class="grid min-w-0 gap-5">
      <div class="flex flex-wrap items-center justify-between gap-4 border-b border-border pb-4">
        <div class="flex min-h-11 items-center gap-3">
          <Switch :id="`rule-sync-${id}`" :model-value="settings.auto_rule_sync" :disabled="saving || loading || disabled" @update:model-value="value => save({ ...settings!, auto_rule_sync: Boolean(value) })" />
          <Label :for="`rule-sync-${id}`" class="flex min-h-11 items-center">规则更新后自动同步到 ROS</Label>
        </div>
        <div class="flex items-center gap-3">
          <Label :for="`rule-sync-interval-${id}`" class="whitespace-nowrap text-muted-foreground">检查间隔</Label>
          <Select :model-value="String(settings.rule_sync_interval)" :disabled="saving || loading || disabled" @update:model-value="value => save({ ...settings!, rule_sync_interval: Number(value) })">
            <SelectTrigger :id="`rule-sync-interval-${id}`" class="min-h-11 w-32"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem v-for="item in intervals" :key="item.value" :value="String(item.value)">{{ item.label }}</SelectItem></SelectContent>
          </Select>
        </div>
      </div>
      <div v-if="state" class="grid gap-2" aria-live="polite">
        <div class="flex flex-wrap items-center gap-2"><span :class="['chip', tone]">{{ settings.auto_rule_sync ? statusLabel : '自动同步已关闭' }}</span><span class="text-sm">{{ state.message }}</span></div>
        <div class="flex flex-wrap gap-x-6 gap-y-2 text-xs text-muted-foreground">
          <span>最近检查：{{ time(state.checked_at) }}</span><span>内容更新：{{ time(state.updated_at) }}</span><span>ROS 同步：{{ time(state.synced_at) }}</span>
          <span>最近同步变更：新增 <span class="num">{{ state.added }}</span> · 删除 <span class="num">{{ state.removed }}</span></span>
        </div>
      </div>
      <p v-if="!state?.ready" class="m-0 text-sm text-muted-foreground">首次使用请先「预览发布」，成功后建立同步基准。仅保存设置不会接管路由器上的已有规则。</p>
      <div v-if="state?.rules.length" class="cf-table-wrap">
        <table class="cf-table min-w-[660px]">
          <thead><tr><th>规则集</th><th class="text-right">有效 IPv4</th><th class="text-right">域名</th><th class="text-right">其他条目</th><th>最近读取版本</th><th>ROS 已同步版本</th></tr></thead>
          <tbody><tr v-for="rule in state.rules" :key="rule.id"><td>{{ rule.name }}<span v-if="rule.source_type !== 'url'" class="ml-2 text-xs text-muted-foreground">本地</span></td><td class="text-right num">{{ rule.ip_count }}</td><td class="text-right num">{{ rule.domain_count }}</td><td class="text-right num">{{ rule.ignored_count }}</td><td class="font-mono text-xs">{{ rule.version }}</td><td class="font-mono text-xs">{{ rule.synced_version || '—' }}</td></tr></tbody>
        </table>
      </div>
      <p class="m-0 text-xs leading-relaxed text-muted-foreground">混合规则中的域名不会解析成 IP 下发。纯 IP 规则格式异常，或某个规则集的有效 IPv4 变为零时，将暂停自动同步，保留原状态并提示人工核对。关闭开关只停止后续同步。</p>
      <details v-if="state?.log.length" class="text-sm"><summary class="cursor-pointer py-2">最近同步明细</summary><ul class="my-2 grid gap-1 pl-5"><li v-for="(item, index) in state.log" :key="index">{{ item.summary }}</li></ul></details>
    </div>
    <p v-if="error" role="alert" class="mt-4 mb-0 break-words text-sm text-destructive-accent">{{ error }}</p>
  </SectionCard>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Loader2, RefreshCw } from '@lucide/vue'
import SectionCard from '@/components/common/SectionCard.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { routerApi, type RouterRuleSyncSettings, type RouterRuleSyncState } from '@/api'
import { notify } from '@/lib/feedback'
import { beginScopedRequest, endScopedRequest } from '@/profileContext'
const props = defineProps<{ id: string; name: string; profile: string; disabled?: boolean }>()
const emit = defineEmits<{ preview: []; busy: [value: boolean] }>()
const settings = ref<RouterRuleSyncSettings | null>(null), state = ref<RouterRuleSyncState | null>(null)
const loading = ref(false), saving = ref(false), error = ref('')
const running = computed(() => ['running', 'queued'].includes(state.value?.status || ''))
const labels: Record<string, string> = { synced: '已同步', waiting: '等待首次发布', queued: '等待同步', running: '正在同步', paused: '已暂停，需核对', failed: '同步失败，将重试' }
const statusLabel = computed(() => labels[state.value?.status || 'waiting'] || '待核对')
const tone = computed(() => !settings.value?.auto_rule_sync ? '' : state.value?.status === 'synced' ? 'chip-ok' : ['paused', 'failed'].includes(state.value?.status || '') ? 'chip-warn' : 'chip-sky')
const intervals = [15, 30, 60, 180, 360, 720, 1440].map(value => ({ value, label: value < 60 ? `${value} 分钟` : `${value / 60} 小时` }))
const time = (value?: string | null) => value ? new Date(value).toLocaleString() : '—'
const message = (reason: any) => reason?.response?.data?.message || reason?.message || '操作失败，请重试'
let epoch = 0, timer: ReturnType<typeof setTimeout> | undefined
function schedule() {
  clearTimeout(timer)
  if (running.value) timer = setTimeout(() => { if (!document.hidden) void load() }, 2000)
}
async function load() {
  const current = ++epoch
  clearTimeout(timer); loading.value = true; error.value = ''
  try {
    const { data } = await routerApi.ruleSync(props.id, props.profile)
    if (current !== epoch) return
    settings.value = data.settings; state.value = data.sync; schedule()
  } catch (reason) { if (current === epoch) error.value = message(reason) }
  finally { if (current === epoch) loading.value = false }
}
async function mutate(action: () => ReturnType<typeof routerApi.ruleSync>, success: string) {
  if (saving.value || props.disabled) return
  const current = ++epoch
  clearTimeout(timer); loading.value = false; saving.value = true; error.value = ''
  emit('busy', true); beginScopedRequest()
  try {
    const { data } = await action()
    if (current !== epoch) return
    settings.value = data.settings; state.value = data.sync
    notify.success(success); schedule()
  } catch (reason) { if (current === epoch) error.value = message(reason) }
  finally { endScopedRequest(); if (current === epoch) { saving.value = false; emit('busy', false) } }
}
function save(value: RouterRuleSyncSettings) { return mutate(() => routerApi.saveRuleSync(props.id, value, props.profile), '同步设置已保存') }
function syncNow() { return mutate(() => routerApi.startRuleSync(props.id, props.profile), '已开始检查远程规则') }
function visible() { if (!document.hidden && running.value && !saving.value) void load() }
document.addEventListener('visibilitychange', visible)
watch(() => [props.id, props.profile], () => { settings.value = null; state.value = null; saving.value = false; emit('busy', false); void load() }, { immediate: true })
onBeforeUnmount(() => { epoch++; clearTimeout(timer); document.removeEventListener('visibilitychange', visible); emit('busy', false) })
</script>
