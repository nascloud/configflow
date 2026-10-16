<template>
  <div class="mt-3 min-w-0 border-t border-border pt-3">
    <OxidnsSchemaValueEditor :node="tree" :schema="schema" :label="`${label}其他上游参数`" @change="commit" />
    <p v-if="error" role="alert" class="mt-2 text-xs text-destructive-accent">{{ error }}</p>
  </div>
</template>

<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { configNodeToValue, valueToConfigNode } from '@/lib/oxidnsConfigEditor'
import { oxidnsUpstreamSchema } from '@/lib/oxidnsSchema'
import OxidnsSchemaValueEditor from './OxidnsSchemaValueEditor.vue'

const props = defineProps<{ modelValue: Record<string, any> | string | number; label: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: Record<string, any>]; error: [message: string] }>()
const common = new Set(['addr', 'bootstrap', 'dial_addr', 'enable_pipeline'])
const schema = { ...oxidnsUpstreamSchema, label: '其他上游参数', properties: Object.fromEntries(Object.entries(oxidnsUpstreamSchema.properties || {}).filter(([key]) => !common.has(key))) }
const tree = ref(valueToConfigNode({}))
const error = ref('')
let expected: string | undefined
const extraFields = (value: typeof props.modelValue) => typeof value === 'object' && value !== null ? Object.fromEntries(Object.entries(value).filter(([key]) => !common.has(key))) : {}
function report(message = '') { error.value = message; emit('error', message) }
function commit() {
  try {
    const kept = typeof props.modelValue === 'object' ? Object.fromEntries(Object.entries(props.modelValue).filter(([key]) => common.has(key))) : { addr: props.modelValue }
    const extras = configNodeToValue(tree.value)
    const value = { ...kept, ...extras }
    expected = JSON.stringify(extras)
    emit('update:modelValue', value); report()
  } catch (failure: any) { report(failure.message || '请检查上游参数。') }
}
watch(() => JSON.stringify(extraFields(props.modelValue)), value => {
  if (expected === value) { expected = undefined; return }
  expected = undefined
  try { tree.value = valueToConfigNode(extraFields(props.modelValue)); report() }
  catch (failure: any) { report(failure.message) }
}, { immediate: true })
onUnmounted(() => emit('error', ''))
</script>
