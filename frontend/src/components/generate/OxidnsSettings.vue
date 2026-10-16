<template>
  <Dialog :open="open" @update:open="emit('update:open', $event)">
    <DialogContent class="flex max-h-[90dvh] max-w-[calc(100vw-2rem)] flex-col overflow-hidden sm:max-w-[860px]">
      <DialogHeader>
        <DialogTitle>OxiDNS 设置</DialogTitle>
        <DialogDescription>保存到当前配置空间，用于配置预览、完整下载与 Agent 发布。</DialogDescription>
      </DialogHeader>

      <LoadingRows v-if="loading" :rows="5" />
      <div v-else-if="loadError" role="alert" class="space-y-3 py-4">
        <p class="text-destructive-accent">{{ loadError }}</p>
        <Button variant="outline" @click="load">重新加载</Button>
      </div>
      <div v-else-if="settings" class="flex min-h-0 min-w-0 flex-1 flex-col gap-3">
        <div class="flex flex-wrap items-center gap-2">
          <OxidnsEditorModeToggle :model-value="configMode" :options="[{ value: 'template', label: '快捷模板' }, { value: 'native', label: '完整配置' }]" label="OxiDNS 配置方式" :disabled="saving || rendering" @update:model-value="switchConfigMode" />
          <FieldHelp label="OxiDNS 配置方式" text="快捷模板自动生成分流与缓存流程。完整配置可编辑官方全部全局参数、插件和执行链，表单与 YAML 可切换；首次切换会从当前模板草稿生成配置。两种草稿分别保留，保存时只使用选中模式；完整配置不会随模板选项自动变化。" />
          <span v-if="rendering" class="flex items-center gap-2 text-xs text-muted-foreground"><Loader2 class="size-3.5 animate-spin" />正在生成完整配置…</span>
        </div>
        <Tabs v-model="tab" class="flex min-h-0 min-w-0 flex-1 flex-col">
        <TabsList class="h-auto w-full shrink-0 flex-wrap justify-start">
          <TabsTrigger v-for="item in visibleTabs" :key="item.value" :value="item.value" class="min-h-11 text-xs">{{ item.label }}</TabsTrigger>
        </TabsList>
        <div class="cf-focus-gutter min-h-0 overflow-y-auto py-2">
          <TabsContent force-mount value="native" class="data-[state=inactive]:hidden space-y-4">
            <OxidnsNativeEditor v-if="configMode === 'native' || settings.native_config" v-model="settings.native_config" :disabled="saving" @error="setEditorError('native_config', $event)" />
            <div v-else class="space-y-3 py-4">
              <p class="text-sm text-muted-foreground">从当前模板生成完整配置后，可逐项编辑全部参数。</p>
              <Button :disabled="rendering || saving" variant="outline" @click="initializeNative">从当前模板生成</Button>
            </div>
          </TabsContent>
          <TabsContent force-mount value="files" class="data-[state=inactive]:hidden">
            <OxidnsFilesEditor v-model="settings.additional_files" :disabled="saving" @error="setEditorError('additional_files', $event)" />
          </TabsContent>
          <TabsContent force-mount value="core" class="data-[state=inactive]:hidden space-y-5">
            <div class="flex items-center gap-1">
              <Button variant="outline" class="min-h-11" @click="applyTemplate">应用已验证模板</Button>
              <FieldHelp label="已验证模板" text="填入已测试的性能参数：2 个工作线程、合并相邻同策略规则、300 秒 lazy cache 和 2048 条国内探测缓存，以及对应的上游并发与备用等待参数。上游地址、规则选择和特色功能继续使用当前值；保存后才生效。" />
            </div>
            <OxidnsFields :settings="settings" @error="setEditorError" :fields="coreFields" />
          </TabsContent>
          <TabsContent force-mount value="rules" class="data-[state=inactive]:hidden space-y-5">
            <div class="flex items-center gap-1">
              <h3 class="text-sm font-medium">分流规则</h3>
              <FieldHelp label="分流规则" text="只有选中的规则参与分流，按规则管理中的顺序执行。先匹配域名规则，未命中时通过直连 DNS 探测，再用响应 IP 判断 IP 规则。合并相邻同策略规则不会改变直连与代理之间的优先级。" />
            </div>
            <div class="grid min-w-0 gap-4 sm:grid-cols-2">
              <FormField v-for="group in ruleGroups" :key="group.key" :label="group.label" :help="group.hint">
                <MultiSelect v-model="settings[group.key]" :options="ruleOptions(group)" :placeholder="`选择${group.label}`" />
              </FormField>
            </div>
            <p v-if="!rules.length && !ruleSets.length" class="text-sm text-muted-foreground">当前空间尚无规则，可先在「规则管理」中添加。</p>
            <OxidnsFields :settings="settings" @error="setEditorError" :fields="[{ path: 'custom_hosts', label: '自定义 Hosts', type: 'lines', wide: true, placeholder: 'full:example.com 192.0.2.10 2001:db8::10', hint: '每条填写域名规则和一个或多个 IP；支持 IPv4、IPv6。可切换 YAML 批量编辑。' }]" />
          </TabsContent>
          <TabsContent force-mount value="upstreams" class="data-[state=inactive]:hidden "><OxidnsFields :settings="settings" @error="setEditorError" :fields="upstreamFields" /></TabsContent>
          <TabsContent force-mount value="cache" class="data-[state=inactive]:hidden space-y-4">
            <OxidnsFields :settings="settings" @error="setEditorError" :fields="cacheFields" />
          </TabsContent>
          <TabsContent force-mount value="features" class="data-[state=inactive]:hidden space-y-3">
            <Collapsible v-for="feature in featureGroups" :key="feature.key" class="group rounded-lg border border-border bg-card">
              <div class="relative flex min-h-12 items-center justify-between gap-3 px-4 py-3 text-sm font-medium">
                <CollapsibleTrigger type="button" :aria-label="`${feature.title}设置`" class="absolute inset-0 cursor-pointer rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-primary-accent" />
                <span class="pointer-events-none flex min-w-0 flex-1 items-center gap-1">
                  <span class="min-w-0">{{ feature.title }}</span>
                  <span class="pointer-events-auto relative z-10"><FieldHelp :label="feature.title" :text="feature.hint" /></span>
                </span>
                <span class="chip pointer-events-none shrink-0" :class="settings[feature.key].enabled ? 'chip-ok' : ''">{{ settings[feature.key].enabled ? '已启用' : '未启用' }}</span><ChevronDown class="pointer-events-none size-4 shrink-0 text-muted-foreground group-data-[state=open]:rotate-180" aria-hidden="true" />
              </div>
              <CollapsibleContent force-mount class="data-[state=closed]:hidden space-y-4 border-t border-border px-4 py-4">
                <div class="flex min-h-11 items-center gap-3">
                  <Switch :id="`oxi-enable-${feature.key}`" v-model="settings[feature.key].enabled" />
                  <Label :for="`oxi-enable-${feature.key}`">启用{{ feature.title }}</Label>
                </div>
                <OxidnsFields v-if="settings[feature.key].enabled" :settings="settings" @error="setEditorError" :fields="feature.fields" />
              </CollapsibleContent>
            </Collapsible>
          </TabsContent>
          <TabsContent force-mount value="access" class="data-[state=inactive]:hidden space-y-4">
            <OxidnsFields :settings="settings" @error="setEditorError" :fields="apiFields" />
          </TabsContent>
          <TabsContent force-mount value="advanced" class="data-[state=inactive]:hidden space-y-4">
            <OxidnsFields :settings="settings" @error="setEditorError" :fields="[{ path: 'custom_match_position', label: '自定义匹配位置', hint: '决定自定义匹配与自动域名分流规则的先后顺序。放在前面可优先覆盖自动规则；放在后面则在自动域名规则未结束请求时执行，仍早于动态域名集和 IP 分流判断。', options: [{ value: 'head', label: '在自动规则之前' }, { value: 'tail', label: '在自动规则之后' }] }]" />
            <OxidnsMatchEditor v-model="settings.custom_matches" :disabled="saving" @error="setEditorError('custom_matches', $event ? `自定义匹配：${$event}` : '')" />
            <OxidnsConfigEditor v-model="settings.custom_config" @error="setEditorError('custom_config', $event ? `基础配置扩展：${$event}` : '')" />
          </TabsContent>
        </div>
        </Tabs>
      </div>
      <p v-if="saveError" role="alert" class="shrink-0 text-sm text-destructive-accent">{{ saveError }}</p>
      <p v-else-if="editorError" role="alert" class="shrink-0 text-sm text-destructive-accent">{{ editorError }} 修正后即可保存，草稿已保留。</p>
      <DialogFooter class="shrink-0">
        <Button variant="outline" :disabled="saving" class="min-h-11" @click="emit('update:open', false)">取消</Button>
        <Button :disabled="loading || saving || rendering || !settings || Boolean(loadError) || Boolean(editorError)" class="min-h-11" @click="save"><Loader2 v-if="saving" class="size-4 animate-spin" />保存设置</Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { ChevronDown, Loader2 } from '@lucide/vue'
