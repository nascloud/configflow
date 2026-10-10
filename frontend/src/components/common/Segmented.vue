<template>
  <div
    ref="root"
    role="radiogroup"
    :aria-label="label"
    :class="cn('relative inline-flex rounded-xl border border-border bg-secondary p-[3px]', block && 'flex w-full', props.class)"
  >
    <span
      class="pointer-events-none absolute top-[3px] bottom-[3px] rounded-[9px] bg-card shadow-[0_2px_10px_-4px_rgb(0_0_0/40%),0_0_0_1px_var(--border-strong)] transition-[left,width] duration-350 ease-(--ease-flow)"
      :style="thumb"
      aria-hidden="true"
    />
    <button
      v-for="option in options"
      :key="option.value"
      ref="buttons"
      type="button"
      role="radio"
      :aria-checked="option.value === modelValue"
      :disabled="disabled"
      :class="cn(
        'relative z-1 h-[30px] cursor-pointer rounded-[9px] px-3.5 text-[13px] whitespace-nowrap transition-colors duration-250 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none disabled:cursor-not-allowed disabled:opacity-50',
        block && 'flex-1 px-2',
        option.value === modelValue ? 'text-foreground' : 'text-muted-foreground hover:text-foreground'
      )"
      @click="emit('update:modelValue', option.value)"
    >
      {{ option.label }}
    </button>
  </div>
</template>

<script setup lang="ts" generic="T extends string">
import { nextTick, onMounted, onUnmounted, ref, watch, type HTMLAttributes } from 'vue'
import { cn } from '@/lib/utils'

/** 分段控件：选中项下方有一块滑动的底，用于「定一个参数」的单选 */
const props = defineProps<{
  modelValue: T
  options: Array<{ value: T; label: string }>
  label?: string
  block?: boolean
  disabled?: boolean
  class?: HTMLAttributes['class']
}>()
const emit = defineEmits<{ (e: 'update:modelValue', value: T): void }>()

const root = ref<HTMLElement | null>(null)
const buttons = ref<HTMLButtonElement[]>([])
const thumb = ref<Record<string, string>>({ left: '3px', width: '0px' })

const place = () => {
  const index = props.options.findIndex(o => o.value === props.modelValue)
  const el = buttons.value[index]
  if (!el) return
  thumb.value = { left: `${el.offsetLeft}px`, width: `${el.offsetWidth}px` }
}

let observer: ResizeObserver | null = null
onMounted(() => {
  nextTick(place)
  observer = new ResizeObserver(place)
  if (root.value) observer.observe(root.value)
})
onUnmounted(() => observer?.disconnect())
watch(() => [props.modelValue, props.options], () => nextTick(place), { deep: true })
</script>
