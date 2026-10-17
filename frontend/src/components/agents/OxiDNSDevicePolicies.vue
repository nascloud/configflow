<template>
  <LoadingRows v-if="loading && !state" :rows="4" />
  <SectionCard v-else-if="!state">
    <EmptyState :icon="Monitor" title="暂时无法读取设备策略" :description="error || '正在读取配置空间。'">
      <Button variant="outline" class="min-h-11" :disabled="loading" @click="load">重试</Button>
    </EmptyState>
  </SectionCard>
  <SectionCard v-else class="[&>header]:items-start max-sm:[&>header>div:first-child]:basis-full" title="设备策略">
    <template #actions>
      <span v-if="dirty" class="text-xs text-warning-accent">未保存</span>
      <Button variant="outline" class="min-h-11" :disabled="saving || publishing || loading" @click="reload">重新载入</Button>
      <Button class="min-h-11" :disabled="!dirty || !state.editable || saving || publishing || loading" @click="save">
        <Loader2 v-if="saving" class="size-4 animate-spin" />保存草稿
      </Button>
    </template>
    <div class="mb-4 space-y-2 text-xs leading-relaxed text-muted-foreground">
      <p class="m-0 break-words">配置：<span class="font-medium text-foreground">{{ state.profile_name }}</span> · 保存草稿，发布到当前设备后生效。</p>
      <p v-if="state.agents.length > 1" class="m-0 break-words text-info-accent">共用草稿：{{ state.agents.map(agent => agent.name).join('、') }}</p>
      <details>
        <summary class="min-h-11 cursor-pointer content-center text-foreground focus-visible:outline-2 focus-visible:outline-primary-accent">使用说明</summary>
        <p class="mb-2 mt-0">智能分流沿用现有规则，未设置的设备保持默认。</p>
        <p class="mb-2 mt-0">直连使用本地 DNS，仅影响解析，不改变代理路由。</p>
        <p class="m-0">设备需使用 OxiDNS，且能识别其真实 IP。</p>
      </details>
    </div>
    <p v-if="!state.editable" role="alert" class="mb-4 rounded-lg bg-warning-soft p-3 text-sm text-warning-accent">{{ state.reason }}</p>
    <p v-if="error" role="alert" class="mb-4 text-sm text-destructive-accent">{{ error }}</p>
    <fieldset :disabled="!state.editable || saving || publishing || loading" class="m-0 min-w-0 border-0 p-0">
      <div class="mb-4 grid min-w-0 gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
        <div class="min-w-0">
          <p class="mb-1.5 mt-0 text-xs text-muted-foreground">从查询记录选择</p>
          <OxiDNSClientSelect :model-value="''" :agent-id="agentId" :recorder="recorder" range="all" :allow-all="false" placeholder="选择已有客户端…" @update:model-value="add" />
          <p v-if="!recorder" class="mb-0 mt-1.5 text-xs text-muted-foreground">暂无可读取的查询记录，可手动添加 IP。</p>
        </div>
        <div class="min-w-0">
          <label for="policy-client-ip" class="mb-1.5 block text-xs text-muted-foreground">手动添加 IP</label>
          <Input id="policy-client-ip" v-model="newIP" class="min-h-11 font-mono" placeholder="192.168.50.51 或 IPv6" @keydown.enter.prevent="add(newIP)" />
        </div>
        <Button variant="outline" class="min-h-11 md:mt-6" :disabled="!newIP.trim() || rows.length >= 512" @click="add(newIP)"><Plus class="size-4" />添加设备</Button>
      </div>
      <div v-if="rows.length" class="cf-table-wrap">
        <table class="cf-table w-full min-w-[540px]">
          <thead><tr><th scope="col">客户端 IP</th><th scope="col">备注</th><th scope="col">策略</th><th scope="col"><span class="sr-only">操作</span></th></tr></thead>
          <tbody>
            <tr v-for="(row, index) in rows" :key="row.ip">
              <td class="font-mono text-xs">{{ row.ip }}</td>
              <td><Input v-model="row.name" :aria-label="row.ip + ' 备注'" maxlength="80" placeholder="设备名称（可选）" class="min-h-11 min-w-32" /></td>
              <td>
                <Select v-model="row.mode"><SelectTrigger :aria-label="row.ip + ' 策略'" class="min-h-11 w-32"><SelectValue /></SelectTrigger>
                  <SelectContent><SelectItem value="smart">智能分流</SelectItem><SelectItem value="direct">直连</SelectItem></SelectContent>
                </Select>
              </td>
              <td><Button variant="ghost" size="icon" class="size-11" :aria-label="'移除 ' + row.ip" @click="rows.splice(index, 1)"><Trash2 class="size-4" /></Button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else :icon="Monitor" title="尚未添加设备策略" description="从已有客户端中选择，或输入 IP。添加后可选择智能分流或直连。" />
    </fieldset>
    <div class="mt-5 flex flex-wrap items-center justify-between gap-3 border-t pt-4">
      <div class="min-w-0 text-xs text-muted-foreground" role="status" aria-live="polite">
        <p class="m-0">{{ task ? deploymentStatusLabel(task) : '草稿尚未在此处发布；运行状态以设备实际配置为准。' }}</p>
        <p v-if="task?.error || task?.message" class="mb-0 mt-1 break-words">{{ task.error || task.message }}</p>
        <p v-if="task?.query_error" class="mb-0 mt-1 text-warning-accent">发布结果暂未确认，正在继续查询。可在 Agent 管理中查看记录。</p>
        <p v-if="task?.status === 'ready'" class="mb-0 mt-1">待激活的是已上传的配置快照，不包含之后保存的草稿修改。</p>
      </div>
      <Button v-if="task?.status === 'ready'" class="min-h-11" :disabled="publishing" @click="activate(agentId)">应用并启动</Button>
      <Button v-else variant="outline" class="min-h-11" :disabled="dirty || !state.editable || saving || loading || publishing || busy(agentId)" @click="publish">
        <Loader2 v-if="publishing || busy(agentId)" class="size-4 animate-spin" /><Upload v-else class="size-4" />发布到当前设备
      </Button>
    </div>
  </SectionCard>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { Loader2, Monitor, Plus, Trash2, Upload } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import SectionCard from '@/components/common/SectionCard.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import OxiDNSClientSelect from './OxiDNSClientSelect.vue'
