<template>
  <SectionCard class="mt-4 min-w-0 max-md:[&_[data-slot=button]]:min-h-11" :padded="false" :title="`策略路由 · ${name}`"
    help="读取本配置空间发布记录对应的 ROS 对象。编辑规则集或网关后需重新预览并确认发布；来源设备在同一配置空间的 OxiDNS 设备策略中编辑，并分别发布到 OxiDNS 和 ROS。">
    <template #actions>
      <Button size="sm" variant="outline" :disabled="loading" @click="load"><RefreshCw class="size-3.5" :class="{ 'animate-spin': loading }" />刷新</Button>
      <Button size="sm" variant="outline" @click="$emit('edit')">编辑规则集与网关</Button>
      <Button size="sm" @click="$emit('preview')">预览发布</Button>
      <Button size="sm" variant="ghost" @click="$emit('close')">关闭策略路由</Button>
    </template>
    <div class="space-y-3 px-5 pb-4 text-xs max-md:px-4">
      <p class="m-0 break-words text-muted-foreground">已保存的策略规则集：{{ ruleSets.join('、') || '未选择' }} · 网关：{{ gateway || '未关联' }}</p>
      <p class="m-0 text-muted-foreground">下表为已下发对象；保存的设置与设备可能存在差异，请通过「预览发布」核对并应用。</p>
      <div v-if="data?.source_agents.length" class="flex flex-wrap items-center gap-2">
        <span class="text-muted-foreground">编辑来源设备：</span>
        <Button v-for="agent in data.source_agents" :key="agent.id" as-child size="sm" variant="outline" class="max-w-full">
          <RouterLink :to="{ path: oxidnsMonitorPath(agent.id), query: { tab: 'devices' } }" :title="agent.name"><span class="min-w-0 truncate">{{ agent.name }}</span><ExternalLink class="size-3.5 shrink-0" /></RouterLink>
        </Button>
      </div>
      <p v-else-if="data" class="m-0 text-muted-foreground">当前配置空间尚未绑定 OxiDNS Agent，接入并绑定后可编辑来源设备。</p>
      <p v-if="collectedAt" class="m-0 text-muted-foreground">读取于 {{ collectedAt }}</p>
      <p v-if="error" role="alert" class="m-0 break-words text-destructive-accent">{{ error }}</p>
    </div>
    <LoadingRows v-if="loading" :rows="4" />
    <template v-else-if="data?.objects.length">
      <div class="flex flex-wrap gap-2 border-t border-border px-5 py-3 max-md:px-4" role="group" aria-label="策略对象类型">
        <Button v-for="group in groups" :key="group.key" size="sm" :variant="kind === group.key ? 'secondary' : 'ghost'" :aria-pressed="kind === group.key" @click="kind = group.key">
          {{ group.label }} <span class="num">{{ data.objects.filter(item => item.kind === group.key).length }}</span>
        </Button>
      </div>
      <div class="px-5 pb-3 max-md:px-4"><Input v-model="search" class="min-h-11 max-w-md" aria-label="搜索策略对象" placeholder="搜索名称、地址或动作…" /></div>
      <div v-if="filtered.length" class="cf-table-wrap">
        <table class="cf-table min-w-[720px]">
          <thead><tr><th>名称 / 链</th><th>地址 / 动作</th><th>状态</th><th>实际配置</th></tr></thead>
          <tbody>
            <tr v-for="item in pageRows" :key="item.kind + item.id">
              <td class="font-mono text-xs">{{ item.fields.list || item.fields.name || item.fields.chain || item.fields['routing-table'] || '—' }}</td>
              <td class="font-mono text-xs">{{ item.fields.address || item.fields.action || item.fields['dst-address'] || (item.fields.fib === 'true' || item.fields.fib === true ? 'FIB' : '—') }}</td>
              <td>
                <span :class="['chip', item.status === 'matched' ? 'chip-ok' : 'chip-warn']">{{ states[item.status] }}</span>
                <p v-if="item.fields.disabled === 'true' || item.fields.disabled === true" class="mb-0 mt-1 text-xs text-muted-foreground">已停用</p>
                <p v-if="item.kind === 'routes' && item.status !== 'missing'" class="mb-0 mt-1 text-xs text-muted-foreground">{{ item.fields.active === 'true' || item.fields.active === true ? '路由活跃' : '路由未活跃' }}</p>
              </td>
              <td class="min-w-64 max-w-md whitespace-normal break-words">
                <p v-if="item.fields.gateway" class="m-0 font-mono text-xs">下一跳 {{ item.fields.gateway }}</p>
                <p v-if="item.fields['new-routing-mark'] || item.fields['jump-target']" class="m-0 font-mono text-xs">→ {{ item.fields['new-routing-mark'] || item.fields['jump-target'] }}</p>
                <details>
                  <summary class="min-h-11 cursor-pointer content-center text-xs focus-visible:outline-2 focus-visible:outline-primary-accent">{{ item.status === 'missing' ? '查看上次发布记录' : '查看字段' }} · {{ item.id }}</summary>
                  <dl class="m-0 space-y-1 pb-2 font-mono text-xs"><div v-for="(value, field) in item.fields" :key="field"><dt class="inline text-muted-foreground">{{ field }}: </dt><dd class="m-0 inline">{{ value }}</dd></div></dl>
                </details>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <EmptyState v-else :icon="ListFilter" title="没有匹配的对象" description="此分类下没有记录，或没有符合搜索条件的对象。" />
      <div v-if="filtered.length" class="flex flex-wrap items-center justify-end gap-2 px-5 py-3 text-xs max-md:px-4">
        <span class="num text-muted-foreground">共 {{ filtered.length }} 项 · 第 {{ page }} / {{ pages }} 页</span>
        <Button size="sm" variant="outline" :disabled="page === 1" @click="page--">上一页</Button>
        <Button size="sm" variant="outline" :disabled="page >= pages" @click="page++">下一页</Button>
      </div>
    </template>
    <EmptyState v-else-if="data" :icon="Network" title="尚无已下发的策略对象" description="选择规则集和网关，并配置来源设备后，预览发布到 ROS。这里只展示本配置空间的发布记录，不接管手工同名对象。" />
  </SectionCard>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import { ExternalLink, ListFilter, Network, RefreshCw } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import SectionCard from '@/components/common/SectionCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import { routerApi, type PolicyRoutingObject, type PolicyRoutingView } from '@/api'