import { oxidnsApi, ruleApi, ruleSetApi } from '@/api'
import { notify } from '@/lib/feedback'
import { apiFields, applyVerifiedOxidnsTemplate, cacheFields, coreFields, featureGroups, upstreamFields } from '@/lib/oxidns'
import OxidnsFields from './OxidnsFields.vue'
import OxidnsMatchEditor from './OxidnsMatchEditor.vue'
import OxidnsConfigEditor from './OxidnsConfigEditor.vue'
import OxidnsNativeEditor from './OxidnsNativeEditor.vue'
import OxidnsFilesEditor from './OxidnsFilesEditor.vue'
import OxidnsEditorModeToggle from './OxidnsEditorModeToggle.vue'
import FormField from '@/components/common/FormField.vue'
import FieldHelp from '@/components/common/FieldHelp.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import MultiSelect from '@/components/common/MultiSelect.vue'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'

const props = defineProps<{ open: boolean; profileId: string }>()
const emit = defineEmits<{ (event: 'update:open', value: boolean): void; (event: 'saved'): void }>()
const settings = ref<Record<string, any> | null>(null)
const rules = ref<any[]>([])
const ruleSets = ref<any[]>([])
const loading = ref(false)
const saving = ref(false)
const rendering = ref(false)
const loadError = ref('')
const saveError = ref('')
const editorErrors = reactive<Record<string, string>>({})
const configMode = computed<'template' | 'native'>(() => settings.value?.config_mode === 'native' ? 'native' : 'template')
const editorError = computed(() => Object.entries(editorErrors).find(([path, message]) => message && (path === 'additional_files' || (configMode.value === 'native' ? path === 'native_config' : path !== 'native_config')))?.[1] || '')
const setEditorError = (path: string, message: string) => { editorErrors[path] = message; saveError.value = '' }
const tab = ref('core')
let loadVersion = 0
const tabs = [
  { value: 'core', label: '核心模板' }, { value: 'rules', label: '分流规则' },
  { value: 'upstreams', label: 'DNS 上游' }, { value: 'cache', label: '缓存' },
  { value: 'features', label: '特色功能' }, { value: 'access', label: 'WebUI / 日志' }, { value: 'advanced', label: '高级' }
]
const visibleTabs = computed(() => configMode.value === 'native'
  ? [{ value: 'native', label: '参数与插件' }, { value: 'files', label: '附加文件' }]
  : [...tabs, { value: 'files', label: '附加文件' }])
