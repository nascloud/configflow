<template>
  <section class="min-w-0 space-y-3" :aria-label="label">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <span class="text-xs text-muted-foreground">{{ rows.length }} 个条目</span>
      <div class="inline-flex rounded-lg border border-border bg-secondary p-1" :aria-label="`${label}编辑模式`" role="group">
        <Button v-for="option in modes" :key="option.value" size="sm" :variant="mode === option.value ? 'secondary' : 'ghost'" class="min-h-11" :aria-pressed="mode === option.value" @click="switchMode(option.value)">{{ option.label }}</Button>
      </div>
    </div>
    <template v-if="mode === 'entries'">
      <p v-if="!rows.length" class="rounded-lg border border-dashed border-border px-3 py-5 text-sm text-muted-foreground">{{ emptyHint || '暂无条目，点击下方按钮添加。' }}</p>
      <div v-for="(row, index) in rows" :key="row.id" class="min-w-0 space-y-2 rounded-lg border border-border p-3">
        <div class="flex min-w-0 items-center gap-2">
          <span class="w-5 shrink-0 font-mono text-xs text-muted-foreground">{{ index + 1 }}</span>
          <Input :id="index === 0 ? id : undefined" :model-value="kind === 'upstreams' ? upstreamAddress(row.value) : String(row.value)" :aria-label="`${label}第 ${index + 1} 项${kind === 'upstreams' ? '地址' : ''}`" :placeholder="placeholder" :type="kind === 'numbers' ? 'number' : 'text'" :min="kind === 'numbers' ? 1 : undefined" :max="kind === 'numbers' ? 65535 : undefined" class="min-h-11 min-w-0 flex-1 font-mono text-xs" @update:model-value="editValue(index, String($event))" />
          <Button variant="ghost" size="icon" class="size-11 shrink-0" :aria-label="`删除${label}第 ${index + 1} 项`" @click="remove(index)"><Trash2 class="size-4" /></Button>
        </div>
        <div class="flex flex-wrap items-center gap-1 pl-7">
          <Button variant="ghost" size="sm" class="min-h-11" :disabled="index === 0" :aria-label="`上移${label}第 ${index + 1} 项`" @click="move(index, -1)"><ArrowUp class="size-3.5" />上移</Button>
          <Button variant="ghost" size="sm" class="min-h-11" :disabled="index === rows.length - 1" :aria-label="`下移${label}第 ${index + 1} 项`" @click="move(index, 1)"><ArrowDown class="size-3.5" />下移</Button>
          <span v-if="kind === 'upstreams' && extraCount(row.value)" class="text-xs text-muted-foreground">另有 {{ extraCount(row.value) }} 个扩展参数，已保留，可在 YAML 中编辑</span>
        </div>
        <details v-if="kind === 'upstreams'" class="pl-7">
          <summary class="min-h-11 cursor-pointer py-3 text-xs text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">上游参数</summary>
          <div class="grid min-w-0 gap-3 sm:grid-cols-2">
            <FormField v-for="key in ['bootstrap', 'dial_addr']" :key="key" :label="key === 'bootstrap' ? 'Bootstrap DNS' : '连接地址（dial_addr）'">
              <Input :model-value="upstreamField(row.value, key)" :aria-label="`${label}第 ${index + 1} 项 ${key}`" class="min-h-11 min-w-0 font-mono text-xs" @update:model-value="editUpstream(index, key, String($event))" />
            </FormField>
            <FormField label="连接流水线">
              <Select :model-value="pipeline(row.value)" @update:model-value="editUpstream(index, 'enable_pipeline', $event === 'default' ? undefined : $event === 'true')">
                <SelectTrigger class="min-h-11" :aria-label="`${label}第 ${index + 1} 项连接流水线`"><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="default">核心默认</SelectItem><SelectItem value="true">启用</SelectItem><SelectItem value="false">关闭</SelectItem></SelectContent>
              </Select>
            </FormField>
          </div>
        </details>
      </div>
      <Button variant="outline" class="min-h-11" @click="add"><Plus class="size-4" />添加{{ kind === 'upstreams' ? '上游' : '条目' }}</Button>
    </template>
    <div v-else class="space-y-2">
      <Textarea :id="`${id}-yaml`" :model-value="yamlDraft" :aria-label="`${label} YAML`" :rows="Math.min(14, Math.max(5, rows.length * 3))" class="min-w-0 font-mono text-xs" spellcheck="false" @update:model-value="editYaml(String($event))" />
      <p class="text-xs text-muted-foreground">{{ kind === 'upstreams' ? '使用 YAML 列表填写地址和完整上游参数。' : '使用 YAML 列表批量编辑，每个列表项对应一条。' }} 切换模式会保留配置值，YAML 注释与排版可能重新整理。</p>
    </div>
    <p v-if="error" role="alert" class="break-words text-sm text-destructive-accent">{{ error }}</p>
  </section>
