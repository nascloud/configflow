<template>
  <section aria-label="完整 OxiDNS 配置" class="min-w-0 space-y-4">
    <div class="flex min-w-0 flex-wrap items-center justify-between gap-2">
      <div class="flex min-w-0 items-center gap-1">
        <h3 class="text-sm font-semibold">原生配置</h3>
        <FieldHelp label="完整配置" text="直接编辑 OxiDNS 原生配置。表单根据官方文档提供参数选择；未列出的扩展字段也可逐项添加。只有主动添加的参数才会写入配置。切换 YAML 保留字段和值；在表单中修改后会重新整理注释和排版。保存前请通过配置检查确认插件引用及参数有效。" />
      </div>
      <OxidnsEditorModeToggle :model-value="mode" :disabled="disabled" :options="[{ value: 'form', label: '表单' }, { value: 'yaml', label: 'YAML', disabled: mode === 'form' && formDraftError }]" label="完整配置编辑模式" @update:model-value="switchMode" />
    </div>
    <Textarea v-if="mode === 'yaml'" :model-value="draft" :disabled="disabled" :rows="20" aria-label="OxiDNS 完整配置 YAML" :aria-invalid="Boolean(error)" class="min-w-0 font-mono text-xs" placeholder="runtime:\n  worker_threads: 2\nplugins: []" @update:model-value="editYaml(String($event))" />
    <div v-else class="min-w-0 space-y-5">
      <div class="min-w-0 space-y-2">
        <OxidnsSchemaValueEditor :node="globalTree" :schema="oxidnsGlobalSchema" label="全局参数" scope-title="全局参数" :disabled="disabled" @change="commitGlobals">
          <template #header>
          <h4 class="text-sm font-medium">全局参数</h4>
          <FieldHelp label="完整配置全局参数" text="线程、日志、网络出口、API 与 WebUI、include 文件均可按官方字段配置。没有填写的参数使用 OxiDNS 自身默认值；这些参数不会再被简易模板覆盖。" />
          <a href="https://oxidns.org/configuration/global/" target="_blank" rel="noopener noreferrer" class="ml-auto inline-flex min-h-9 items-center gap-1 text-xs text-primary-accent underline-offset-4 hover:underline max-md:min-h-11">官方文档<ExternalLink class="size-3" /></a>
          </template>
        </OxidnsSchemaValueEditor>
      </div>

      <div class="min-w-0 space-y-3">
        <div class="flex min-w-0 flex-wrap items-center gap-1">
          <h4 class="text-sm font-medium">插件 <span class="num text-muted-foreground">{{ plugins?.value.items.length ?? 0 }}</span></h4>
          <FieldHelp label="完整配置插件" text="服务器、执行器、匹配器和数据提供器都定义在 plugins 中。tag 是插件唯一标识，通常通过 $tag 引用。添加插件后请配置参数并接入服务器或执行链。展开条目可以编辑全部原生字段；上移、下移与删除位于同一行。" />
          <a href="https://oxidns.org/plugin-reference/overview/" target="_blank" rel="noopener noreferrer" class="ml-auto inline-flex min-h-9 items-center gap-1 text-xs text-primary-accent underline-offset-4 hover:underline max-md:min-h-11">插件文档<ExternalLink class="size-3" /></a>
        </div>
        <Input v-if="(plugins?.value.items.length ?? 0) > 5" v-model="pluginFilter" :disabled="disabled" aria-label="筛选已有插件" placeholder="搜索插件名称、类型…" class="oxi-native-control font-mono text-xs" />
        <p v-if="nonListPlugins" class="text-xs text-warning-accent">plugins 不是列表，已保留在全局参数中；请修正类型后添加插件。</p>
        <p v-else-if="!plugins?.value.items.length" class="text-xs text-muted-foreground">尚无插件。选择类型添加，或切换 YAML 粘贴完整配置。</p>
        <p v-else-if="!visiblePlugins.length" class="text-xs text-muted-foreground">没有匹配的插件。</p>
        <div v-for="{ plugin, index } in visiblePlugins" :key="plugin.id" class="min-w-0 overflow-hidden rounded-lg border border-border bg-card">
          <div class="flex min-w-0 flex-wrap items-center gap-1 px-2 py-1.5">
            <button type="button" class="flex min-h-9 min-w-0 flex-1 items-center gap-2 rounded-md px-1 text-left outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-primary-accent disabled:opacity-50 max-md:min-h-11" :disabled="disabled" :aria-label="`插件 ${index + 1} 展开设置`" :aria-expanded="opened.has(plugin.id)" @click="toggle(plugin.id)">
              <ChevronDown class="size-3.5 shrink-0 text-muted-foreground" :class="{ '-rotate-90': !opened.has(plugin.id) }" />
              <span class="min-w-0"><span class="block truncate font-mono text-xs font-medium">{{ scalarField(plugin, 'tag') || `插件 ${index + 1}` }}</span><span class="block truncate text-[11px] text-muted-foreground">{{ scalarField(plugin, 'type') || '未指定类型' }}</span></span>
            </button>
            <FieldHelp :label="`插件 ${index + 1} ${scalarField(plugin, 'type')}`" :text="pluginDefinition(plugin)?.help || '自定义插件类型。所有原生字段都会保留，请按对应插件文档设置类型和参数。'" />
            <div class="ml-auto flex shrink-0 items-center gap-0.5">
              <Button type="button" variant="ghost" size="icon" class="oxi-native-icon shrink-0" :disabled="disabled || index === 0" :aria-label="`上移原生插件 ${index + 1}`" @click="movePlugin(index, -1)"><ArrowUp class="size-3.5" /></Button>
              <Button type="button" variant="ghost" size="icon" class="oxi-native-icon shrink-0" :disabled="disabled || index === (plugins?.value.items.length ?? 0) - 1" :aria-label="`下移原生插件 ${index + 1}`" @click="movePlugin(index, 1)"><ArrowDown class="size-3.5" /></Button>
              <Button type="button" variant="ghost" size="icon" class="oxi-native-icon shrink-0 text-muted-foreground hover:text-destructive-accent" :disabled="disabled" :aria-label="`删除原生插件 ${index + 1}`" @click="plugins!.value.items.splice(index, 1); commit()"><Trash2 class="size-3.5" /></Button>
            </div>
          </div>
          <div v-if="opened.has(plugin.id)" class="min-w-0 space-y-2 border-t border-border p-3">
            <a v-if="pluginDefinition(plugin)?.documentation" :href="pluginDefinition(plugin)!.documentation" target="_blank" rel="noopener noreferrer" class="inline-flex min-h-9 items-center gap-1 text-xs text-primary-accent underline-offset-4 hover:underline max-md:min-h-11">{{ pluginDefinition(plugin)!.label }}文档<ExternalLink class="size-3" /></a>
            <OxidnsSchemaValueEditor :node="plugin" :schema="pluginSchema(plugin)" :label="`插件 ${index + 1}`" scope-title="插件字段" layout="plugin" :auto-expand-depth="3" :disabled="disabled" @change="commit" />
          </div>
        </div>

        <div class="flex min-w-0 items-center gap-1 border-t border-border pt-3">
          <OxidnsParameterPicker :fields="pluginChoices" label="原生插件" button-text="添加插件" :disabled="disabled || nonListPlugins" @select="addPlugin" />
          <FieldHelp label="添加原生插件" text="搜索官方插件类型并查看用途，点选即可添加；自定义类型适合更新版本或第三方扩展。添加时只创建 tag、type 和空参数，不会把文档默认值自动写入其他现有配置。" />
        </div>
      </div>
    </div>
    <p v-if="error" role="alert" class="break-words text-sm text-destructive-accent">{{ error }}</p>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, ChevronDown, ExternalLink, Trash2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import FieldHelp from '@/components/common/FieldHelp.vue'