import { agentApi, oxidnsDevicePolicyApi } from '@/api'
import { confirm, notify } from '@/lib/feedback'
import { createDeploymentId, deploymentStatusLabel, useAgentDeployments } from '@/composables/useAgentDeployments'
import type { OxiDNSDevicePolicies, OxiDNSDevicePolicy } from '@/types/oxidnsMonitor'

const props = defineProps<{ agentId: string; recorder: string }>()
const emit = defineEmits<{ published: [] }>()
const state = ref<OxiDNSDevicePolicies | null>(null)
const rows = ref<OxiDNSDevicePolicy[]>([])
const newIP = ref('')
const loading = ref(false)
const saving = ref(false)
const publishing = ref(false)
const error = ref('')
const dirty = computed(() => !!state.value && JSON.stringify(rows.value) !== JSON.stringify(state.value.policies))
const { deployments, busy, track, activate } = useAgentDeployments(() => emit('published'))
const task = computed(() => deployments[props.agentId])
let epoch = 0
let disposed = false
let controller: AbortController | undefined
const messageOf = (failure: any) => failure?.response?.data?.message || failure?.message || '操作失败，请重试。'

async function canLeave() {
  if (saving.value || publishing.value) {
    notify.info('正在提交，请稍候再切换。')
    return false
  }
  return !dirty.value || await confirm('设备策略尚未保存，离开会丢失本次修改。', { title: '离开设备策略', confirmText: '放弃修改' })
}
defineExpose({ canLeave })

