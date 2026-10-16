<template>
  <div class="grid min-w-0 grid-cols-1 gap-4 sm:grid-cols-2">
    <FormField v-for="field in visibleFields" :key="field.path" :label="field.label" :html-for="`oxi-${field.path}`" :help="field.hint" :documentation="field.documentation" :class="field.wide && 'sm:col-span-2'">
      <OxidnsEntryEditor v-if="['list', 'numbers', 'upstreams', 'lines'].includes(field.type || '')" :id="`oxi-${field.path}`" :model-value="read(field.path)" :label="field.label" :kind="field.type === 'list' ? 'strings' : field.type as 'numbers' | 'upstreams' | 'lines'" :placeholder="field.placeholder" :empty-hint="field.emptyHint" @update:model-value="write(field.path, $event)" @error="emit('error', field.path, $event ? `${field.label}：${$event}` : '')" />
      <div v-else-if="field.type === 'switch' || field.type === 'tls'" class="flex min-h-11 items-center">
        <Switch :id="`oxi-${field.path}`" :model-value="Boolean(read(field.path))" @update:model-value="write(field.path, field.type === 'tls' ? ($event ? {} : undefined) : $event)" />
      </div>
      <Select v-else-if="field.options" :model-value="String(read(field.path) ?? '')" @update:model-value="write(field.path, $event)">
        <SelectTrigger :id="`oxi-${field.path}`" class="min-h-11"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem v-for="option in field.options" :key="option.value" :value="option.value">{{ option.label }}</SelectItem></SelectContent>
      </Select>
      <Textarea v-else-if="field.type === 'textarea'" :id="`oxi-${field.path}`" :model-value="display(field)" :rows="field.rows ?? 3" :placeholder="field.placeholder" class="min-w-0 font-mono text-xs" @update:model-value="updateText(field, String($event))" />
      <Input v-else :id="`oxi-${field.path}`" :model-value="read(field.path) ?? ''" :type="field.type ?? 'text'" :min="field.min" :max="field.max" :placeholder="field.placeholder" :autocomplete="field.type === 'password' ? 'new-password' : 'off'" class="min-h-11 min-w-0 font-mono" @update:model-value="write(field.path, field.type === 'number' ? Number($event) : $event)" />
    </FormField>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import FormField from '@/components/common/FormField.vue'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { OxidnsField } from '@/lib/oxidns'
import OxidnsEntryEditor from './OxidnsEntryEditor.vue'

const props = defineProps<{ settings: Record<string, any>; fields: OxidnsField[] }>()
const emit = defineEmits<{ error: [path: string, message: string] }>()
const textDrafts = reactive<Record<string, string>>({})
watch(() => props.settings, () => Object.keys(textDrafts).forEach(key => delete textDrafts[key]))
const read = (path: string) => path.split('.').reduce((value, key) => value?.[key], props.settings)
const visibleFields = computed(() => props.fields.filter(field => !field.when || Boolean(read(field.when))))
const write = (path: string, value: unknown) => {
  const keys = path.split('.')
  const key = keys.pop()!
  const target = keys.reduce((value, part) => (value[part] ??= {}), props.settings)
  if (value === undefined) delete target[key]
  else target[key] = value
}
const display = (field: OxidnsField) => {
  if (field.path in textDrafts) return textDrafts[field.path]
  const value = read(field.path)
  return Array.isArray(value) ? value.join('\n') : String(value ?? '')
}
const updateText = (field: OxidnsField, text: string) => {
  textDrafts[field.path] = text
  if (field.type === 'list') write(field.path, text.split('\n').map(line => line.trim()).filter(Boolean))
  else if (field.type === 'numbers') write(field.path, text.split(/[\s,]+/).filter(Boolean).map(Number))
  else write(field.path, text)
}
</script>
