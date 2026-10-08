<template>
  <div class="flex flex-col gap-5">
    <ScopeBanner scope="profile" :profile-name="profileName" />
    <PageHeader eyebrow="Profile" title="当前配置资源" description="仅选择共享资源的引用，不复制或覆盖资源字段。策略组仅使用这里选中的来源。">
      <template #actions>
        <Button :disabled="!ready || saving || dialerSaving" @click="saveResources">{{ saving ? '保存中…' : '保存选择' }}</Button>
      </template>
    </PageHeader>
    <p class="text-sm leading-relaxed text-muted-foreground">聚合自动包含依赖的订阅和节点，无需重复勾选。策略组或拨号代理正在使用的资源不能移除，请先调整引用。编辑共享资源会影响所有引用它的配置。</p>
    <LoadingRows v-if="loading" :rows="4" />
    <template v-else-if="ready">
      <SectionCard v-for="section in sections" :key="section.key" :title="`${section.label}（已选 ${selected[section.key].length}）`">
        <template #actions>
          <Button as-child variant="outline" size="sm"><RouterLink :to="section.route">管理共享{{ section.label }}</RouterLink></Button>
        </template>
        <p v-if="!catalog[section.key].length" class="text-sm text-muted-foreground">暂无共享{{ section.label }}</p>
        <div v-else class="grid min-w-0 gap-2 sm:grid-cols-2">
          <label v-for="item in catalog[section.key]" :key="item.id" class="flex min-w-0 cursor-pointer items-center gap-2 rounded-lg border border-border/50 bg-background/40 px-3 py-2">
            <Checkbox :aria-label="`选择${section.label} ${item.name}`" :model-value="selected[section.key].includes(item.id)" :disabled="saving || dialerSaving" @update:model-value="toggleResource(section.key, item.id)" />
            <span class="min-w-0 flex-1 break-all text-sm">{{ item.name }}</span>
            <Badge v-if="item.enabled === false" variant="outline" class="shrink-0">已停用</Badge>
          </label>
        </div>
      </SectionCard>
      <SectionCard title="节点拨号代理（仅 Mihomo）" description="针对已保存选择的手动节点（含聚合依赖）设置本配置覆盖；不会修改共享节点的原始 dialer-proxy。新增选择请先保存。">
        <p v-if="!dialerNodes.length" class="text-sm text-muted-foreground">尚未选择手动节点。</p>
        <div class="flex min-w-0 flex-col gap-2">
          <div v-for="node in dialerNodes" :key="node.id" class="flex min-w-0 items-center gap-3 rounded-lg border border-border/50 bg-background/40 px-3 py-2">
            <div class="min-w-0 flex-1">
              <p class="break-all text-sm font-medium">{{ node.name }}</p>
              <p class="break-all text-xs text-muted-foreground">{{ referenceLabel(dialers[node.id]) }}</p>
            </div>
            <Button variant="outline" size="sm" class="shrink-0" :disabled="saving || dialerSaving" :aria-label="`编辑拨号代理 ${node.name}`" @click="editDialer(node)">设置拨号代理</Button>
          </div>
        </div>
      </SectionCard>
    </template>
    <Button v-else variant="outline" class="self-start" @click="loadResources">重新加载</Button>
    <Dialog :open="dialogVisible" @update:open="value => { if (!dialerSaving) dialogVisible = value }">
      <DialogContent class="glass-strong hairline max-w-[640px] border-border/50">
        <DialogHeader>
          <DialogTitle class="min-w-0 break-all pr-6">拨号代理 · {{ editingNode?.name }}</DialogTitle>
          <DialogDescription>仅作用于当前配置；服务器校验全部依赖分支并阻止循环。</DialogDescription>
        </DialogHeader>
        <div class="flex min-w-0 flex-col gap-3">
          <Label>拨号代理（仅 Mihomo）</Label>
          <Input v-model="dialerSearch" :disabled="dialerSaving" placeholder="搜索节点或静态策略组" aria-label="搜索拨号代理" />
          <Select v-model="dialerSelection" :disabled="dialerSaving">
            <SelectTrigger data-testid="dialer-trigger" class="data-[size=default]:h-auto min-h-9 w-full min-w-0 [&_[data-slot=select-value]]:line-clamp-none"><SelectValue class="min-w-0 whitespace-normal break-all text-left">{{ dialerLabel }}</SelectValue></SelectTrigger>
            <SelectContent class="max-w-[calc(100vw-32px)]">
              <SelectItem value="none">不覆盖（保留原始值）</SelectItem>
              <SelectItem v-for="candidate in dialerCandidates" :key="candidate.value" :value="candidate.value" class="whitespace-normal break-all">{{ candidate.label }}</SelectItem>
            </SelectContent>
          </Select>
          <p v-if="dialerUnavailable" role="alert" class="text-xs text-destructive-accent">当前稳定引用目标不可用，请重新选择或清除覆盖；不会自动清除。</p>
          <p v-if="legacyDialer" class="break-all text-xs text-muted-foreground">原始 dialer-proxy：{{ legacyDialer }}；{{ draftDialer ? '当前由稳定引用覆盖' : '将保留原始值' }}</p>
          <Button v-if="draftDialer" variant="outline" :disabled="dialerSaving" @click="draftDialer = null">清除覆盖（恢复原始值）</Button>
          <p class="text-xs text-muted-foreground">清除只移除稳定引用覆盖，不删除节点字符串/params 的原始值；如需关闭原始拨号，请在共享节点库显式编辑原始配置。</p>
          <p class="text-xs text-muted-foreground">只支持当前配置已选择并启用的手动节点和静态策略组；订阅、聚合、跟随组不支持。</p>
        </div>
        <DialogFooter>
          <Button variant="outline" :disabled="dialerSaving" @click="dialogVisible = false">取消</Button>
          <Button :disabled="dialerSaving" @click="saveDialer">{{ dialerSaving ? '保存中…' : '保存' }}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, RouterLink } from 'vue-router'
