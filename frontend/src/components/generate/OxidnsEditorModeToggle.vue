<template>
  <div class="inline-flex h-9 shrink-0 items-stretch rounded-lg border border-border bg-secondary p-0.5 max-sm:h-[50px] pointer-coarse:h-[50px]" role="group" :aria-label="label">
    <Button
      v-for="option in options"
      :key="option.value"
      type="button"
      size="sm"
      variant="ghost"
      class="h-full min-w-11 rounded-md border px-2.5 text-xs shadow-none"
      :class="modelValue === option.value
        ? 'border-primary-accent bg-primary-soft text-primary-accent hover:bg-primary-soft dark:border-border-strong dark:bg-card dark:text-foreground dark:hover:bg-card'
        : 'border-transparent text-muted-foreground'"
      :aria-pressed="modelValue === option.value"
      :disabled="disabled || option.disabled"
      @click="emit('update:modelValue', option.value)"
    >{{ option.label }}</Button>
  </div>
</template>

<script setup lang="ts" generic="T extends string">
import { Button } from '@/components/ui/button'

defineProps<{
  modelValue: T
  options: Array<{ value: T; label: string; disabled?: boolean }>
  label: string
  disabled?: boolean
}>()
const emit = defineEmits<{ 'update:modelValue': [value: T] }>()
</script>
