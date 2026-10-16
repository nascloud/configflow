<template>
  <div class="min-w-0 space-y-2" :data-value-type="node.type">
    <div class="flex min-w-0 flex-wrap items-center gap-2">
      <Select :model-value="node.type" @update:model-value="changeType(String($event) as ConfigValueType)">
        <SelectTrigger class="min-h-11 w-28 shrink-0" :aria-label="`${label} 类型`"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem v-for="option in configValueTypes" :key="option.value" :value="option.value">{{ option.label }}</SelectItem></SelectContent>
      </Select>
      <Textarea v-if="node.type === 'string'" :model-value="node.scalar" :rows="1" :aria-label="`${label} 值`" class="min-h-11 min-w-0 flex-1 basis-36 font-mono text-xs" @update:model-value="node.scalar = String($event); emit('change')" />
      <Input v-else-if="node.type === 'number'" :model-value="node.scalar" inputmode="decimal" :aria-label="`${label} 值`" class="min-h-11 min-w-0 flex-1 basis-36 font-mono text-xs" @update:model-value="node.scalar = String($event); emit('change')" />
      <Select v-else-if="node.type === 'boolean'" :model-value="node.scalar" @update:model-value="node.scalar = String($event); emit('change')">
        <SelectTrigger class="min-h-11 w-36" :aria-label="`${label} 值`"><SelectValue /></SelectTrigger>
        <SelectContent><SelectItem value="true">true · 是</SelectItem><SelectItem value="false">false · 否</SelectItem></SelectContent>
      </Select>
      <span v-else-if="node.type === 'null'" class="font-mono text-sm text-muted-foreground">null</span>
      <span v-else class="text-xs text-muted-foreground num">{{ node.type === 'object' ? `${node.entries.length} 个字段` : `${node.items.length} 个条目` }}</span>
    </div>

    <div v-if="node.type === 'object'" class="min-w-0 space-y-3 border-border" :class="depth < 3 ? 'border-l pl-3' : 'border-t pt-2'">
      <div v-for="(entry, index) in node.entries" :key="entry.id" class="min-w-0 space-y-2">
        <div class="flex min-w-0 items-center gap-2">
          <Input :model-value="entry.key" :aria-label="`${label} 字段名 ${index + 1}`" placeholder="字段名" class="min-h-11 min-w-0 flex-1 font-mono text-xs" @update:model-value="entry.key = String($event); emit('change')" />
          <Button type="button" variant="ghost" size="icon" class="size-11 shrink-0" :aria-label="`${label} 删除字段 ${index + 1}`" @click="node.entries.splice(index, 1); emit('change')"><Trash2 class="size-4" /></Button>
        </div>
        <OxidnsValueEditor :node="entry.value" :label="`${label}.${entry.key}`" :depth="depth + 1" @change="emit('change')" />
      </div>
      <Button type="button" variant="outline" class="min-h-11" :aria-label="`${label} 添加字段`" @click="appendConfigEntry(node, 'field'); emit('change')"><Plus class="size-3.5" />添加字段</Button>
    </div>

    <div v-else-if="node.type === 'array'" class="min-w-0 space-y-3 border-border" :class="depth < 3 ? 'border-l pl-3' : 'border-t pt-2'">
      <div v-for="(item, index) in node.items" :key="item.id" class="min-w-0 space-y-2">
        <div class="flex min-w-0 items-center gap-1">
          <span class="mr-auto text-xs text-muted-foreground num">条目 {{ index + 1 }}</span>
          <Button type="button" variant="ghost" size="icon" class="size-11 shrink-0" :disabled="index === 0" :aria-label="`${label} 上移条目 ${index + 1}`" @click="move(index, -1)"><ArrowUp class="size-4" /></Button>
          <Button type="button" variant="ghost" size="icon" class="size-11 shrink-0" :disabled="index === node.items.length - 1" :aria-label="`${label} 下移条目 ${index + 1}`" @click="move(index, 1)"><ArrowDown class="size-4" /></Button>
          <Button type="button" variant="ghost" size="icon" class="size-11 shrink-0" :aria-label="`${label} 删除条目 ${index + 1}`" @click="node.items.splice(index, 1); emit('change')"><Trash2 class="size-4" /></Button>
        </div>
        <OxidnsValueEditor :node="item" :label="`${label}[${index + 1}]`" :depth="depth + 1" @change="emit('change')" />
      </div>
      <Button type="button" variant="outline" class="min-h-11" :aria-label="`${label} 添加条目`" @click="node.items.push(defaultConfigNode()); emit('change')"><Plus class="size-3.5" />添加条目</Button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ArrowDown, ArrowUp, Plus, Trash2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { appendConfigEntry, configValueTypes, defaultConfigNode, type ConfigValueNode, type ConfigValueType } from '@/lib/oxidnsConfigEditor'

const props = withDefaults(defineProps<{ node: ConfigValueNode; label: string; depth?: number }>(), { depth: 0 })
const emit = defineEmits<{ (event: 'change'): void }>()
function changeType(type: ConfigValueType) {
  if (type === props.node.type) return
  Object.assign(props.node, { ...defaultConfigNode(type), id: props.node.id })
  emit('change')
}
function move(index: number, offset: number) {
  const [item] = props.node.items.splice(index, 1)
  props.node.items.splice(index + offset, 0, item)
  emit('change')
}
</script>
