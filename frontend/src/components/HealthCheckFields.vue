<template>
  <details class="group rounded-lg border border-border bg-background/30" data-health-advanced>
    <summary class="flex cursor-pointer list-none items-center gap-2 rounded-lg px-3 py-2.5 select-none hover:bg-accent/40 focus-visible:ring-2 focus-visible:ring-primary-accent focus-visible:outline-none [&::-webkit-details-marker]:hidden">
      <ChevronRight class="size-4 shrink-0 text-muted-foreground transition-transform duration-200 group-open:rotate-90" />
      <span class="shrink-0 text-sm font-medium">健康检查</span>
      <span class="min-w-0 flex-1 truncate text-right text-xs text-muted-foreground" :title="summary">{{ summary }}</span>
    </summary>
    <div class="grid gap-3 border-t border-border px-3 pt-3 pb-3.5">
      <p v-if="provider" class="m-0 text-xs leading-relaxed text-muted-foreground">
        影响所有引用此来源的配置，与策略组检测独立。“未设置”项生成时取默认值（启用、懒惰检测、超时 5 秒）。
      </p>
      <div class="grid grid-cols-2 gap-x-4 gap-y-3 max-sm:grid-cols-1">
        <label class="col-span-full grid gap-1.5">
          <span :class="labelClass">测试 URL</span>
          <input :id="provider ? undefined : 'group-url'" data-health="url" :class="[fieldClass, 'font-mono text-[12px]']" :value="modelValue.url ?? ''" placeholder="未设置（保持原行为）" @input="setText('url', $event)" />
        </label>
        <label class="grid gap-1.5">
          <span :class="labelClass">测试间隔（秒）</span>
          <input :id="provider ? undefined : 'group-interval'" data-health="interval" type="number" min="1" max="86400" :class="[fieldClass, 'num']" :value="modelValue.interval ?? ''" placeholder="未设置" @input="setNumber('interval', $event)" />
        </label>
        <label class="grid gap-1.5">
          <span :class="labelClass">超时（秒）</span>
          <input data-health="timeout" type="number" min="0.001" max="600" step="0.001" :class="[fieldClass, 'num']" :value="modelValue.timeout === undefined ? '' : modelValue.timeout / 1000" placeholder="未设置" @input="setNumber('timeout', $event, 1000)" />
        </label>
        <label class="grid gap-1.5">
          <span :class="labelClass">预期 HTTP 状态</span>
          <input data-health="expected-status" :class="[fieldClass, 'font-mono text-[12px]']" :value="modelValue['expected-status'] ?? ''" placeholder="未设置 / 204 / 200-299/304" @input="setText('expected-status', $event)" />
        </label>
        <label class="grid gap-1.5">
          <span :class="labelClass">懒惰检测</span>
          <span class="relative">
            <select data-health="lazy" :class="selectClass" :value="choice('lazy')" @change="setChoice('lazy', $event)">
              <option value="unset">未设置</option><option value="true">是</option><option value="false">否</option>
            </select>
            <ChevronDown class="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          </span>
        </label>
        <label v-if="provider" class="grid gap-1.5">
          <span :class="labelClass">启用</span>
          <span class="relative">
            <select data-health="enable" :class="selectClass" :value="choice('enable')" @change="setChoice('enable', $event)">
              <option value="unset">未设置</option><option value="true">是</option><option value="false">否</option>
            </select>
            <ChevronDown class="pointer-events-none absolute top-1/2 right-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
          </span>
        </label>
        <label v-if="!provider && groupType === 'url-test'" class="grid gap-1.5">
          <span :class="labelClass">容差（毫秒）</span>
          <input data-health="tolerance" type="number" min="0" max="600000" :class="[fieldClass, 'num']" :value="modelValue.tolerance ?? ''" placeholder="未设置（旧有效值 100）" @input="setNumber('tolerance', $event)" />
        </label>
      </div>
    </div>
  </details>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { ChevronDown, ChevronRight } from '@lucide/vue'
const props = defineProps<{ modelValue: Record<string, any>; provider?: boolean; groupType?: string }>()
const emit = defineEmits<{ 'update:modelValue': [Record<string, any>] }>()
const labelClass = 'text-xs text-muted-foreground'
const fieldClass = 'h-9 w-full min-w-0 rounded-md border border-input bg-background/50 px-3 text-sm text-foreground outline-none transition-[color,box-shadow] placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50'
const selectClass = `${fieldClass} cursor-pointer appearance-none pr-8 [&_option]:bg-popover [&_option]:text-popover-foreground`
const yesNo = (value: unknown) => value === undefined ? '未设置' : value ? '是' : '否'
const summary = computed(() => {
  const v = props.modelValue
  const parts = [
    v.url ?? 'URL 未设置',
    `间隔 ${v.interval ?? '未设置'}${v.interval === undefined ? '' : ' 秒'}`,
    `超时 ${v.timeout === undefined ? '未设置' : v.timeout / 1000 + ' 秒'}`,
    `懒惰 ${yesNo(v.lazy)}`,
    `状态 ${v['expected-status'] ?? '未设置'}`,
  ]
  if (props.provider) parts.push(`启用 ${yesNo(v.enable)}`)
  else if (props.groupType === 'url-test') parts.push(`容差 ${v.tolerance ?? '未设置'}${v.tolerance === undefined ? '' : ' ms'}`)
  return parts.join(' · ')
})
const choice = (key: string) => props.modelValue[key] === undefined ? 'unset' : String(props.modelValue[key])
const update = (key: string, value: any) => { const next = { ...props.modelValue }; if (value === undefined) delete next[key]; else next[key] = value; emit('update:modelValue', next) }
const target = (event: Event) => (event.target as HTMLInputElement).value
const setText = (key: string, event: Event) => update(key, target(event) || undefined)
const setNumber = (key: string, event: Event, scale = 1) => {
  const text = target(event)
  const raw = Number(text) * scale
  const value = Math.abs(raw - Math.round(raw)) < 1e-7 ? Math.round(raw) : raw
  update(key, text === '' ? undefined : value)
}
const setChoice = (key: string, event: Event) => update(key, target(event) === 'unset' ? undefined : target(event) === 'true')
</script>
