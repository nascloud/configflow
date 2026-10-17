<template>
  <div class="min-w-0">
    <Toolbar v-model:search="search" placeholder="搜索策略组或节点">
      <template #actions>
        <Button variant="outline" class="min-h-11" :disabled="!groups.length" @click="toggleAll">
          <ChevronsUpDown class="size-3.5" />{{ allExpanded ? '全部收起' : '全部展开' }}
        </Button>
      </template>
    </Toolbar>

    <LoadingRows v-if="loading && !groups.length" :rows="5" />
    <SectionCard v-else-if="error && !groups.length">
      <EmptyState :icon="TriangleAlert" title="读取策略组失败" :description="error">
        <Button variant="outline" class="min-h-11" @click="load">重试</Button>
      </EmptyState>
    </SectionCard>
    <SectionCard v-else-if="!visibleGroups.length">
      <EmptyState :icon="Search" :title="search ? '没有匹配的策略组' : '暂无策略组'" :description="search ? '换个关键词试试。' : 'Mihomo 配置中没有可展示的策略组。'" />
    </SectionCard>

    <div v-else class="grid min-w-0 gap-3 xl:grid-cols-2">
      <section v-for="group in visibleGroups" :key="group.name" class="min-w-0 rounded-xl border border-border bg-card shadow-surface">
        <header class="flex min-w-0 items-center gap-2 p-2 pl-4">
          <button type="button" class="flex min-h-11 min-w-0 flex-1 items-center gap-3 rounded-md text-left focus-visible:outline-2 focus-visible:outline-ring"
            :aria-expanded="isExpanded(group.name)" @click="toggle(group.name)">
            <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform duration-200" :class="{ 'rotate-90': isExpanded(group.name) }" aria-hidden="true" />
            <span class="min-w-0 flex-1">
              <span class="flex min-w-0 flex-wrap items-center gap-2">
                <span class="break-all font-semibold text-foreground">{{ group.name }}</span>
                <span class="chip">{{ GROUP_TYPE_LABELS[group.type] || group.type }}</span>
                <span v-if="group.fixed" class="chip chip-warn">已固定</span>
                <span class="num text-xs text-muted-foreground">{{ group.all?.length || 0 }} 个</span>
              </span>
              <span v-if="group.now" class="mt-0.5 flex min-w-0 items-center gap-1.5 text-[12.5px] text-muted-foreground">
                <ArrowRight class="size-3 shrink-0" aria-hidden="true" />
                <span class="truncate">{{ group.now }}</span>
                <span class="num shrink-0" :class="toneClass(delayOf(group.now, group))">{{ delayText(delayOf(group.now, group)) }}</span>
              </span>
            </span>
          </button>
          <Button v-if="group.fixed" variant="ghost" size="sm" class="min-h-11" :disabled="busy.has(group.name)" @click="unfix(group)">取消固定</Button>
          <Button variant="ghost" size="icon" class="size-11 shrink-0" :aria-label="`测速 ${group.name}`" :title="'测速'"
            :disabled="testing.has(group.name)" @click="testGroup(group)">
            <Loader2 v-if="testing.has(group.name)" class="size-4 animate-spin" />
            <Zap v-else class="size-4" />
          </Button>
        </header>
        <div v-if="isExpanded(group.name)" class="flex flex-wrap gap-2 border-t border-border p-3">
          <button v-for="member in membersOf(group)" :key="member" type="button"
            class="flex min-h-11 min-w-0 max-w-full flex-col justify-center rounded-lg border px-3 py-1.5 text-left transition-colors duration-200 focus-visible:outline-2 focus-visible:outline-ring disabled:cursor-default"
            :class="member === group.now ? 'border-primary-accent/60 bg-primary-soft' : 'border-border bg-background hover:border-border-strong enabled:hover:bg-accent'"
            :disabled="!canSwitch(group) || busy.has(group.name)" :aria-pressed="member === group.now" @click="select(group, member)">
            <span class="max-w-60 truncate text-[13px] text-foreground">{{ member }}</span>
            <span class="flex items-center gap-2 text-[11.5px] text-muted-foreground">
              <span>{{ proxies[member]?.type || '—' }}</span>
              <span class="num" :class="toneClass(delayOf(member, group))">{{ delayText(delayOf(member, group)) }}</span>
            </span>
          </button>
        </div>
      </section>
    </div>

    <SectionCard v-if="providers.length" class="mt-5" title="代理集合" description="proxy-providers 订阅，可手动更新或触发健康检查" :padded="false">
      <div class="cf-table-wrap rounded-none border-x-0 border-b-0">
        <table class="cf-table min-w-[640px]">
          <thead><tr><th>名称</th><th>节点数</th><th>流量 / 到期</th><th>更新时间</th><th class="cf-table__right">操作</th></tr></thead>
          <tbody>
            <tr v-for="provider in providers" :key="provider.name">
              <td class="break-all font-medium">{{ provider.name }} <span class="chip ml-1">{{ provider.vehicleType }}</span></td>
              <td class="num">{{ provider.proxies?.length || 0 }}</td>
              <td class="text-xs text-muted-foreground">{{ subscriptionText(provider) }}</td>
              <td class="text-xs text-muted-foreground">{{ provider.updatedAt ? relativeTime(provider.updatedAt) : '—' }}</td>
              <td class="cf-table__right">
                <Button variant="ghost" size="sm" class="min-h-11" :disabled="busy.has('p:' + provider.name)" @click="healthcheck(provider)">健康检查</Button>
                <Button v-if="provider.vehicleType === 'HTTP'" variant="ghost" size="sm" class="min-h-11" :disabled="busy.has('p:' + provider.name)" @click="updateProvider(provider)">
                  <Loader2 v-if="busy.has('p:' + provider.name)" class="size-3.5 animate-spin" />更新
                </Button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </SectionCard>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ArrowRight, ChevronRight, ChevronsUpDown, Loader2, Search, TriangleAlert, Zap } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import Toolbar from '@/components/common/Toolbar.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import { mihomoPanelApi } from '@/api'
