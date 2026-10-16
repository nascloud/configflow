<template>
  <div class="oxi-schema-value min-w-0 space-y-2" :data-value-type="node.type">
    <div v-if="node.type !== 'object' && node.type !== 'array'" class="flex min-w-0 items-start gap-2">
      <Input v-if="node.type === 'string' && sensitive" type="password" autocomplete="new-password" :model-value="node.scalar" :disabled="disabled" :aria-label="`${label} 值`" class="oxi-native-control min-w-0 flex-1 font-mono text-xs" @update:model-value="node.scalar = String($event); emit('change')" />
      <Select v-else-if="schema?.enum?.length && !showType && node.type === schema.type" :model-value="node.scalar" :disabled="disabled" @update:model-value="node.scalar = String($event); emit('change')">
        <SelectTrigger class="oxi-native-control min-w-0 flex-1 font-mono text-xs" :aria-label="`${label} 值`"><SelectValue /></SelectTrigger>
        <SelectContent>
          <SelectItem v-for="option in enumValues" :key="String(option)" :value="String(option)">{{ option }}</SelectItem>
        </SelectContent>
      </Select>
      <Textarea v-else-if="node.type === 'string'" :model-value="node.scalar" :rows="node.scalar.includes('\n') ? 3 : 1" :disabled="disabled" :aria-label="`${label} 值`" class="oxi-native-control min-w-0 flex-1 font-mono text-xs" @update:model-value="node.scalar = String($event); emit('change')" />
      <Input v-else-if="node.type === 'number'" :model-value="node.scalar" :disabled="disabled" inputmode="decimal" :aria-label="`${label} 值`" class="oxi-native-control min-w-0 flex-1 font-mono text-xs" @update:model-value="node.scalar = String($event); emit('change')" />
      <Select v-else-if="node.type === 'boolean'" :model-value="node.scalar" :disabled="disabled" @update:model-value="node.scalar = String($event); emit('change')">
        <SelectTrigger class="oxi-native-control min-w-0 flex-1" :aria-label="`${label} 值`"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value="true">true · 是</SelectItem><SelectItem value="false">false · 否</SelectItem></SelectContent>
      </Select>
      <span v-else class="flex min-h-9 flex-1 items-center font-mono text-xs text-muted-foreground">null</span>
      <Button type="button" variant="ghost" size="icon" class="oxi-native-icon shrink-0 text-muted-foreground" :disabled="disabled" :aria-label="`${label} 更改值类型`" :aria-expanded="showType" @click="showType = !showType"><Braces class="size-3.5" /></Button>
    </div>
    <div v-if="showType || !schema || node.type !== schema.type" class="flex flex-wrap items-center gap-2">
      <Select :model-value="node.type" :disabled="disabled" @update:model-value="changeType(String($event) as ConfigValueType)">
        <SelectTrigger class="oxi-native-control w-28" :aria-label="`${label} 类型`"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem v-for="option in configValueTypes" :key="option.value" :value="option.value">{{ option.label }}</SelectItem></SelectContent>
      </Select>
      <FieldHelp :label="`${label} 值类型`" text="按原生配置选择文本、数字、布尔值、列表或对象。环境变量引用使用文本类型。更改类型会清空该字段当前值；只切换表单与 YAML 不会改变值。" />
    </div>

    <div v-if="node.type === 'object'" class="grid min-w-0 gap-3" :class="{ 'md:grid-cols-2': layout === 'plugin' }">
      <div v-for="(entry, index) in node.entries" :key="entry.id" class="min-w-0" :class="entryClasses(entry.key, entry.value)">
        <div class="flex min-w-0 items-center gap-1.5">
          <button v-if="complex(entry.value) && !inlineEntry(entry.key)" type="button" class="oxi-native-icon inline-flex shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-primary-accent disabled:opacity-50" :disabled="disabled" :aria-label="`${label}.${entry.key} 展开参数`" :aria-expanded="isOpen(entry.id)" @click="toggle(entry.id)"><ChevronDown class="size-3.5" :class="{ '-rotate-90': !isOpen(entry.id) }" /></button>
          <div v-if="knownField(entry.key)" class="min-w-0 flex-1">
            <span class="block text-xs font-medium">{{ knownField(entry.key)?.label || entry.key }}</span>
            <code v-if="knownField(entry.key)?.label && knownField(entry.key)?.label !== entry.key" class="block truncate text-[11px] text-muted-foreground" :title="entry.key">{{ entry.key }}</code>
          </div>
          <Input v-else :model-value="entry.key" :disabled="disabled" :aria-label="`${label} 字段名 ${index + 1}`" placeholder="字段名" class="oxi-native-control min-w-0 flex-1 font-mono text-xs" @update:model-value="entry.key = String($event); emit('change')" />
          <FieldHelp :label="`${label}.${entry.key}`" :text="fieldHelp(entry.key)" />
          <Button type="button" variant="ghost" size="icon" class="oxi-native-icon shrink-0 text-muted-foreground hover:text-destructive-accent" :disabled="disabled" :aria-label="`${label} 删除字段 ${entry.key}`" @click="node.entries.splice(index, 1); emit('change')"><Trash2 class="size-3.5" /></Button>
        </div>
        <OxidnsSchemaValueEditor v-if="!complex(entry.value) || inlineEntry(entry.key) || isOpen(entry.id)" :node="entry.value" :schema="fieldSchema(entry.key)" :label="`${label}.${entry.key}`" :depth="depth + 1" :auto-expand-depth="autoExpandDepth" :disabled="disabled" class="mt-1" @change="emit('change')" />
        <div v-else class="ml-10 text-[11px] text-muted-foreground num">{{ countSummary(entry.value) }}</div>
      </div>
      <div class="flex min-w-0 flex-wrap items-center gap-2" :class="{ 'md:col-span-2': layout === 'plugin' }">
        <OxidnsParameterPicker v-if="missingFields.length" :fields="missingFields" :label="label" :disabled="disabled" @select="addField" />
        <Button type="button" variant="ghost" class="oxi-native-control text-xs text-muted-foreground" :disabled="disabled" :aria-label="`${label} 添加自定义字段`" @click="addUnknown"><Plus class="size-3.5" />自定义字段</Button>
        <Button type="button" variant="ghost" size="icon" class="oxi-native-icon text-muted-foreground" :disabled="disabled" :aria-label="`${label} 更改值类型`" :aria-expanded="showType" @click="showType = !showType"><Braces class="size-3.5" /></Button>
      </div>
    </div>

    <div v-else-if="node.type === 'array'" class="min-w-0 space-y-2">
      <div v-for="(item, index) in node.items" :key="item.id" class="min-w-0 rounded-md border border-border py-2" :class="depth === 0 ? 'px-3' : 'px-2 max-md:border-x-0 max-md:px-0'">
        <div class="oxi-schema-item-header grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-1 max-md:grid-cols-1">
          <div class="oxi-schema-item-summary flex min-w-0 items-center gap-1">
          <button v-if="complex(item)" type="button" class="oxi-native-icon inline-flex shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-primary-accent disabled:opacity-50" :disabled="disabled" :aria-label="`${label}[${index + 1}] 展开参数`" :aria-expanded="isOpen(item.id)" @click="toggle(item.id)"><ChevronDown class="size-3.5" :class="{ '-rotate-90': !isOpen(item.id) }" /></button>
          <span class="mr-auto min-w-0 truncate text-xs text-muted-foreground num">{{ index + 1 }}{{ complex(item) ? ` · ${itemSummary(item)}` : '' }}</span>
          <FieldHelp v-if="schema?.items?.help" :label="`${label}[${index + 1}]`" :text="schema.items.help" />
          </div>
          <div class="oxi-schema-item-actions flex shrink-0 items-center justify-end gap-0.5" role="group" :aria-label="`${label} 条目 ${index + 1} 操作`">
          <Button type="button" variant="ghost" size="icon" class="oxi-native-icon shrink-0" :disabled="disabled || index === 0" :aria-label="`${label} 上移条目 ${index + 1}`" @click="move(index, -1)"><ArrowUp class="size-3.5" /></Button>
          <Button type="button" variant="ghost" size="icon" class="oxi-native-icon shrink-0" :disabled="disabled || index === node.items.length - 1" :aria-label="`${label} 下移条目 ${index + 1}`" @click="move(index, 1)"><ArrowDown class="size-3.5" /></Button>
          <Button type="button" variant="ghost" size="icon" class="oxi-native-icon shrink-0 text-muted-foreground hover:text-destructive-accent" :disabled="disabled" :aria-label="`${label} 删除条目 ${index + 1}`" @click="node.items.splice(index, 1); emit('change')"><Trash2 class="size-3.5" /></Button>
          </div>
        </div>
        <OxidnsSchemaValueEditor v-if="!complex(item) || isOpen(item.id)" :node="item" :schema="schema?.items" :label="`${label}[${index + 1}]`" :depth="depth + 1" :auto-expand-depth="autoExpandDepth" :disabled="disabled" class="mt-2" @change="emit('change')" />
      </div>
      <div class="flex items-center gap-2">
        <Button type="button" variant="outline" class="oxi-native-control" :disabled="disabled" :aria-label="`${label} 添加条目`" @click="addItem"><Plus class="size-3.5" />添加条目</Button>
        <Button type="button" variant="ghost" size="icon" class="oxi-native-icon text-muted-foreground" :disabled="disabled" :aria-label="`${label} 更改值类型`" :aria-expanded="showType" @click="showType = !showType"><Braces class="size-3.5" /></Button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, Braces, ChevronDown, Plus, Trash2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import FieldHelp from '@/components/common/FieldHelp.vue'
