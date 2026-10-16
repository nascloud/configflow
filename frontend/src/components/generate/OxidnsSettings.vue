<template>
  <Dialog :open="open" @update:open="emit('update:open', $event)">
    <DialogContent class="flex max-h-[90dvh] max-w-[calc(100vw-2rem)] flex-col overflow-hidden sm:max-w-[860px]">
      <DialogHeader>
        <DialogTitle>OxiDNS 设置</DialogTitle>
        <DialogDescription>当前配置空间的独立模板。保存后用于配置预览、完整下载与 Agent 发布。</DialogDescription>
      </DialogHeader>

      <LoadingRows v-if="loading" :rows="5" />
      <div v-else-if="loadError" role="alert" class="space-y-3 py-4">
        <p class="text-destructive-accent">{{ loadError }}</p>
        <Button variant="outline" @click="load">重新加载</Button>
      </div>
      <Tabs v-else-if="settings" v-model="tab" class="flex min-h-0 min-w-0 flex-1 flex-col">
        <TabsList class="h-auto w-full shrink-0 flex-wrap justify-start">
          <TabsTrigger v-for="item in tabs" :key="item.value" :value="item.value" class="min-h-11 text-xs">{{ item.label }}</TabsTrigger>
        </TabsList>
        <div class="cf-focus-gutter min-h-0 overflow-y-auto py-2">
          <TabsContent force-mount value="core" class="data-[state=inactive]:hidden space-y-5">
            <InfoNote>
              <p>已验证模板：2 线程、相邻同策略规则合并、300 秒 lazy cache、2048 条独立探测缓存。</p>
              <p>应用模板会填写这些性能参数；上游、规则选择与特色功能继续使用本页设置。</p>
            </InfoNote>
            <Button variant="outline" class="min-h-11" @click="applyTemplate">应用已验证模板</Button>
            <OxidnsFields :settings="settings" @error="setEditorError" :fields="coreFields" />
          </TabsContent>
          <TabsContent force-mount value="rules" class="data-[state=inactive]:hidden space-y-5">
            <InfoNote>
              <p>只有选中的规则参与分流，按「规则管理」中的顺序执行。相邻同策略规则可合并，直连 / 代理交替优先级保持不变。</p>
              <p>域名规则优先匹配；未命中时使用国内探测结果继续判断 IP 规则。</p>
            </InfoNote>
            <div class="grid min-w-0 gap-4 sm:grid-cols-2">
              <FormField v-for="group in ruleGroups" :key="group.key" :label="group.label">
                <MultiSelect v-model="settings[group.key]" :options="ruleOptions(group)" :placeholder="`选择${group.label}`" />
              </FormField>
            </div>
            <p v-if="!rules.length && !ruleSets.length" class="text-sm text-muted-foreground">当前空间尚无规则，可先在「规则管理」中添加。</p>
            <OxidnsFields :settings="settings" @error="setEditorError" :fields="[{ path: 'custom_hosts', label: '自定义 Hosts', type: 'lines', wide: true, placeholder: 'full:example.com 192.0.2.10 2001:db8::10', hint: '每条填写域名规则和一个或多个 IP；支持 IPv4、IPv6。可切换 YAML 批量编辑。' }]" />
          </TabsContent>
          <TabsContent force-mount value="upstreams" class="data-[state=inactive]:hidden "><OxidnsFields :settings="settings" @error="setEditorError" :fields="upstreamFields" /></TabsContent>
          <TabsContent force-mount value="cache" class="data-[state=inactive]:hidden space-y-4">
            <InfoNote>探测缓存仅存储国内探测响应，返回后仍继续 IP 分流。此缓存不使用 lazy cache，避免过期 IP 延迟重新分类。</InfoNote>
            <OxidnsFields :settings="settings" @error="setEditorError" :fields="cacheFields" />
          </TabsContent>
          <TabsContent force-mount value="features" class="data-[state=inactive]:hidden space-y-3">
            <details v-for="feature in featureGroups" :key="feature.key" class="group rounded-lg border border-border bg-card">
              <summary class="flex min-h-12 cursor-pointer items-center justify-between gap-3 px-4 py-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <span class="min-w-0 flex-1">{{ feature.title }}</span><span class="chip shrink-0" :class="settings[feature.key].enabled ? 'chip-ok' : ''">{{ settings[feature.key].enabled ? '已启用' : '未启用' }}</span><ChevronDown class="size-4 shrink-0 text-muted-foreground group-open:rotate-180" aria-hidden="true" />
              </summary>
              <div class="space-y-4 border-t border-border px-4 py-4">
                <p class="text-xs leading-relaxed text-muted-foreground">{{ feature.hint }}</p>
                <div class="flex min-h-11 items-center gap-3">
                  <Switch :id="`oxi-enable-${feature.key}`" v-model="settings[feature.key].enabled" />
                  <Label :for="`oxi-enable-${feature.key}`">启用{{ feature.title }}</Label>
                </div>
                <OxidnsFields v-if="settings[feature.key].enabled" :settings="settings" @error="setEditorError" :fields="feature.fields" />
              </div>
            </details>
          </TabsContent>
          <TabsContent force-mount value="access" class="data-[state=inactive]:hidden space-y-4">
            <InfoNote>WebUI 与 API 使用同一组用户名、密码。预览、下载的运行配置包含这些凭据，请妥善保存。</InfoNote>
            <OxidnsFields :settings="settings" @error="setEditorError" :fields="apiFields" />
          </TabsContent>
          <TabsContent force-mount value="advanced" class="data-[state=inactive]:hidden space-y-4">
            <OxidnsFields :settings="settings" @error="setEditorError" :fields="[{ path: 'custom_match_position', label: '自定义匹配位置', options: [{ value: 'head', label: '在自动规则之前' }, { value: 'tail', label: '在自动规则之后' }] }]" />
            <OxidnsMatchEditor v-model="settings.custom_matches" :disabled="saving" @error="setEditorError('custom_matches', $event ? `自定义匹配：${$event}` : '')" />
            <OxidnsConfigEditor v-model="settings.custom_config" @error="setEditorError('custom_config', $event ? `基础配置扩展：${$event}` : '')" />
          </TabsContent>
        </div>
      </Tabs>
      <p v-if="saveError" role="alert" class="shrink-0 text-sm text-destructive-accent">{{ saveError }}</p>
      <p v-else-if="editorError" role="alert" class="shrink-0 text-sm text-destructive-accent">{{ editorError }} 修正后即可保存，草稿已保留。</p>
      <DialogFooter class="shrink-0">
        <Button variant="outline" :disabled="saving" class="min-h-11" @click="emit('update:open', false)">取消</Button>
        <Button :disabled="loading || saving || !settings || Boolean(loadError) || Boolean(editorError)" class="min-h-11" @click="save"><Loader2 v-if="saving" class="size-4 animate-spin" />保存设置</Button>
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
import FormField from '@/components/common/FormField.vue'
import InfoNote from '@/components/common/InfoNote.vue'
import LoadingRows from '@/components/common/LoadingRows.vue'
import MultiSelect from '@/components/common/MultiSelect.vue'
import { Button } from '@/components/ui/button'
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
const loadError = ref('')
const saveError = ref('')
const editorErrors = reactive<Record<string, string>>({})
const editorError = computed(() => Object.values(editorErrors).find(Boolean) || '')
const setEditorError = (path: string, message: string) => { editorErrors[path] = message; saveError.value = '' }
const tab = ref('core')
let loadVersion = 0
const tabs = [
  { value: 'core', label: '核心模板' }, { value: 'rules', label: '分流规则' },
  { value: 'upstreams', label: 'DNS 上游' }, { value: 'cache', label: '缓存' },
  { value: 'features', label: '特色功能' }, { value: 'access', label: 'WebUI / 日志' }, { value: 'advanced', label: '高级' }
]
const ruleGroups = [
  { key: 'direct_rulesets', opposite: 'proxy_rulesets', label: '直连规则集', sets: true },
  { key: 'proxy_rulesets', opposite: 'direct_rulesets', label: '代理规则集', sets: true },
  { key: 'direct_rules', opposite: 'proxy_rules', label: '直连规则', sets: false },
  { key: 'proxy_rules', opposite: 'direct_rules', label: '代理规则', sets: false }
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
    settings.value = config.data
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
  if (!settings.value || saving.value || editorError.value) return
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