const initializeNative = async () => {
  if (!settings.value || rendering.value) return false
  const version = loadVersion
  rendering.value = true
  saveError.value = ''
  try {
    const response = await oxidnsApi.render({ ...settings.value, config_mode: 'template' }, props.profileId)
    if (version !== loadVersion || !settings.value) return false
    settings.value.native_config = response.data.config
    return true
  } catch (error: any) {
    if (version === loadVersion) saveError.value = error.response?.data?.message || '生成完整配置失败，请检查当前模板。'
    return false
  } finally { rendering.value = false }
}
const switchConfigMode = async (mode: 'template' | 'native') => {
  if (!settings.value || mode === configMode.value || rendering.value) return
  if (mode === 'native' && !settings.value.native_config) {
    if (editorError.value) { saveError.value = '请先修正当前模板的格式错误，再生成完整配置。'; return }
    if (!await initializeNative()) return
  }
  if (!settings.value) return
  settings.value.config_mode = mode
  saveError.value = ''
  tab.value = mode === 'native' ? 'native' : 'core'
}
const ruleGroups = [
  { key: 'direct_rulesets', opposite: 'proxy_rulesets', label: '直连规则集', sets: true, hint: '选择整组域名或 IP 规则，命中后使用直连 DNS。规则集内容会包含在完整下载包中；同一规则集不能同时归入代理分组。' },
  { key: 'proxy_rulesets', opposite: 'direct_rulesets', label: '代理规则集', sets: true, hint: '选择整组域名或 IP 规则，命中后进入代理 DNS 与备用 DNS 流程。同一规则集不能同时归入直连分组。' },
  { key: 'direct_rules', opposite: 'proxy_rules', label: '直连规则', sets: false, hint: '选择规则管理中单独添加的域名或 IP 规则，命中后使用直连 DNS。与规则集一起按原有顺序参与分流。' },
  { key: 'proxy_rules', opposite: 'direct_rules', label: '代理规则', sets: false, hint: '选择规则管理中单独添加的域名或 IP 规则，命中后进入代理 DNS 与备用 DNS 流程。与规则集一起按原有顺序参与分流。' }
]
const ruleOptions = (group: typeof ruleGroups[number]) => (group.sets ? ruleSets.value : rules.value)
  .filter(item => !settings.value?.[group.opposite].includes(item.id) || settings.value?.[group.key].includes(item.id))
  .map(item => ({ value: item.id, label: item.name || `${item.type || item.rule_type || '规则'} · ${item.value || item.rule_value || item.id}` }))