import OxidnsParameterPicker from './OxidnsParameterPicker.vue'
import { appendConfigEntry, configValueTypes, defaultConfigNode, valueToConfigNode, type ConfigValueNode, type ConfigValueType } from '@/lib/oxidnsConfigEditor'
import type { OxidnsSchema } from '@/lib/oxidnsSchema'

const props = withDefaults(defineProps<{ node: ConfigValueNode; schema?: OxidnsSchema; label: string; depth?: number; disabled?: boolean; layout?: 'default' | 'plugin'; autoExpandDepth?: number }>(), { depth: 0, disabled: false, layout: 'default', autoExpandDepth: 0 })
const emit = defineEmits<{ (event: 'change'): void }>()
const showType = ref(false)
const opened = ref(new Set<string>())
watch(() => props.node.id, () => {
  const expand = props.depth < props.autoExpandDepth && (props.node.type !== 'array' || props.node.items.length <= 4)
  opened.value = new Set(expand ? [...props.node.entries.map(entry => entry.id), ...props.node.items.map(item => item.id)] : [])
}, { immediate: true })
const sensitive = computed(() => /(?:^|\.)(?:(?:[a-z_]+_)?(?:password|passwd|secret|token)|api_key|authorization)$/i.test(props.label))
const missingFields = computed(() => Object.entries(props.schema?.properties ?? {}).filter(([key]) => !props.node.entries.some(entry => entry.key === key)).map(([key, schema]) => ({ key, schema })))
const enumValues = computed(() => {
  const options = props.schema?.enum ?? []
  return props.node.scalar && !options.some(value => String(value) === props.node.scalar) ? [props.node.scalar, ...options] : options
})
const complex = (node: ConfigValueNode) => node.type === 'object' || node.type === 'array'
const countSummary = (node: ConfigValueNode) => node.type === 'object' ? `${node.entries.length} 个参数` : `${node.items.length} 个条目`
const knownField = (key: string) => props.schema?.properties?.[key]
const fieldSchema = (key: string) => knownField(key) ?? props.schema?.additionalProperties
function fieldHelp(key: string) {
  const schema = fieldSchema(key)
  return (schema?.help || '扩展参数。字段名、值类型与含义以对应 OxiDNS 插件文档为准；切换 YAML 会保留该字段。') + (schema?.required ? '\n此参数为官方文档中的必填项。' : '')
}
function itemSummary(node: ConfigValueNode) {
  const title = node.entries.find(entry => ['addr', 'tag', 'exec', 'path', 'name', 'domain'].includes(entry.key) && entry.value.type === 'string')
  return title?.value.scalar || countSummary(node)
}
const inlineEntry = (key: string) => props.layout === 'plugin' && key === 'args'
function entryClasses(key: string, node: ConfigValueNode) {
  if (inlineEntry(key)) return 'border-t border-border pt-3 md:col-span-2'
  if (complex(node)) return props.depth === 0 ? 'rounded-md border border-border p-2 md:col-span-full' : 'border-t border-border pt-2'
  return props.layout === 'plugin' && !['tag', 'type'].includes(key) ? 'md:col-span-2' : ''
}
function isOpen(id: string) {
  return opened.value.has(id)
}
function toggle(id: string) {
  opened.value.has(id) ? opened.value.delete(id) : opened.value.add(id)
}
function initialNode(schema?: OxidnsSchema) {
  return schema && Object.prototype.hasOwnProperty.call(schema, 'default') ? valueToConfigNode(schema.default) : defaultConfigNode(schema?.type ?? 'string')
}
function changeType(type: ConfigValueType) {
  if (type === props.node.type) return
  Object.assign(props.node, { ...defaultConfigNode(type), id: props.node.id })
  emit('change')
}
function addField(key: string) {
  if (props.disabled || !knownField(key) || props.node.entries.some(entry => entry.key === key)) return
  const entry = appendConfigEntry(props.node, key)
  entry.value = initialNode(knownField(key))
  opened.value.add(entry.id)
  emit('change')
}
function addUnknown() {
  const entry = appendConfigEntry(props.node, 'field')
  if (props.schema?.additionalProperties) entry.value = initialNode(props.schema.additionalProperties)
  opened.value.add(entry.id)
  emit('change')
}
function addItem() {
  const node = initialNode(props.schema?.items)
  props.node.items.push(node)
  opened.value.add(node.id)
  emit('change')
}
function move(index: number, offset: number) {
  const [item] = props.node.items.splice(index, 1)
  props.node.items.splice(index + offset, 0, item)
  emit('change')
}
</script>

<style>
.oxi-native-control { min-height: 36px; height: auto; }
.oxi-native-icon { width: 36px; height: 36px; }
@media (max-width: 767px), (pointer: coarse) {
  .oxi-native-control { min-height: 44px; }
  .oxi-native-icon { width: 44px; height: 44px; }
}
</style>