import { oxidnsMonitorPath } from '@/composables/useOxiDNSDevices'

const props = defineProps<{ id: string; name: string; profile: string; ruleSets: string[]; gateway: string }>()
defineEmits<{ edit: []; preview: []; close: [] }>()
const data = ref<PolicyRoutingView | null>(null)
const loading = ref(false)
const error = ref('')
const collectedAt = ref('')
const kind = ref<PolicyRoutingObject['kind']>('addresses')
const search = ref('')
const page = ref(1)
const groups = [{ key: 'addresses', label: '地址列表' }, { key: 'mangle', label: 'Mangle' },
  { key: 'tables', label: '路由表' }, { key: 'routes', label: '下一跳' }, { key: 'nat', label: 'NAT 免转换' }] as const
const states = { matched: '字段与发布记录一致', changed: '已被外部修改', missing: '设备上缺失' }
const filtered = computed(() => (data.value?.objects || []).filter(item => item.kind === kind.value
  && Object.values(item.fields).join(' ').toLowerCase().includes(search.value.trim().toLowerCase())))
const pages = computed(() => Math.max(1, Math.ceil(filtered.value.length / 50)))
const pageRows = computed(() => filtered.value.slice((page.value - 1) * 50, page.value * 50))
watch([kind, search], () => { page.value = 1 })
let epoch = 0
async function load() {
  const current = ++epoch
  loading.value = true
  error.value = ''
  data.value = null
  collectedAt.value = ''
  page.value = 1
  try {
    const response = await routerApi.policy(props.id, props.profile)
    if (current !== epoch) return
    if (!response.data.success) throw new Error(response.data.message || '读取策略路由失败')
    data.value = response.data.policy
    collectedAt.value = new Date(response.data.generated_at).toLocaleString()
  } catch (failure: any) {
    if (current === epoch) error.value = failure?.response?.data?.message || failure?.message || '读取策略路由失败，请刷新重试。'
  } finally {
    if (current === epoch) loading.value = false
  }
}
watch(() => [props.id, props.profile], () => { kind.value = 'addresses'; search.value = ''; void load() }, { immediate: true })
onBeforeUnmount(() => { epoch++ })
</script>