const load = async () => {
  const version = ++loadVersion
  loading.value = true
  loadError.value = ''
  saveError.value = ''
  settings.value = null
  Object.keys(editorErrors).forEach(key => delete editorErrors[key])
  try {
    const [config, availableRules, availableSets] = await Promise.all([
      oxidnsApi.getSettings(props.profileId), ruleApi.getAll(props.profileId), ruleSetApi.getAll(props.profileId)
    ])
    if (version !== loadVersion) return
    settings.value = { config_mode: 'template', native_config: '', additional_files: [], ...config.data }
    if (settings.value!.config_mode === 'native') tab.value = 'native'
    else if (tab.value === 'native') tab.value = 'core'
    rules.value = (availableRules.data || []).filter((item: any) => item.itemType === 'rule')
    ruleSets.value = availableSets.data || []
  } catch (error: any) {
    if (version === loadVersion) loadError.value = error.response?.data?.message || '加载 OxiDNS 设置失败，请重试。'
  } finally {
    if (version === loadVersion) loading.value = false
  }
}
const applyTemplate = () => {
  if (!settings.value) return
  applyVerifiedOxidnsTemplate(settings.value)
  notify.success('已填入验证过的性能参数，保存后生效')
}
const save = async () => {
  if (!settings.value || saving.value || rendering.value || editorError.value) return
  saving.value = true
  saveError.value = ''
  try {
    await oxidnsApi.saveSettings({ ...settings.value }, props.profileId)
    notify.success('OxiDNS 设置已保存')
    emit('saved')
    emit('update:open', false)
  } catch (error: any) {
    saveError.value = error.response?.data?.message || error.message || '保存 OxiDNS 设置失败。'
    notify.error(saveError.value)
  } finally {
    saving.value = false
  }
}
watch(() => [props.open, props.profileId], () => {
  if (props.open) void load()
  else ++loadVersion
}, { immediate: true })
</script>