import { notify } from '@/lib/feedback'
import { formatBytes, relativeTime } from '@/lib/format'
import {
  GROUP_TYPE_LABELS, SWITCHABLE_GROUP_TYPES, delayText, delayTone, latestDelay, orderedGroups, panelError,
  type MihomoProvider, type MihomoProxy
} from '@/types/mihomo'

const props = defineProps<{ agentId: string; reloadToken: number }>()

const proxies = ref<Record<string, MihomoProxy>>({})
const providers = ref<MihomoProvider[]>([])
const loading = ref(false)
const error = ref('')
const search = ref('')
const expanded = ref(new Set<string>())
const testing = reactive(new Set<string>())
const busy = reactive(new Set<string>())
/** 本次会话里测速得到的延迟，优先于核心记录的历史 */
const delays = reactive(new Map<string, number>())

const groups = computed(() => orderedGroups(proxies.value))
const visibleGroups = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  if (!keyword) return groups.value
  return groups.value.filter(group => group.name.toLowerCase().includes(keyword)
    || group.all?.some(member => member.toLowerCase().includes(keyword)))
})
const allExpanded = computed(() => groups.value.length > 0 && groups.value.every(group => expanded.value.has(group.name)))

const isExpanded = (name: string) => expanded.value.has(name) || (!!search.value.trim() && visibleGroups.value.length <= 6)
function toggle(name: string) {
  const next = new Set(expanded.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  expanded.value = next
}
function toggleAll() {
  expanded.value = allExpanded.value ? new Set() : new Set(groups.value.map(group => group.name))
}

function membersOf(group: MihomoProxy) {
  const keyword = search.value.trim().toLowerCase()
  const members = group.all || []
  if (!keyword || group.name.toLowerCase().includes(keyword)) return members
  return members.filter(member => member.toLowerCase().includes(keyword))
}

const canSwitch = (group: MihomoProxy) => SWITCHABLE_GROUP_TYPES.has(group.type)

/** 成员是策略组时，延迟取它当前选中的节点 */
function delayOf(name: string, group?: MihomoProxy, depth = 0): number | undefined {
  if (delays.has(name)) return delays.get(name)
  const proxy = proxies.value[name]
  if (!proxy) return undefined
  const own = latestDelay(proxy, group?.testUrl)
  if (own !== undefined || !proxy.now || depth > 5) return own
  return delayOf(proxy.now, group, depth + 1)
}

const toneClass = (delay: number | undefined) => ({
  success: 'text-success-accent', warning: 'text-warning-accent', danger: 'text-destructive-accent', muted: 'text-muted-foreground'
})[delayTone(delay)]

function subscriptionText(provider: MihomoProvider) {
  const info = provider.subscriptionInfo
  if (!info?.Total) return '—'
  const used = formatBytes((info.Upload || 0) + (info.Download || 0))
  const expire = info.Expire ? ' · ' + new Date(info.Expire * 1000).toLocaleDateString() + ' 到期' : ''
  return `${used} / ${formatBytes(info.Total)}${expire}`
}

let epoch = 0
async function load() {
  const requestEpoch = ++epoch
  loading.value = true
  error.value = ''
  try {
    const [proxyResult, providerResult] = await Promise.all([
      mihomoPanelApi.proxies(props.agentId),
      mihomoPanelApi.proxyProviders(props.agentId).catch(() => null)
    ])
    if (requestEpoch !== epoch) return
    proxies.value = proxyResult.data.data?.proxies || {}
    const providerMap: Record<string, MihomoProvider> = providerResult?.data.data?.providers || {}
    providers.value = Object.values(providerMap).filter(provider => provider.vehicleType !== 'Compatible')
  } catch (failure) {
    if (requestEpoch === epoch) error.value = panelError(failure, '暂时无法读取策略组。')
  } finally {
    if (requestEpoch === epoch) loading.value = false
  }
}

async function select(group: MihomoProxy, member: string) {
  if (member === group.now && !group.fixed) return
  busy.add(group.name)
  try {
    await mihomoPanelApi.selectProxy(props.agentId, group.name, member)
    proxies.value[group.name] = { ...group, now: member, fixed: group.type === 'Selector' ? group.fixed : member }
    notify.success(`${group.name} 已切换到 ${member}`)
  } catch (failure) {
    notify.error('切换失败', panelError(failure))
  } finally {
    busy.delete(group.name)
  }
}

async function unfix(group: MihomoProxy) {
  busy.add(group.name)
  try {
    await mihomoPanelApi.unfixProxy(props.agentId, group.name)
    notify.success(`${group.name} 已恢复自动选择`)
    await load()
  } catch (failure) {
    notify.error('取消固定失败', panelError(failure))
  } finally {
    busy.delete(group.name)
  }
}

async function testGroup(group: MihomoProxy) {
  testing.add(group.name)
  try {
    const { data } = await mihomoPanelApi.groupDelay(props.agentId, group.name, group.testUrl)
    const result: Record<string, number> = data.data || {}
    for (const member of group.all || []) delays.set(member, result[member] ?? 0)
    // 自动策略组测速后可能换了节点，刷新一次当前选择
    if (group.type !== 'Selector') await load()
  } catch (failure) {
    notify.error(`${group.name} 测速失败`, panelError(failure))
  } finally {
    testing.delete(group.name)
  }
}

async function updateProvider(provider: MihomoProvider) {
  busy.add('p:' + provider.name)
  try {
    await mihomoPanelApi.updateProxyProvider(props.agentId, provider.name)
    notify.success(`${provider.name} 已更新`)
    await load()
  } catch (failure) {
    notify.error('更新代理集合失败', panelError(failure))
  } finally {
    busy.delete('p:' + provider.name)
  }
}

async function healthcheck(provider: MihomoProvider) {
  busy.add('p:' + provider.name)
  try {
    await mihomoPanelApi.healthcheckProxyProvider(props.agentId, provider.name)
    notify.success(`${provider.name} 健康检查完成`)
    delays.clear()
    await load()
  } catch (failure) {
    notify.error('健康检查失败', panelError(failure))
  } finally {
    busy.delete('p:' + provider.name)
  }
}

watch(() => props.reloadToken, () => { delays.clear(); load() })
onMounted(load)
</script>
