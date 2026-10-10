<script lang="ts" setup>
import type { ToasterProps } from "vue-sonner"
import { CircleCheckIcon, InfoIcon, Loader2Icon, OctagonXIcon, TriangleAlertIcon, XIcon } from "@lucide/vue"
import { Toaster as Sonner } from "vue-sonner"
import { cn } from "@/lib/utils"

const props = defineProps<ToasterProps>()
</script>

<template>
  <Sonner
    :class="cn('toaster group', props.class)"
    :style="{
      /* 反色胶囊：深色主题下是象牙底黑字，浅色主题下是炭黑底白字 */
      '--normal-bg': 'var(--foreground)',
      '--normal-text': 'var(--background)',
      '--normal-border': 'transparent',
      '--border-radius': '12px',
    }"
    v-bind="props"
  >
    <template #success-icon>
      <CircleCheckIcon class="size-4 text-primary" />
    </template>
    <template #info-icon>
      <InfoIcon class="size-4 text-info" />
    </template>
    <template #warning-icon>
      <TriangleAlertIcon class="size-4 text-warning" />
    </template>
    <template #error-icon>
      <OctagonXIcon class="size-4 text-destructive" />
    </template>
    <template #loading-icon>
      <div>
        <Loader2Icon class="size-4 animate-spin text-primary" />
      </div>
    </template>
    <template #close-icon>
      <XIcon class="size-4" />
    </template>
  </Sonner>
</template>
