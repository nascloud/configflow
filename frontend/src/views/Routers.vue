<template>
  <div>
    <PageHeader title="上游路由">
      <template #actions>
        <Button @click="openCreate">
          <Plus class="size-4" />
          添加路由器
        </Button>
      </template>
    </PageHeader>

    <p class="mt-0 mb-4 text-[12.5px] leading-relaxed text-muted-foreground">
      读取主路由上与旁路网关相关的配置（代理路由、NAT 免转换）和 mihomo 主机的回流规则，与当前配置空间计算出的期望状态对比。
      生成报告不会修改任何设备；「预览发布」列出全部变更，确认后才按「回流规则 → 代理路由 → NAT 免转换」的顺序写入，任一阶段校验失败会逆序回滚。
    </p>

    <LoadingRows v-if="loading && !routers.length" :rows="3" />

    <SectionCard v-else-if="!routers.length" :padded="false">
      <EmptyState
        :icon="RouterIcon"
        title="还没有接入路由器"
        description="接入 iKuai 4.x 或 RouterOS 7.1+，查看主路由配置与期望状态的差异。"
      >
        <Button @click="openCreate">
          <Plus class="size-4" />
          添加路由器
        </Button>
      </EmptyState>
    </SectionCard>

    <SectionCard v-else :padded="false" title="路由器">
      <div class="cf-table-wrap">
        <table class="cf-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>类型</th>
              <th>管理地址</th>
              <th>版本</th>
              <th>状态</th>
              <th class="text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="router in routers" :key="router.id">
              <td class="font-medium">{{ router.name }}</td>
              <td>{{ typeLabel(router.type) }}</td>
              <td class="font-mono text-[12px]">{{ router.endpoint }}</td>
              <td class="font-mono text-[12px]">{{ router.last_probe?.ok ? router.last_probe.version : '—' }}</td>
              <td>
                <StatusDot
                  :tone="!router.last_probe ? 'muted' : router.last_probe.ok ? 'success' : 'danger'"
                  :label="!router.last_probe ? '未探测' : router.last_probe.ok ? '可连接' : '连接失败'"
                />
                <p v-if="router.last_probe && !router.last_probe.ok" class="mt-1 mb-0 max-w-[260px] text-[11.5px] text-destructive-accent">
                  {{ router.last_probe.message }}
                </p>
              </td>
              <td>
                <div class="flex items-center justify-end gap-1">
                  <Button size="sm" variant="outline" :disabled="busyId === router.id" @click="probe(router)">
                    <Loader2 v-if="busyId === router.id && busyAction === 'probe'" class="size-3.5 animate-spin" />
                    <Activity v-else class="size-3.5" />
                    探测
                  </Button>
                  <Button size="sm" :disabled="busyId === router.id" @click="loadReport(router)">
                    <Loader2 v-if="busyId === router.id && busyAction === 'report'" class="size-3.5 animate-spin" />
                    <GitCompare v-else class="size-3.5" />
                    差异报告
                  </Button>
                  <Button variant="ghost" size="icon-sm" title="编辑" aria-label="编辑" @click="openEdit(router)">
                    <Pencil class="size-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    class="text-destructive-accent hover:bg-destructive-soft"
                    title="删除"
                    aria-label="删除"
                    @click="remove(router)"
                  >
                    <Trash2 class="size-4" />
                  </Button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </SectionCard>

    <template v-if="report">
      <SectionCard class="mt-4" :title="`差异报告 · ${reportRouter?.name}`" :description="`生成于 ${reportTime}`">
        <template #actions>
          <span :class="['chip', report.in_sync ? 'chip-ok' : 'chip-warn']">
            {{ report.in_sync ? '已全部同步' : '存在差异' }}
          </span>
          <Button size="sm" :disabled="planLoading" @click="openPlan">
            <Loader2 v-if="planLoading" class="size-3.5 animate-spin" />
            <Rocket v-else class="size-3.5" />
            预览发布
          </Button>
        </template>
        <div class="flex flex-wrap gap-2">
          <span v-for="(count, key) in report.summary" :key="key" :class="['chip', STATUS[key]?.chip || 'chip-acc']">
            {{ STATUS[key]?.label || key }} <span class="num">{{ count }}</span>
          </span>
        </div>
        <dl class="mt-4 mb-0 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[12.5px]">
          <dt class="text-muted-foreground">fake-ip 网段</dt>
          <dd class="m-0 font-mono">{{ report.desired.fake_ip_range }}</dd>
          <dt class="text-muted-foreground">代理网关（下一跳）</dt>
          <dd class="m-0 font-mono">{{ report.desired.proxy_gateway || '未关联' }}</dd>
        </dl>
        <ul v-if="report.desired.warnings.length" class="mt-4 mb-0 flex list-none flex-col gap-1.5 p-0">
          <li
            v-for="warning in report.desired.warnings"
            :key="warning"
            class="flex items-start gap-2 rounded-md bg-warning-soft px-3 py-2 text-[12.5px] text-warning-accent"
          >
            <TriangleAlert class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
            {{ warning }}
          </li>
        </ul>
      </SectionCard>

      <SectionCard class="mt-4" :padded="false" title="代理路由" description="期望的代理网段及其下一跳是否已在主路由上配置。">
        <div class="cf-table-wrap">
          <table class="cf-table">
            <thead>
              <tr><th>网段</th><th>来源</th><th>状态</th><th>说明</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in report.routes.items" :key="item.cidr">
                <td class="font-mono text-[12px]">{{ item.cidr }}</td>
                <td class="text-[12px] text-muted-foreground">{{ item.sources.join('、') }}</td>
                <td><span :class="['chip', STATUS[item.status].chip]">{{ STATUS[item.status].label }}</span></td>
                <td class="text-[12px] text-muted-foreground">{{ item.detail || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </SectionCard>

      <SectionCard class="mt-4" :padded="false" title="NAT 免转换" description="发往代理网段的流量不做源地址转换，mihomo 才能看到真实客户端 IP。">
        <div class="cf-table-wrap">
          <table class="cf-table">
            <thead>
              <tr><th>网段</th><th>状态</th><th>说明</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in report.nat_bypass.items" :key="item.cidr">
                <td class="font-mono text-[12px]">{{ item.cidr }}</td>
                <td><span :class="['chip', STATUS[item.status].chip]">{{ STATUS[item.status].label }}</span></td>
                <td class="text-[12px] text-muted-foreground">{{ item.detail || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="report.nat_bypass.disabled_rules.length" class="m-0 px-5 pb-4 text-[12px] text-muted-foreground max-md:px-4">
          另有 {{ report.nat_bypass.disabled_rules.length }} 条已停用的免转换规则：
          {{ report.nat_bypass.disabled_rules.map(rule => rule.comment || rule.rule_id).join('、') }}
        </p>
      </SectionCard>

      <SectionCard class="mt-4" title="回流规则（旁路网关）" description="mihomo 主机上把回包强制送回主路由的连接标记与策略路由。">
        <template #actions>
          <StatusDot :tone="returnTone" :label="returnLabel" />
        </template>
        <p v-if="report.return_routing.status !== 'ok'" class="m-0 text-[12.5px] text-muted-foreground">
          {{ report.return_routing.message }}
        </p>
        <div v-else class="flex flex-col gap-3 text-[12.5px]">
          <div v-if="!report.return_routing.nft_available || !report.return_routing.iproute_available" class="text-warning-accent">
            旁路网关缺少 {{ [!report.return_routing.nft_available && 'nftables', !report.return_routing.iproute_available && 'iproute2'].filter(Boolean).join('、') }}，无法完整检查。
          </div>
          <div>
            <p class="mt-0 mb-1 text-muted-foreground">连接标记表</p>
            <p v-if="!report.return_routing.return_tables?.length" class="m-0">未发现</p>
            <p v-for="table in report.return_routing.return_tables" :key="table.name" class="m-0 font-mono">
              {{ table.family }} {{ table.name }} · {{ table.marks.join(', ') }}
            </p>
          </div>
          <div>
            <p class="mt-0 mb-1 text-muted-foreground">按标记的策略路由</p>
            <p v-if="!report.return_routing.fwmark_rules?.length" class="m-0">未发现</p>
            <div v-for="rule in report.return_routing.fwmark_rules" :key="rule.priority" class="font-mono">
              <p class="m-0">优先级 {{ rule.priority }} · fwmark {{ rule.mark }} → 表 {{ rule.table }}</p>
              <p v-for="line in report.return_routing.table_routes?.[rule.table] || []" :key="line" class="m-0 pl-4 text-muted-foreground">
                {{ line }}
              </p>
            </div>
          </div>
          <p v-if="report.return_routing.tun_devices?.length" class="m-0 text-muted-foreground">
            TUN 网卡：<span class="font-mono">{{ report.return_routing.tun_devices.join(', ') }}</span>
          </p>
        </div>
      </SectionCard>

      <SectionCard
        v-if="report.routes.unmanaged_other.length || report.routes.extra.length"
        class="mt-4"
        :padded="false"
        title="其他路由"
        description="指向代理网关但不由期望状态推导的手工路由不会被修改；多余项为 ConfigFlow 标记但已不再需要的路由。"
      >
        <div class="cf-table-wrap">
          <table class="cf-table">
            <thead>
              <tr><th>网段</th><th>下一跳</th><th>类别</th><th>备注</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in report.routes.unmanaged_other" :key="'u' + item.cidr">
                <td class="font-mono text-[12px]">{{ item.cidr }}</td>
                <td class="font-mono text-[12px]">{{ item.gateway }}</td>
                <td><span class="chip chip-acc">手工路由{{ item.enabled ? '' : '（已停用）' }}</span></td>
                <td class="text-[12px] text-muted-foreground">{{ item.comment || '—' }}</td>
              </tr>
              <tr v-for="item in report.routes.extra" :key="'e' + item.cidr">
                <td class="font-mono text-[12px]">{{ item.cidr }}</td>
                <td class="font-mono text-[12px]">{{ item.gateway }}</td>
                <td><span class="chip chip-warn">多余</span></td>
                <td class="text-[12px] text-muted-foreground">{{ item.comment || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </SectionCard>
    </template>

    <Dialog v-model:open="planVisible">
      <DialogContent class="max-w-[640px]">
        <DialogHeader>
          <DialogTitle>发布到 {{ reportRouter?.name }}</DialogTitle>
          <DialogDescription>
            以下是本次发布会做的全部变更，按顺序执行；每个阶段完成后回读校验，失败时按相反顺序撤销已完成的步骤。
          </DialogDescription>
        </DialogHeader>

        <div v-if="plan" class="flex max-h-[60vh] flex-col gap-4 overflow-y-auto text-[12.5px]">
          <ul v-if="plan.blockers.length" class="m-0 flex list-none flex-col gap-1.5 p-0">
            <li v-for="item in plan.blockers" :key="item" class="flex items-start gap-2 rounded-md bg-destructive-soft px-3 py-2 text-destructive-accent">
              <CircleX class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              {{ item }}
            </li>
          </ul>
          <ul v-if="plan.warnings.length" class="m-0 flex list-none flex-col gap-1.5 p-0">
            <li v-for="item in plan.warnings" :key="item" class="flex items-start gap-2 rounded-md bg-warning-soft px-3 py-2 text-warning-accent">
              <TriangleAlert class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              {{ item }}
            </li>
          </ul>
          <p v-if="!plan.blockers.length && plan.empty" class="m-0 text-muted-foreground">已全部同步，无需发布。</p>

          <ol v-if="!plan.empty" class="m-0 flex list-none flex-col gap-3 p-0">
            <li v-for="(stage, index) in plan.stages" :key="stage.key" class="rounded-md border border-border px-3 py-2.5">
              <div class="flex items-center gap-2">
                <span class="num text-muted-foreground">{{ index + 1 }}</span>
                <span class="font-semibold">{{ stage.title }}</span>
                <span v-if="!stage.ops.length" class="chip chip-ok">无变更</span>
                <span v-else-if="stage.manual" class="chip chip-sky">需手动导入</span>
                <span v-else class="chip chip-acc">{{ stage.ops.length }} 项</span>
              </div>
              <ul v-if="stage.ops.length" class="mt-2 mb-0 flex list-disc flex-col gap-1 pl-8 text-muted-foreground">
                <li v-for="op in stage.ops" :key="op.summary">{{ op.summary }}</li>
              </ul>
            </li>
          </ol>

          <div v-if="plan.script" class="flex flex-col gap-1.5">
            <div class="flex items-center justify-between">
              <span class="text-muted-foreground">RouterOS 脚本（回流规则由 ConfigFlow 下发，路由器部分请在终端检查后导入）</span>
              <Button size="sm" variant="outline" @click="copyScript">
                <Copy class="size-3.5" />
                复制
              </Button>
            </div>
            <pre class="m-0 max-h-[220px] overflow-auto rounded-md bg-muted px-3 py-2 font-mono text-[11.5px] leading-relaxed">{{ plan.script }}</pre>
          </div>

          <div v-if="releaseResult" class="flex flex-col gap-1.5">
            <p class="m-0 font-semibold">{{ releaseResult.ok ? '发布完成' : '发布失败，已回滚' }}</p>
            <ul class="m-0 flex list-none flex-col gap-1 p-0">
              <li v-for="(entry, index) in [...releaseResult.log, ...releaseResult.rollback]" :key="index" class="flex items-start gap-2">
                <span :class="['chip shrink-0', LOG_STATUS[entry.status]?.chip || 'chip-acc']">{{ LOG_STATUS[entry.status]?.label || entry.status }}</span>
                <span>{{ stageTitle(entry.stage) }} · {{ entry.summary }}</span>
              </li>
            </ul>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" @click="planVisible = false">{{ releaseResult ? '关闭' : '取消' }}</Button>
          <Button v-if="!releaseResult" :disabled="!canApply || applying" @click="applyPlan">
            <Loader2 v-if="applying" class="size-4 animate-spin" />
            确认发布
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>

    <Dialog v-model:open="dialogVisible">
      <DialogContent class="max-w-[480px]">
        <DialogHeader>
          <DialogTitle>{{ editingId ? '编辑路由器' : '添加路由器' }}</DialogTitle>
          <DialogDescription>
            ConfigFlow 通过路由器的管理接口读取配置，iKuai 发布时也通过它写入（需要管理员账号）。RouterOS 支持 HTTPS（www-ssl）和 HTTP（www，需 7.9+），用户需有 rest-api、read 权限，变更以脚本形式交给你导入。
          </DialogDescription>
        </DialogHeader>

        <form class="flex flex-col gap-4" @submit.prevent="submit">
          <div class="flex flex-col gap-1.5">
            <Label for="router-name">名称</Label>
            <Input id="router-name" v-model="form.name" class="bg-background/50" maxlength="80" placeholder="例如 主路由" />
          </div>
          <div class="flex flex-col gap-1.5">
            <Label for="router-type">类型</Label>
            <Select v-model="form.type">
              <SelectTrigger id="router-type" class="w-full bg-background/50"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="ikuai">iKuai 4.x</SelectItem>
                <SelectItem value="routeros">RouterOS 7.1+</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div class="flex flex-col gap-1.5">
            <Label for="router-endpoint">管理地址</Label>
            <Input
              id="router-endpoint"
              v-model="form.endpoint"
              class="bg-background/50 font-mono"
              :placeholder="form.type === 'routeros' ? 'https://192.168.88.1' : 'http://192.168.1.1'"
            />
          </div>
          <div class="grid grid-cols-2 gap-3 max-md:grid-cols-1">
            <div class="flex flex-col gap-1.5">
              <Label for="router-username">用户名</Label>
              <Input id="router-username" v-model="form.username" class="bg-background/50" autocomplete="off" />
            </div>
            <div class="flex flex-col gap-1.5">
              <Label for="router-password">密码</Label>
              <Input
                id="router-password"
                v-model="form.password"
                type="password"
                class="bg-background/50"
                autocomplete="new-password"
                :placeholder="editingId && editingHasPassword ? '留空则保持不变' : ''"
              />
            </div>
          </div>
          <label class="flex items-center gap-2 text-[13px]">
            <Switch :model-value="form.verify_tls" @update:model-value="value => (form.verify_tls = Boolean(value))" />
            校验 HTTPS 证书
          </label>
          <div class="flex flex-col gap-1.5">
            <Label for="router-gateway">代理网关（mihomo Agent）</Label>
            <Select v-model="form.proxy_gateway_agent_id">
              <SelectTrigger id="router-gateway" class="w-full bg-background/50"><SelectValue placeholder="不关联" /></SelectTrigger>
              <SelectContent>
                <SelectItem :value="NONE">不关联</SelectItem>
                <SelectItem v-for="agent in agentsOf('mihomo')" :key="agent.id" :value="agent.id">
                  {{ agent.name }}（{{ agent.host }}）
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div class="flex flex-col gap-1.5">
            <Label>按 IP 路由给 mihomo 的规则集</Label>
            <MultiSelect v-model="form.ip_rule_sets" :options="ruleSetOptions" placeholder="只路由 fake-ip 网段" />
            <p class="m-0 text-[11.5px] leading-relaxed text-muted-foreground">
              fake-ip 网段始终包含。只勾选客户端直接连 IP、不经过 DNS 的规则集（如 Telegram）；其余流量经 OxiDNS 解析成 fake-ip 后自然进入 mihomo。
            </p>
          </div>
          <div class="flex flex-col gap-1.5">
            <Label for="router-dns">DNS 服务器（OxiDNS Agent）</Label>
            <Select v-model="form.dns_agent_id">
              <SelectTrigger id="router-dns" class="w-full bg-background/50"><SelectValue placeholder="不关联" /></SelectTrigger>
              <SelectContent>
                <SelectItem :value="NONE">不关联</SelectItem>
                <SelectItem v-for="agent in agentsOf('oxidns')" :key="agent.id" :value="agent.id">
                  {{ agent.name }}（{{ agent.host }}）
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
        </form>

        <DialogFooter>
          <Button variant="outline" @click="dialogVisible = false">取消</Button>
          <Button :disabled="saving" @click="submit">
            <Loader2 v-if="saving" class="size-4 animate-spin" />
            保存
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import {
  Activity,
  CircleX,
  Copy,
  GitCompare,
  Loader2,
  Pencil,
  Plus,
  Rocket,
  Router as RouterIcon,
  Trash2,
  TriangleAlert
} from '@lucide/vue'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import EmptyState from '@/components/common/EmptyState.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import SectionCard from '@/components/common/SectionCard.vue'
import StatusDot from '@/components/common/StatusDot.vue'
import MultiSelect from '@/components/common/MultiSelect.vue'
import { agentApi, routerApi, ruleApi } from '@/api'
import { confirmDanger, notify } from '@/lib/feedback'

type RouterType = 'ikuai' | 'routeros'
type Status = 'synced' | 'adoptable' | 'conflict' | 'missing' | 'extra'

interface RouterItem {
  id: string
  name: string
  type: RouterType
  endpoint: string
  username: string
  verify_tls: boolean
  proxy_gateway_agent_id: string
  dns_agent_id: string
  ip_rule_sets?: string[]
  has_password: boolean
  last_probe?: { ok: boolean; version?: string; message?: string; at: string }
}

interface AgentItem {
  id: string
  name: string
  host: string
  service_type: string
}

const NONE = '__none__'
const STATUS: Record<string, { label: string; chip: string }> = {
  synced: { label: '已同步', chip: 'chip-ok' },
  adoptable: { label: '可接管', chip: 'chip-sky' },
  conflict: { label: '冲突', chip: 'chip-bad' },
  missing: { label: '缺失', chip: 'chip-warn' },
  extra: { label: '多余', chip: 'chip-warn' }
}
const LOG_STATUS: Record<string, { label: string; chip: string }> = {
  ok: { label: '完成', chip: 'chip-ok' },
  verified: { label: '已校验', chip: 'chip-ok' },
  manual: { label: '待导入', chip: 'chip-sky' },
  failed: { label: '失败', chip: 'chip-bad' }
}

interface PlanStage {
  key: string
  title: string
  manual: boolean
  ops: { kind: string; summary: string }[]
}
interface Plan {
  stages: PlanStage[]
  blockers: string[]
  warnings: string[]
  script: string
  fingerprint: string
  empty: boolean
}
interface ReleaseResult {
  ok: boolean
  log: { stage: string; summary: string; status: string }[]
  rollback: { stage: string; summary: string; status: string }[]
}

const routers = ref<RouterItem[]>([])
const agents = ref<AgentItem[]>([])
const ruleSets = ref<{ id: string; name: string; policy: string }[]>([])
const loading = ref(false)
const saving = ref(false)
const dialogVisible = ref(false)
const editingId = ref('')
const editingHasPassword = ref(false)
const busyId = ref('')
const busyAction = ref<'probe' | 'report' | ''>('')
const report = ref<any>(null)
const reportRouter = ref<RouterItem | null>(null)
const reportTime = ref('')
const plan = ref<Plan | null>(null)
const planVisible = ref(false)
const planLoading = ref(false)
const applying = ref(false)
const releaseResult = ref<ReleaseResult | null>(null)

const emptyForm = () => ({
  name: '',
  type: 'ikuai' as RouterType,
  endpoint: '',
  username: '',
  password: '',
  verify_tls: true,
  proxy_gateway_agent_id: NONE,
  dns_agent_id: NONE,
  ip_rule_sets: [] as string[]
})
const form = reactive(emptyForm())

const typeLabel = (type: RouterType) => (type === 'routeros' ? 'RouterOS' : 'iKuai')
const ruleSetOptions = computed(() =>
  ruleSets.value.map(rule => ({ value: rule.id, label: `${rule.name}（${rule.policy}）` }))
)
const agentsOf = (serviceType: string) => agents.value.filter(agent => agent.service_type === serviceType)
const errorMessage = (error: any, fallback: string) => error?.response?.data?.message || fallback

const returnTone = computed(() => {
  const state = report.value?.return_routing
  if (!state || state.status !== 'ok') return 'muted'
  return state.return_tables?.length && state.fwmark_rules?.length ? 'success' : 'warning'
})
const returnLabel = computed(() => {
  const state = report.value?.return_routing
  if (!state) return ''
  if (state.status === 'unlinked') return '未关联'
  if (state.status === 'offline') return 'Agent 离线'
  if (state.status !== 'ok') return '无法读取'
  return returnTone.value === 'success' ? '已配置' : '未配置'
})

const load = async () => {
  loading.value = true
  try {
    const [routerResponse, agentResponse, ruleResponse] = await Promise.all([
      routerApi.list(),
      agentApi.getAll(),
      ruleApi.getAll()
    ])
    routers.value = routerResponse.data.routers || []
    const agentData = agentResponse.data
    agents.value = Array.isArray(agentData) ? agentData : agentData?.data || agentData?.agents || []
    const ruleData = ruleResponse.data
    const rules = Array.isArray(ruleData) ? ruleData : ruleData?.data || []
    ruleSets.value = rules
      .filter((rule: any) => rule.itemType === 'ruleset')
      .map((rule: any) => ({ id: rule.id, name: rule.name || rule.id, policy: rule.policy || '' }))
  } catch (error) {
    notify.error(errorMessage(error, '加载路由器失败'))
  } finally {
    loading.value = false
  }
}

const openCreate = () => {
  Object.assign(form, emptyForm())
  editingId.value = ''
  editingHasPassword.value = false
  dialogVisible.value = true
}

const openEdit = (router: RouterItem) => {
  Object.assign(form, {
    name: router.name,
    type: router.type,
    endpoint: router.endpoint,
    username: router.username,
    password: '',
    verify_tls: router.verify_tls,
    proxy_gateway_agent_id: router.proxy_gateway_agent_id || NONE,
    dns_agent_id: router.dns_agent_id || NONE,
    ip_rule_sets: [...(router.ip_rule_sets || [])]
  })
  editingId.value = router.id
  editingHasPassword.value = router.has_password
  dialogVisible.value = true
}

const submit = async () => {
  if (saving.value) return
  saving.value = true
  const payload = {
    ...form,
    proxy_gateway_agent_id: form.proxy_gateway_agent_id === NONE ? '' : form.proxy_gateway_agent_id,
    dns_agent_id: form.dns_agent_id === NONE ? '' : form.dns_agent_id
  }
  try {
    if (editingId.value) await routerApi.update(editingId.value, payload)
    else await routerApi.create(payload)
    notify.success(editingId.value ? '路由器已更新' : '路由器已添加')
    dialogVisible.value = false
    if (reportRouter.value?.id === editingId.value) report.value = null
    await load()
  } catch (error) {
    notify.error(errorMessage(error, '保存失败'))
  } finally {
    saving.value = false
  }
}

const remove = async (router: RouterItem) => {
  if (!(await confirmDanger(`删除路由器「${router.name}」？只删除 ConfigFlow 中的接入记录，不会改动路由器。`))) return
  try {
    await routerApi.remove(router.id)
    if (reportRouter.value?.id === router.id) report.value = null
    notify.success('已删除')
    await load()
  } catch (error) {
    notify.error(errorMessage(error, '删除失败'))
  }
}

const probe = async (router: RouterItem) => {
  busyId.value = router.id
  busyAction.value = 'probe'
  try {
    const { data } = await routerApi.probe(router.id)
    notify.success(`连接成功：${typeLabel(router.type)} ${data.probe.version}`)
  } catch (error) {
    notify.error(errorMessage(error, '探测失败'))
  } finally {
    busyId.value = ''
    busyAction.value = ''
    await load()
  }
}

const loadReport = async (router: RouterItem) => {
  busyId.value = router.id
  busyAction.value = 'report'
  try {
    const { data } = await routerApi.report(router.id)
    report.value = data.report
    reportRouter.value = router
    reportTime.value = new Date(data.generated_at).toLocaleString()
  } catch (error) {
    notify.error(errorMessage(error, '生成差异报告失败'))
  } finally {
    busyId.value = ''
    busyAction.value = ''
  }
}

const canApply = computed(() => Boolean(plan.value && !plan.value.blockers.length && !plan.value.empty))
const stageTitle = (key: string) => plan.value?.stages.find(stage => stage.key === key)?.title || key

const openPlan = async () => {
  const router = reportRouter.value
  if (!router) return
  planLoading.value = true
  try {
    const { data } = await routerApi.plan(router.id)
    plan.value = data.plan
    releaseResult.value = null
    planVisible.value = true
  } catch (error) {
    notify.error(errorMessage(error, '生成发布计划失败'))
  } finally {
    planLoading.value = false
  }
}

const applyPlan = async () => {
  const router = reportRouter.value
  if (!router || !plan.value || applying.value) return
  applying.value = true
  try {
    const { data } = await routerApi.apply(router.id, plan.value.fingerprint)
    releaseResult.value = data.result
    notify.success('发布完成')
  } catch (error: any) {
    const data = error?.response?.data
    if (data?.result) releaseResult.value = data.result
    else if (data?.plan) plan.value = data.plan    // changed since preview: show the fresh plan
    notify.error(errorMessage(error, '发布失败'))
  } finally {
    applying.value = false
    await loadReport(router)
  }
}

const copyScript = async () => {
  try {
    await navigator.clipboard.writeText(plan.value?.script || '')
    notify.success('已复制')
  } catch {
    notify.error('复制失败，请手动选择脚本')
  }
}

onMounted(load)
</script>