</template>

<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { ArrowDown, ArrowUp, Plus, Trash2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import FormField from '@/components/common/FormField.vue'
import { encodeEntries, entryYaml, parseEntryModel, parseEntryYaml, type EntryKind, type EntryValue } from '@/lib/oxidnsEntries'

const props = defineProps<{ modelValue: unknown; kind: EntryKind; label: string; id: string; placeholder?: string; emptyHint?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string | EntryValue[]]; validity: [valid: boolean]; error: [message: string] }>()
const mode = ref<'entries' | 'yaml'>('entries')
const modes = [{ value: 'entries' as const, label: '条目' }, { value: 'yaml' as const, label: 'YAML' }]
const rows = ref<Array<{ id: number; value: EntryValue }>>([])
const yamlDraft = ref('')
const error = ref('')
let sequence = 0
let expected: string | undefined
const setError = (message: string) => { error.value = message; emit('validity', !message); emit('error', message) }
onUnmounted(() => { emit('validity', true); emit('error', '') })
const values = () => rows.value.map(row => row.value)
const replaceRows = (entries: EntryValue[]) => { rows.value = entries.map(value => ({ id: ++sequence, value })) }
const send = () => {
  try {
    const value = encodeEntries(values(), props.kind)
    expected = JSON.stringify(value)
    emit('update:modelValue', value)
    setError('')
    return true
  } catch (cause: any) { setError(cause.message); return false }
}
watch(() => props.modelValue, value => {
  if (JSON.stringify(value) === expected) return
  try { replaceRows(parseEntryModel(value, props.kind)); yamlDraft.value = entryYaml(values()); setError('') }
  catch (cause: any) { mode.value = 'yaml'; yamlDraft.value = typeof value === 'string' ? value : entryYaml(value); setError(cause.message) }
}, { immediate: true, deep: true })
const switchMode = (next: 'entries' | 'yaml') => {
  if (next === mode.value) return
  if (next === 'entries') {
    try { replaceRows(parseEntryYaml(yamlDraft.value, props.kind)); if (!send()) return }
    catch (cause: any) { setError(cause.message); return }
  } else { yamlDraft.value = entryYaml(values()) }
  mode.value = next
}
const editYaml = (text: string) => {
  yamlDraft.value = text
  try { replaceRows(parseEntryYaml(text, props.kind)); send() }
  catch (cause: any) { setError(cause.message) }
}
const add = () => { rows.value.push({ id: ++sequence, value: props.kind === 'upstreams' ? { addr: '' } : '' }); send() }
const remove = (index: number) => { rows.value.splice(index, 1); send() }
const move = (index: number, step: number) => { const [row] = rows.value.splice(index, 1); rows.value.splice(index + step, 0, row); send() }
const upstreamAddress = (value: EntryValue) => typeof value === 'string' ? value : typeof value === 'object' ? value.addr : ''
const upstreamField = (value: EntryValue, key: string) => typeof value === 'object' ? value[key] ?? '' : ''
const pipeline = (value: EntryValue) => typeof value === 'object' && typeof value.enable_pipeline === 'boolean' ? String(value.enable_pipeline) : 'default'
const extraCount = (value: EntryValue) => typeof value === 'object' ? Object.keys(value).filter(key => !['addr', 'bootstrap', 'dial_addr', 'enable_pipeline'].includes(key)).length : 0
const editValue = (index: number, value: string) => {
  if (props.kind === 'upstreams') {
    const before = rows.value[index].value
    rows.value[index].value = typeof before === 'string' ? value : { ...before as Record<string, any>, addr: value }
  } else rows.value[index].value = props.kind === 'numbers' && value !== '' ? Number(value) : value
  send()
}
const editUpstream = (index: number, key: string, value: unknown) => {
  const before = rows.value[index].value
  const entry: Record<string, any> = typeof before === 'object' ? { ...before } : { addr: before }
  if (value === undefined || value === '') delete entry[key]
  else entry[key] = value
  rows.value[index].value = entry
  send()
}
</script>