import * as yaml from 'js-yaml'
import api, { nodeApi, profileApi, proxyGroupApi } from '@/api'
import { getActiveProfileId } from '@/profileContext'
import { useProfileStore } from '@/stores/profile'
import type { ProxyGroup, ProxyNode } from '@/types'
import { confirm, notify } from '@/lib/feedback'
import ScopeBanner from '@/components/shell/ScopeBanner.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

type ResourceRefs = { subscriptions: string[]; nodes: string[]; subscription_aggregations: string[] }
type DialerRef = { type: 'node' | 'group'; id: string }
type NodeDialers = Record<string, DialerRef>
type Resource = { id: string; name: string; enabled?: boolean; nodes?: string[] }
type DialerGroup = ProxyGroup & {
  aggregations?: string[]
  use?: string[]
  follow_group?: string
  include_all?: boolean
  'include-all'?: boolean
  proxies_order?: { type: string; id: string }[]
}
function errorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'response' in error) {
    const response = error.response
    if (response && typeof response === 'object' && 'data' in response) {
      const data = response.data
      if (data && typeof data === 'object' && 'message' in data && typeof data.message === 'string') return data.message
    }
  }
  return fallback
}
const profileId = getActiveProfileId()
const profileStore = useProfileStore()
const profileName = computed(() => profileStore.profiles.value.find(profile => profile.id === profileId)?.name || profileId)
const sections: { key: keyof ResourceRefs; label: string; route: string }[] = [
  { key: 'subscriptions', label: '订阅', route: '/subscriptions' },
  { key: 'nodes', label: '节点', route: '/nodes' },
  { key: 'subscription_aggregations', label: '聚合', route: '/subscription-aggregation' }
]
const catalog = ref<Record<keyof ResourceRefs, Resource[]>>({ subscriptions: [], nodes: [], subscription_aggregations: [] })
const nodes = ref<ProxyNode[]>([])
const groups = ref<DialerGroup[]>([])
const selected = ref<ResourceRefs>({ subscriptions: [], nodes: [], subscription_aggregations: [] })
const savedResources = ref<ResourceRefs>({ subscriptions: [], nodes: [], subscription_aggregations: [] })
const dialers = ref<NodeDialers>({})
const loading = ref(true)
const ready = ref(false)
const saving = ref(false)
const dialerSaving = ref(false)
const dirty = computed(() => ready.value && JSON.stringify(selected.value) !== JSON.stringify(savedResources.value))
const copyResources = (value: ResourceRefs): ResourceRefs => ({ subscriptions: [...value.subscriptions], nodes: [...value.nodes], subscription_aggregations: [...value.subscription_aggregations] })
const selectedNodes = computed(() => {
  const selectedIds = new Set(savedResources.value.nodes)
  for (const aggregation of catalog.value.subscription_aggregations) {
    if (savedResources.value.subscription_aggregations.includes(aggregation.id)) {
      for (const id of aggregation.nodes || []) selectedIds.add(id)
    }
  }
  return nodes.value.filter(node => selectedIds.has(node.id) && !node.subscription_id)
})
const dialerNodes = computed(() => {
  const result: Resource[] = [...selectedNodes.value]
  for (const id of Object.keys(dialers.value)) {
    if (!result.some(node => node.id === id)) result.push({ id, name: nodes.value.find(node => node.id === id)?.name || `${id}（不可用）` })
  }
  return result
})
function toggleResource(key: keyof ResourceRefs, id: string) {
  const ids = selected.value[key]
  selected.value[key] = ids.includes(id) ? ids.filter(value => value !== id) : [...ids, id]
}
async function loadResources() {
  loading.value = true
  ready.value = false
  try {
    const [refs, subscriptions, nodeRows, aggregations, mappings, groupRows] = await Promise.all([
      profileApi.getResources(profileId), api.get('/subscriptions'), nodeApi.getAll(), api.get('/aggregations'),
      profileApi.getNodeDialers(profileId), proxyGroupApi.getAll(profileId)
    ])
    catalog.value = { subscriptions: subscriptions.data, nodes: nodeRows.data, subscription_aggregations: aggregations.data }
    nodes.value = nodeRows.data
    groups.value = groupRows.data
    selected.value = copyResources(refs.data)
    savedResources.value = copyResources(refs.data)
    dialers.value = mappings.data
    ready.value = true
  } catch (error: unknown) {
    notify.error(errorMessage(error, '加载当前配置资源失败'))
  } finally { loading.value = false }
}
async function saveResources() {
  if (!ready.value || saving.value || dialerSaving.value) return
  saving.value = true
  const snapshot = copyResources(selected.value)
  try {
    await profileApi.saveResources(profileId, snapshot)
    savedResources.value = copyResources(snapshot)
    notify.success('当前配置资源选择已保存')
  } catch (error: unknown) {
    notify.error(errorMessage(error, '保存失败，请先移除策略组或拨号代理对资源的引用'))
  } finally { saving.value = false }
}
const dialogVisible = ref(false)
const editingNode = ref<Resource | null>(null)
const draftDialer = ref<DialerRef | null>(null)
const dialerSearch = ref('')
function editDialer(node: Resource) {
  editingNode.value = node
  draftDialer.value = dialers.value[node.id] ? { ...dialers.value[node.id] } : null
  dialerSearch.value = ''
  dialogVisible.value = true
}
const legacyDialer = computed(() => {
  const node = nodes.value.find(node => node.id === editingNode.value?.id)
  if (!node?.proxy_string) return node?.params?.['dialer-proxy']
  try {
    let parsed: unknown = yaml.load(node.proxy_string)
    if (Array.isArray(parsed)) parsed = parsed[0]
    if (parsed && typeof parsed === 'object' && 'proxies' in parsed && Array.isArray(parsed.proxies)) parsed = parsed.proxies[0]
    return parsed && typeof parsed === 'object' && 'dialer-proxy' in parsed ? parsed['dialer-proxy'] : undefined
  } catch { return undefined }
})
const dialerSelection = computed({
  get: () => draftDialer.value ? JSON.stringify({ type: draftDialer.value.type, id: draftDialer.value.id }) : 'none',
  set: (value: string) => { draftDialer.value = value === 'none' ? null : JSON.parse(value) }
})
const eligibleCandidates = computed(() => {
  const staticGroup = (id: string, seen = new Set<string>()): boolean => {
    if (seen.has(id)) return false
    const group = groups.value.find(group => group.id === id)
    if (!group || group.enabled === false || group.subscriptions?.length || group.aggregations?.length || group.use?.length || group.follow_group || group.include_all || group['include-all']) return false
    if (group.source === 'subscription' && group.proxies?.length) return false
    if (group.proxies_order?.some(member => !['node', 'strategy'].includes(member.type))) return false
    const next = new Set(seen).add(id)
    const members = group.proxies_order?.length ? group.proxies_order : [
      ...(group.manual_nodes || (group.source === 'node' ? group.proxies : []) || []).map((id: string) => ({ type: 'node', id })),
      ...(group.include_groups || (group.source === 'strategy' ? group.proxies : []) || []).map((id: string) => ({ type: 'strategy', id }))
    ]
    return members.every(member => member.type === 'strategy'
      ? staticGroup(member.id, next)
      : ['DIRECT', 'REJECT'].includes(member.id) || selectedNodes.value.some(node => node.id === member.id && node.enabled !== false))
  }
  return [
    ...selectedNodes.value.filter(node => node.enabled !== false && node.id !== editingNode.value?.id).map(node => ({ value: JSON.stringify({ type: 'node', id: node.id }), label: `节点 · ${node.name}` })),
    ...groups.value.filter(group => staticGroup(group.id)).map(group => ({ value: JSON.stringify({ type: 'group', id: group.id }), label: `策略组 · ${group.name}` }))
  ]
})
const dialerCandidates = computed(() => eligibleCandidates.value.filter(candidate => candidate.label.toLowerCase().includes(dialerSearch.value.toLowerCase())))
const selectedCandidate = computed(() => eligibleCandidates.value.find(candidate => candidate.value === dialerSelection.value))
const dialerUnavailable = computed(() => !!draftDialer.value && !selectedCandidate.value)
function referenceLabel(reference?: DialerRef | null) {
  if (!reference) return '不覆盖（保留原始值）'
  const target = reference.type === 'node' ? nodes.value.find(node => node.id === reference.id) : groups.value.find(group => group.id === reference.id)
  return `${reference.type === 'node' ? '节点' : '策略组'} · ${target?.name || reference.id}`
}
const dialerLabel = computed(() => selectedCandidate.value?.label || `${referenceLabel(draftDialer.value)}${dialerUnavailable.value ? '（不可用）' : ''}`)
async function saveDialer() {
  if (!editingNode.value || dialerSaving.value) return
  dialerSaving.value = true
  const snapshot = { ...dialers.value }
  if (draftDialer.value) snapshot[editingNode.value.id] = { ...draftDialer.value }
  else delete snapshot[editingNode.value.id]
  try {
    await profileApi.saveNodeDialers(profileId, snapshot)
    dialers.value = snapshot
    dialogVisible.value = false
    notify.success('当前配置拨号代理已保存')
  } catch (error: unknown) {
    notify.error(errorMessage(error, '保存拨号代理失败'))
  } finally { dialerSaving.value = false }
}
onBeforeRouteLeave(async () => {
  if (saving.value || dialerSaving.value) { notify.warning('请等待保存完成'); return false }
  if (!dirty.value && !dialogVisible.value) return true
  return confirm('当前配置资源或拨号代理修改尚未保存，确定离开？', { title: '未保存的修改' })
})
onMounted(loadResources)
</script>
