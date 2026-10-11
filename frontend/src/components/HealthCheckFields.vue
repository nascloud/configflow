<template>
  <details class="rounded-lg border border-border/50 p-3" data-health-advanced>
    <summary class="cursor-pointer">健康检查高级设置 · {{ summary }}</summary>
    <p v-if="provider" class="my-2 text-sm text-muted-foreground">共享来源：影响所有引用此订阅/聚合的配置。保存只修改来源，不推送 Agent；与策略组检测独立。显式健康检查对象中，“未设置”保留字段缺省，生成时采用服务端默认值（启用是、lazy 是、超时 5 秒），不会写回或覆盖显式“否”。</p>
    <div class="mt-3 grid gap-3">
      <label v-if="provider">启用 <select data-health="enable" :value="choice('enable')" @change="setChoice('enable', $event)"><option value="unset">未设置</option><option value="true">是</option><option value="false">否</option></select></label>
      <label>测试 URL <input :id="provider ? undefined : 'group-url'" data-health="url" class="w-full rounded border bg-background p-2" :value="modelValue.url ?? ''" placeholder="未设置（保持原行为）" @input="setText('url', $event)" /></label>
      <label>测试间隔（秒） <input :id="provider ? undefined : 'group-interval'" data-health="interval" type="number" min="1" max="86400" :value="modelValue.interval ?? ''" @input="setNumber('interval', $event)" /></label>
      <label>超时（秒，YAML 毫秒） <input data-health="timeout" type="number" min="0.001" max="600" step="0.001" :value="modelValue.timeout === undefined ? '' : modelValue.timeout / 1000" @input="setNumber('timeout', $event, 1000)" /></label>
      <label>懒惰检测 <select data-health="lazy" :value="choice('lazy')" @change="setChoice('lazy', $event)"><option value="unset">未设置</option><option value="true">是</option><option value="false">否</option></select></label>
      <label>预期 HTTP 状态 <input data-health="expected-status" :value="modelValue['expected-status'] ?? ''" placeholder="未设置 / 204 / 200-299/304" @input="setText('expected-status', $event)" /></label>
      <label v-if="!provider && groupType === 'url-test'">容差（毫秒） <input data-health="tolerance" type="number" min="0" max="600000" :value="modelValue.tolerance ?? ''" @input="setNumber('tolerance', $event)" /></label>
    </div>
  </details>
</template>
<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ modelValue: Record<string, any>; provider?: boolean; groupType?: string }>()
const emit = defineEmits<{ 'update:modelValue': [Record<string, any>] }>()
const summary = computed(() => `${props.modelValue.url ?? 'URL 未设置'} · ${props.modelValue.interval ?? '未设置'} 秒 · 超时 ${props.modelValue.timeout === undefined ? '未设置' : props.modelValue.timeout / 1000 + ' 秒'} · lazy ${props.modelValue.lazy ?? '未设置'} · 状态 ${props.modelValue['expected-status'] ?? '未设置'}${props.provider ? ' · enable ' + (props.modelValue.enable ?? '未设置') : props.groupType === 'url-test' ? ' · 容差 ' + (props.modelValue.tolerance ?? '未设置（旧有效值 100）') + ' ms' : ''}`)
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