async function load() {
  controller?.abort()
  const current = controller = new AbortController()
  const generation = ++epoch
  loading.value = true
  error.value = ''
  try {
    const { data } = await oxidnsDevicePolicyApi.get(props.agentId, { signal: current.signal })
    if (disposed || generation !== epoch) return
    if (!data?.success) throw new Error(data?.message || '设备策略响应无效。')
    state.value = data
    rows.value = data.policies.map((row: OxiDNSDevicePolicy) => ({ ...row }))
    if (data.deployment) track(props.agentId, data.deployment, false)
  } catch (failure) {
    if (!disposed && generation === epoch) error.value = messageOf(failure)
  } finally {
    if (generation === epoch) loading.value = false
  }
}
async function reload() { if (await canLeave()) await load() }
function add(value: string) {
  if (!state.value?.editable || saving.value || publishing.value) return
  let ip = value.trim()
  if (!ip || rows.value.length >= 512) return
  // The server canonicalizes IPv6 and rejects ambiguous, duplicate or non-host addresses.
  try {
    if (!/^[0-9a-fA-F:.]+$/.test(ip)) throw new Error()
    if (ip.includes(':')) ip = new URL(`http://[${ip}]/`).hostname.slice(1, -1)
    else if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(ip) || ip.split('.').some(part => Number(part) > 255 || (part.length > 1 && part.startsWith('0')))) throw new Error()
  } catch {
    notify.error('请输入有效的客户端 IP，不支持域名或网段。')
    return
  }
  if (rows.value.some(row => row.ip === ip)) { notify.info('此客户端已在列表中。'); return }
  rows.value.push({ ip, name: '', mode: 'smart' })
  newIP.value = ''
}
async function save() {
  if (!state.value || !dirty.value || saving.value) return
  const id = props.agentId
  saving.value = true
  error.value = ''
  try {
    const { data } = await oxidnsDevicePolicyApi.save(id, { profile_id: state.value.profile_id, revision: state.value.revision, policies: rows.value })
    if (disposed || id !== props.agentId) return
    state.value = data
    rows.value = data.policies.map((row: OxiDNSDevicePolicy) => ({ ...row }))
    notify.success('设备策略草稿已保存，发布后生效。')
  } catch (failure) { if (!disposed && id === props.agentId) error.value = messageOf(failure) }
  finally { saving.value = false }
}
async function publish() {
  if (!state.value || dirty.value || publishing.value || busy(props.agentId)) return
  const id = props.agentId
  const expected = { expected_profile_id: state.value.profile_id, expected_oxidns_revision: state.value.revision }
  if (!await confirm(`将发布「${state.value.profile_name}」的完整已保存配置到当前设备，并按现有发布流程校验、备份和应用配置。`, { title: '发布配置', confirmText: '发布' })) return
  if (disposed || id !== props.agentId) return
  publishing.value = true
  error.value = ''
  const deploymentId = createDeploymentId()
  deployments[id] = { deployment_id: deploymentId, status: 'preparing' }
  try {
    const { data } = await agentApi.pushConfig(id, deploymentId, expected)
    if (!disposed) track(id, data)
  } catch (failure: any) {
    if (disposed) return
    if (failure.response?.data?.deployment_id) track(id, failure.response.data)
    else if (!failure.response) track(id, { deployment_id: deploymentId, status: 'unknown' })
    else { delete deployments[id]; error.value = messageOf(failure) }
  } finally { publishing.value = false }
}
function beforeUnload(event: BeforeUnloadEvent) {
  if (dirty.value || saving.value) { event.preventDefault(); event.returnValue = '' }
}
watch(() => props.agentId, () => { state.value = null; rows.value = []; newIP.value = ''; void load() }, { immediate: true })
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onUnmounted(() => { disposed = true; epoch++; controller?.abort(); window.removeEventListener('beforeunload', beforeUnload) })
</script>
