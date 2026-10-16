<template>
  <div class="flex flex-col gap-1.5">
    <div v-if="label && (help || documentation)" class="flex flex-wrap items-center gap-1">
      <Label :for="htmlFor">{{ label }}</Label>
      <FieldHelp v-if="help" :label="label" :text="help" />
      <a v-if="documentation" :href="documentation.href" target="_blank" rel="noopener noreferrer" class="inline-flex min-h-6 items-center gap-1 rounded-sm px-1 text-xs text-primary-accent underline-offset-4 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-primary-accent max-sm:min-h-11 pointer-coarse:min-h-11" :aria-label="`${documentation.label}（官方文档，新窗口打开）`">
        {{ documentation.label }}<ExternalLink class="size-3" aria-hidden="true" />
      </a>
    </div>
    <Label v-else-if="label" :for="htmlFor">{{ label }}</Label>
    <slot />
    <p v-if="hint" class="m-0 text-[12px] leading-relaxed whitespace-pre-line text-muted-foreground">
      {{ hint }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { Label } from '@/components/ui/label'
import { ExternalLink } from '@lucide/vue'
import FieldHelp from './FieldHelp.vue'

defineProps<{ label?: string; hint?: string; help?: string; htmlFor?: string; documentation?: { href: string; label: string } }>()
</script>