import OxidnsEditorModeToggle from './OxidnsEditorModeToggle.vue'
import OxidnsSchemaValueEditor from './OxidnsSchemaValueEditor.vue'
import OxidnsParameterPicker from './OxidnsParameterPicker.vue'
import { appendConfigEntry, configNodeToValue, defaultConfigNode, parseConfigDocument, serializeConfigDocument, valueToConfigNode, type ConfigValueNode } from '@/lib/oxidnsConfigEditor'
import { oxidnsGlobalSchema, oxidnsPluginCatalog, type OxidnsSchema } from '@/lib/oxidnsSchema'

const props = withDefaults(defineProps<{ modelValue: string; disabled?: boolean }>(), { disabled: false })
const emit = defineEmits<{ (event: 'update:modelValue', value: string): void; (event: 'error', message: string): void }>()
const tree = ref(valueToConfigNode({}))
const globalTree = ref(valueToConfigNode({}))
const draft = ref('')
const mode = ref<'form' | 'yaml'>('form')
const error = ref('')
const formDraftError = ref(false)
const opened = ref(new Set<string>())
const pluginFilter = ref('')
let lastEmitted: string | undefined
const plugins = computed(() => tree.value.entries.find(entry => entry.key === 'plugins' && entry.value.type === 'array'))
const nonListPlugins = computed(() => tree.value.entries.some(entry => entry.key === 'plugins' && entry.value.type !== 'array'))
const scalarField = (node: ConfigValueNode, key: string) => node.entries.find(entry => entry.key === key)?.value.scalar ?? ''
const pluginDefinition = (node: ConfigValueNode) => oxidnsPluginCatalog.find(plugin => plugin.type === scalarField(node, 'type'))
const pluginChoices = [...oxidnsPluginCatalog.map(plugin => ({ key: plugin.type, schema: { type: 'string' as const, label: plugin.label, help: plugin.help } })), { key: '__custom__', schema: { type: 'string' as const, label: '自定义类型', help: '用于更新版本或第三方插件，添加后手动填写类型及参数。' } }]
const visiblePlugins = computed(() => {
  const query = pluginFilter.value.toLowerCase().trim()
  return (plugins.value?.value.items ?? []).map((plugin, index) => ({ plugin, index })).filter(({ plugin }) => `${scalarField(plugin, 'tag')} ${scalarField(plugin, 'type')}`.toLowerCase().includes(query))
})
function pluginSchema(node: ConfigValueNode): OxidnsSchema {
  return { type: 'object', help: 'OxiDNS 插件定义。', properties: {
    tag: { type: 'string', label: '唯一标识', help: '其他插件通过 $tag 引用此插件。标识不得与其他插件重复。' },
    type: { type: 'string', label: '插件类型', help: '选择官方插件类型，或更改值类型后填写自定义类型。更改插件类型会保留已有参数，请按新插件文档调整。', enum: oxidnsPluginCatalog.map(plugin => plugin.type) },
    args: pluginDefinition(node)?.args ?? { type: 'object', label: '参数', help: '按插件定义添加参数。支持对象、列表、文本等全部原生值类型。' },
  } }
}
function report(message = '') { error.value = message; emit('error', message) }
function publish(text: string) { lastEmitted = text; emit('update:modelValue', text) }
function validate() {
  const value = configNodeToValue(tree.value)
  if (!Object.keys(value).length) throw new Error('完整配置不能为空，请初始化模板或填写 OxiDNS 配置。')
  if (value.plugins !== undefined && !Array.isArray(value.plugins)) throw new Error('plugins 必须是插件列表，请修改后保存。')
  const used = new Set<string>()
  for (const [index, plugin] of (value.plugins ?? []).entries()) {
    if (!plugin || typeof plugin !== 'object' || Array.isArray(plugin)) throw new Error(`插件 ${index + 1} 必须是对象。`)
    if (typeof plugin.type !== 'string' || !plugin.type.trim()) throw new Error(`插件 ${index + 1} 需要填写 type。`)
    if (plugin.tag !== undefined && typeof plugin.tag !== 'string') throw new Error(`插件 ${index + 1} 的 tag 必须是文本。`)
    if (plugin.tag) { if (used.has(plugin.tag)) throw new Error(`插件标识「${plugin.tag}」重复，请修改后保存。`); used.add(plugin.tag) }
  }
}
function syncGlobals() { globalTree.value = { ...tree.value, entries: tree.value.entries.filter(entry => entry !== plugins.value) } }
function parseDraft() {
  try { tree.value = parseConfigDocument(draft.value); syncGlobals(); formDraftError.value = false; validate(); report(); return true }
  catch (failure: any) { report(String(failure.message || '请检查 YAML 格式。').replace('基础配置扩展必须是 YAML 对象；可清空文本表示不扩展。', '完整配置必须是 YAML 对象。')); return false }
}
function editYaml(text: string) { draft.value = text; publish(text); parseDraft() }
function commit() {
  try { draft.value = serializeConfigDocument(tree.value); formDraftError.value = false; publish(draft.value) }
  catch (failure: any) { formDraftError.value = true; report(failure.message || '请修正配置内容。'); return }
  try { validate(); report() } catch (failure: any) { report(failure.message || '请修正配置内容。') }
}
function commitGlobals() {
  const plugin = plugins.value
  tree.value.entries = [...globalTree.value.entries.filter(entry => entry !== plugin), ...(plugin ? [plugin] : [])]
  commit()
  syncGlobals()
}
function switchMode(next: 'form' | 'yaml') {
  if (props.disabled || next === mode.value) return
  if (next === 'form') {
    // A syntactically valid document remains editable even while its plugin references need repair.
    try { tree.value = parseConfigDocument(draft.value); syncGlobals() } catch { parseDraft(); return }
  } else if (formDraftError.value) return
  mode.value = next
}
function toggle(id: string) { opened.value.has(id) ? opened.value.delete(id) : opened.value.add(id) }
function addPlugin(selectedType: string) {
  if (props.disabled || nonListPlugins.value) return
  const type = selectedType === '__custom__' ? '' : selectedType
  const definition = oxidnsPluginCatalog.find(plugin => plugin.type === type)
  const target = plugins.value ?? appendConfigEntry(tree.value, 'plugins', [])
  const used = new Set(target.value.items.map(plugin => scalarField(plugin, 'tag')))
  const base = type || 'custom'
  let tag = base
  for (let i = 2; used.has(tag); i++) tag = `${base}_${i}`
  const plugin = valueToConfigNode({ tag, type, args: configNodeToValue(defaultConfigNode(definition?.args.type ?? 'object')) })
  target.value.items.push(plugin)
  opened.value.add(plugin.id)
  pluginFilter.value = ''
  syncGlobals()
  commit()
}
function movePlugin(index: number, offset: number) {
  const items = plugins.value!.value.items
  const [item] = items.splice(index, 1)
  items.splice(index + offset, 0, item)
  commit()
}
watch(() => props.modelValue, text => {
  if (text === lastEmitted) { lastEmitted = undefined; return }
  lastEmitted = undefined
  draft.value = text ?? ''
  opened.value.clear()
  if (!parseDraft()) {
    try { parseConfigDocument(draft.value) } catch { mode.value = 'yaml' }
  }
}, { immediate: true })
</script>
