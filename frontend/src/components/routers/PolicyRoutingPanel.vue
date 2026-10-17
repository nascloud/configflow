<template>
  <SectionCard :title="`策略路由 · ${name}`" class="mt-4 min-w-0" :padded="false"
    help="在这里编辑当前配置空间共用的来源设备、规则集与网关。保存只更新草稿；预览列出 OxiDNS 和 ROS 的变更，确认后按目标依次发布。">
    <template #actions>
      <span v-if="dirty" class="text-xs text-warning-accent">未保存</span>
      <Button variant="outline" class="min-h-11" :disabled="locked" @click="reload">重新载入</Button>
      <Button class="min-h-11" :disabled="locked || !state" @click="saveAndPreview"><Loader2 v-if="saving" class="size-4 animate-spin" />保存并预览</Button>
      <Button variant="ghost" class="min-h-11" :disabled="loading || saving || publishing" @click="close"><ChevronUp class="size-4" />收起面板</Button>
    </template>
    <LoadingRows v-if="loading && !state" :rows="4" />
    <p v-if="error" role="alert" class="m-0 break-words px-5 pb-4 text-sm text-destructive-accent">{{ error }}</p>
    <Tabs v-if="state" v-model="tab" class="min-w-0 gap-0">
      <div class="px-5 pb-4 max-md:px-4">
        <TabsList class="h-auto w-full flex-wrap justify-start sm:w-auto" aria-label="策略路由设置">
          <TabsTrigger v-for="item in tabs" :key="item.value" :value="item.value" class="min-h-11 px-3">{{ item.label }}</TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="sources" class="m-0 px-5 pb-4 max-md:px-4">
        <div class="mb-4 flex items-center gap-2 text-xs text-muted-foreground">
          <span>{{ state.profile_name }} · 共用来源设备</span>
          <FieldHelp label="来源设备" text="智能分流 IPv4 地址或网段参与 ROS 策略路由；更具体的直连例外和 mihomo 自身会被排除。直连设备仍按主表中的静态路由转发。IPv6 策略只作用于 OxiDNS。" />
        </div>
        <p v-if="!state.sources.editable" role="alert" class="rounded-md bg-warning-soft p-3 text-sm text-warning-accent">{{ state.sources.reason }}</p>
        <fieldset class="m-0 min-w-0 space-y-4 border-0 p-0" :disabled="locked || !state.sources.editable">
          <div class="grid min-w-0 items-start gap-4 lg:grid-cols-2">
            <div v-if="state.source_agents.length" class="min-w-0 space-y-2">
              <div class="flex min-h-5 items-center gap-2">
                <span class="text-sm font-medium">从查询记录选择客户端</span>
                <FieldHelp label="已有客户端" text="读取所选 OxiDNS 的查询记录。选择 IP 后加入下方来源列表；也可以手动添加 IP 或网段。" />
              </div>
              <div class="grid min-w-0 gap-2" :class="state.source_agents.length > 1 ? 'sm:grid-cols-2' : ''">
                <Select v-if="state.source_agents.length > 1" v-model="queryAgent" :disabled="locked || !state.sources.editable">
                  <SelectTrigger class="min-h-11 w-full" aria-label="查询记录来源"><SelectValue /></SelectTrigger>
                  <SelectContent><SelectItem v-for="agent in state.source_agents" :key="agent.id" :value="agent.id">{{ agent.name }}</SelectItem></SelectContent>
                </Select>
                <OxiDNSClientSelect :agent-id="queryAgent" :recorder="recorder" model-value="" range="all" :allow-all="false" :placeholder="queryLoading ? '正在读取查询记录…' : '选择已有客户端…'" @update:model-value="addSource" />
              </div>
              <div v-if="queryError" class="flex items-center gap-2 text-xs text-muted-foreground" role="status">
                <span class="min-w-0">{{ queryError }}</span><Button variant="ghost" class="min-h-11 shrink-0 px-2" :disabled="locked || queryLoading" @click="loadQueryRecorder">重试</Button>
              </div>
            </div>
            <div class="min-w-0 space-y-2">
              <Label for="policy-source-ip" class="flex min-h-5 items-center">手动添加 IP / 网段</Label>
              <div class="flex min-w-0 items-center gap-2">
                <Input id="policy-source-ip" v-model="newIP" class="min-h-11 min-w-0 flex-1 font-mono" placeholder="192.168.0.105 或 192.168.0.0/24" @keydown.enter.prevent="addSource(newIP)" />
                <Button variant="outline" class="min-h-11 shrink-0" :disabled="!newIP.trim() || policies.length >= 512" @click="addSource(newIP)"><Plus class="size-4" />添加</Button>
              </div>
            </div>
          </div>
          <div v-if="policies.length" class="cf-table-wrap">
            <table class="cf-table min-w-[540px] w-full"><thead><tr><th>客户端 IP / 网段</th><th>备注</th><th>策略</th><th><span class="sr-only">操作</span></th></tr></thead>
              <tbody><tr v-for="(row, index) in policies" :key="row.ip">
                <td class="font-mono text-xs">{{ row.ip }}</td>
                <td><Input v-model="row.name" :aria-label="`${row.ip} 备注`" maxlength="80" class="min-h-11 min-w-28" /></td>
                <td><Select v-model="row.mode" :disabled="locked || !state.sources.editable"><SelectTrigger class="min-h-11 w-32" :aria-label="`${row.ip} 策略`"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="smart">智能分流</SelectItem><SelectItem value="direct">直连</SelectItem></SelectContent></Select></td>
                <td><Button variant="ghost" size="icon" class="size-11" :aria-label="`移除 ${row.ip}`" @click="policies.splice(index, 1)"><Trash2 class="size-4" /></Button></td>
              </tr></tbody>
            </table>
          </div>
          <EmptyState v-else :icon="Network" title="尚未添加来源设备" description="添加需要智能分流的客户端 IP 或网段，也可为更具体的地址设置直连例外。" />
        </fieldset>
      </TabsContent>
      <TabsContent value="settings" class="m-0 px-5 pb-5 max-md:px-4">
        <fieldset class="m-0 grid min-w-0 gap-5 border-0 p-0 [&_[role=combobox]]:min-h-11" :disabled="locked">
          <div class="space-y-2"><Label for="policy-gateway">代理网关（mihomo）</Label>
            <Select :model-value="settings.proxy_gateway_agent_id || '__none__'" :disabled="locked" @update:model-value="value => settings.proxy_gateway_agent_id = value === '__none__' ? '' : String(value)">
              <SelectTrigger id="policy-gateway" class="min-h-11"><SelectValue placeholder="不关联" /></SelectTrigger>
              <SelectContent><SelectItem value="__none__">不关联</SelectItem><SelectItem v-for="agent in state.gateways" :key="agent.id" :value="agent.id">{{ agent.name }}（{{ agent.host }}）</SelectItem></SelectContent>
            </Select><p class="m-0 text-xs text-muted-foreground">这台 ROS 的策略路由与静态代理路由共用此网关。</p>
          </div>
          <div class="space-y-2"><Label for="policy-destinations">使用策略路由的 IP 规则集</Label><MultiSelect :model-value="settings.ros_policy_rule_sets" :options="ruleOptions" trigger-id="policy-destinations" placeholder="不使用策略路由" @update:model-value="value => selectRules('ros_policy_rule_sets', value)" /></div>
          <div class="space-y-2"><Label for="policy-static-destinations">使用静态路由的 IP 规则集</Label><MultiSelect :model-value="settings.ip_rule_sets" :options="ruleOptions" trigger-id="policy-static-destinations" placeholder="仅 fake-ip 网段" @update:model-value="value => selectRules('ip_rule_sets', value)" /></div>
          <p class="m-0 text-xs text-muted-foreground">同一规则集只能选择一种方式，移入另一组时自动移出原组。fake-ip 始终使用静态路由。取消选择后，需保存并确认发布才会清理对应的受管对象。</p>
        </fieldset>
      </TabsContent>
      <TabsContent value="preview" class="m-0 px-5 pb-5 max-md:px-4">
        <div class="space-y-5">
          <fieldset class="m-0 min-w-0 border-0 p-0" :disabled="locked">
            <legend class="mb-3 flex items-center gap-2 text-sm font-semibold">
              OxiDNS 发布目标
              <FieldHelp label="OxiDNS 发布目标" text="选中的设备将接收当前配置空间的完整已保存配置，包含其他草稿改动。未选中的设备保持现状；全部取消时只发布 ROS。" />
            </legend>
            <div class="flex flex-wrap gap-2">
              <label v-for="agent in state.source_agents" :key="agent.id" class="flex min-h-11 max-w-full cursor-pointer items-center gap-3 rounded-lg border px-3 py-2 text-sm transition-colors" :class="targets.includes(agent.id) ? 'border-primary-accent/30 bg-primary-soft' : 'border-border hover:bg-muted'">
                <input v-model="targets" type="checkbox" :value="agent.id" class="size-4 shrink-0 accent-primary" />
                <span class="min-w-0"><span class="block break-words font-medium">{{ agent.name }}</span><span class="block font-mono text-xs text-muted-foreground">{{ agent.host }}</span></span>
                <span class="ml-1 flex shrink-0 items-center gap-1.5 text-xs" :class="agent.status === 'online' ? 'text-success-accent' : 'text-muted-foreground'"><span class="size-1.5 rounded-full bg-current" />{{ agent.status === 'online' ? '在线' : '离线' }}</span>
              </label>
            </div>
            <p class="mb-0 mt-2 text-xs text-muted-foreground">{{ targets.length ? '发布完整已保存配置，包含其他草稿改动。' : '本次只发布 ROS，OxiDNS 保持现状。' }}</p>
          </fieldset>
          <p v-if="!preview" class="m-0 border-t border-border pt-4 text-sm text-muted-foreground">点击上方“保存并预览”，核对本次变更后确认发布。</p>
          <template v-else>
            <ul v-if="blockers.length" class="m-0 list-none space-y-2 rounded-lg bg-destructive-soft p-3 text-sm text-destructive-accent" role="alert"><li v-for="item in blockers" :key="item">{{ item }}</li></ul>
            <section v-if="changes.length" class="grid gap-2 border-t border-border pt-4 sm:grid-cols-[11rem_minmax(0,1fr)]" aria-label="本次保存的来源变更">
              <h3 class="m-0 text-sm font-semibold">来源变更 <span class="ml-1 text-xs font-normal text-muted-foreground">{{ changes.length }} 项</span></h3>
              <ul class="m-0 max-h-60 list-none space-y-1 overflow-auto p-0 text-sm"><li v-for="(item, index) in changes" :key="index" class="break-words">{{ item }}</li></ul>
            </section>
            <section class="min-w-0 space-y-3" aria-label="路由变更">
              <div class="flex items-center justify-between gap-3">
                <h3 class="m-0 text-sm font-semibold">路由变更 <span class="ml-2 font-normal text-muted-foreground">{{ name }}</span></h3>
                <span class="shrink-0 text-xs text-muted-foreground">{{ routeChangeCount ? `${routeChangeCount} 项变更` : '已同步' }}</span>
              </div>
              <div v-if="preview.router_plan.warnings.length" class="flex items-start gap-2 rounded-lg bg-warning-soft px-3 py-2.5 text-xs text-warning-accent">
                <Info class="mt-0.5 size-4 shrink-0" />
                <ul class="m-0 list-none space-y-1 p-0"><li v-for="item in preview.router_plan.warnings" :key="item" class="break-words">{{ item }}</li></ul>
              </div>
              <ul class="m-0 list-none divide-y divide-border border-y border-border p-0">
                <li v-for="stage in preview.router_plan.stages" :key="stage.key" class="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-4 gap-y-2 py-3 text-sm sm:grid-cols-[11rem_5rem_minmax(0,1fr)]">
                  <div class="flex min-w-0 items-center gap-2"><span class="font-medium">{{ stage.title }}</span><FieldHelp v-if="stage.note" :label="stage.title" :text="stage.note" /></div>
                  <span class="whitespace-nowrap text-xs leading-5" :class="stage.enabled !== false && stage.ops.length ? 'font-medium text-primary-accent' : 'text-muted-foreground'">{{ stage.enabled === false ? '已跳过' : stage.ops.length ? `${stage.ops.length} 项变更` : '无变更' }}</span>
                  <ul v-if="stage.ops.length" class="col-span-2 m-0 min-w-0 list-none space-y-1 p-0 text-sm sm:col-span-1"><li v-for="(op, index) in stage.ops" :key="index" class="min-w-0"><RouterOperationPreview :op="op" /></li></ul>
                  <span v-else class="hidden text-xs text-muted-foreground sm:block">—</span>
                </li>
              </ul>
            </section>
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div class="flex min-w-0 items-center gap-2 text-xs text-muted-foreground"><span>{{ preview.targets.length ? '先发布 OxiDNS，再发布 ROS；失败时暂停。' : '仅发布 ROS，写入后回读校验。' }}</span><FieldHelp label="发布顺序" text="逐台确认 OxiDNS 发布成功后再发布 ROS。失败或结果待确认时停止后续步骤；已成功的设备保留结果，失败设备沿用现有校验与回滚流程。" /></div>
              <Button class="min-h-11 max-sm:w-full" :disabled="!canPublish" @click="publish"><Loader2 v-if="publishing" class="size-4 animate-spin" />确认发布</Button>
            </div>
          </template>
          <section v-if="results.length" class="space-y-3 border-t border-border pt-4" role="status" aria-live="polite" aria-label="发布结果">
            <h3 class="m-0 text-sm font-semibold">发布结果</h3>
            <ul class="m-0 list-none divide-y divide-border p-0">
              <li v-for="row in results" :key="row.id" class="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 py-3 text-sm sm:grid-cols-[11rem_7rem_minmax(0,1fr)]">
                <span class="min-w-0 break-words font-medium">{{ row.name }}</span><span class="text-xs" :class="row.status === 'succeeded' ? 'text-success-accent' : 'text-warning-accent'">{{ resultStatus(row.status) }}</span>
                <p v-if="row.message" class="col-span-2 m-0 break-words text-xs text-muted-foreground sm:col-span-1">{{ row.message }}</p>
              </li>
            </ul>
            <ul v-if="rosResult" class="m-0 space-y-1 pl-5 text-xs"><li v-for="(entry, index) in [...(rosResult.log || []), ...(rosResult.rollback || [])]" :key="index">{{ entry.summary }} · {{ entry.status }}</li></ul>
            <Button v-if="unresolved" variant="outline" class="min-h-11" :disabled="publishing" @click="refreshResults">核对发布结果</Button>
          </section>
        </div>
      </TabsContent>
      <TabsContent value="objects" class="m-0"><PolicyRoutingObjects :id="id" :profile="profile" /></TabsContent>
    </Tabs>
  </SectionCard>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { ChevronUp, Info, Loader2, Network, Plus, Trash2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import SectionCard from '@/components/common/SectionCard.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import FieldHelp from '@/components/common/FieldHelp.vue'
import MultiSelect from '@/components/common/MultiSelect.vue'
import OxiDNSClientSelect from '@/components/agents/OxiDNSClientSelect.vue'
import PolicyRoutingObjects from './PolicyRoutingObjects.vue'
import RouterOperationPreview from './RouterOperationPreview.vue'
import { usePolicyRoutingWorkspace } from '@/composables/usePolicyRoutingWorkspace'
import { deploymentLabels } from '@/composables/useAgentDeployments'
import { normalizeClientRange } from '@/lib/oxidnsClientRange'
import { notify } from '@/lib/feedback'
import { oxidnsMonitorApi } from '@/api'
const props = defineProps<{ id: string; name: string; profile: string }>()
const emit = defineEmits<{ close: []; saved: []; busy: [value: boolean] }>()
const { state, policies, settings, targets, tab, loading, saving, publishing, locked, dirty, error, preview, changes,
  results, rosResult, unresolved, blockers, canPublish, reload, canLeave, selectRules, saveAndPreview, publish, refreshResults } = usePolicyRoutingWorkspace(props, () => emit('saved'))
const tabs = [{ value: 'sources', label: '来源设备' }, { value: 'settings', label: '规则集与网关' }, { value: 'preview', label: '发布预览' }, { value: 'objects', label: '已下发对象' }]
const ruleOptions = computed(() => state.value?.rule_sets.map(rule => ({ value: rule.id, label: `${rule.name}（${rule.policy}）` })) || [])
const newIP = ref(''), queryAgent = ref(''), recorder = ref(''), queryError = ref('')
const queryLoading = ref(false)
const routeChangeCount = computed(() => preview.value?.router_plan.stages.reduce((count, stage) => count + (stage.enabled === false ? 0 : stage.ops.length), 0) || 0)
let queryEpoch = 0
watch(locked, value => emit('busy', value), { immediate: true })
watch(() => state.value?.profile_id, () => { queryAgent.value = state.value?.source_agents[0]?.id || ''; newIP.value = '' })
watch(queryAgent, () => { void loadQueryRecorder() })
async function loadQueryRecorder() {
  const current = ++queryEpoch; recorder.value = ''; queryError.value = ''
  queryLoading.value = false
  if (!queryAgent.value) return
  queryLoading.value = true
  try {
    const { data } = await oxidnsMonitorApi.overview(queryAgent.value)
    if (current !== queryEpoch) return
    recorder.value = data.recorders?.[0] || ''
    if (!recorder.value) queryError.value = '此设备尚无可读取的查询记录，可手动添加 IP 或网段。'
  } catch { if (current === queryEpoch) queryError.value = '暂时无法读取查询记录，可手动添加 IP 或网段。' }
  finally { if (current === queryEpoch) queryLoading.value = false }
}
function addSource(value: string) {
  if (locked.value || !state.value?.sources.editable || policies.value.length >= 512) return
  try {
    const ip = normalizeClientRange(value.trim())
    if (policies.value.some(row => row.ip === ip)) { notify.info('此 IP 或网段已在列表中'); return }
    policies.value.push({ ip, name: '', mode: 'smart' }); newIP.value = ''
  } catch { notify.error('请输入有效的 IP 或 CIDR 网段') }
}
async function close() { if (await canLeave()) emit('close') }
const resultStatus = (status: string) => status === 'succeeded' ? '成功' : deploymentLabels[status] || status
onBeforeUnmount(() => { queryEpoch++; emit('busy', false) })
defineExpose({ canLeave })
</script>
