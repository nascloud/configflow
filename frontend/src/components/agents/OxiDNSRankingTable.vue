<template>
  <SectionCard class="min-w-0" :title="title" description="按请求次数排列 · 前 10 项" :padded="false">
    <EmptyState v-if="!entries.length" :title="emptyTitle" :description="emptyDescription" />
    <div v-else class="cf-table-wrap max-w-full" tabindex="0" role="region" :aria-label="title">
      <table class="cf-table table-fixed">
        <caption class="cf-sr">{{ title }}，按请求次数从高到低排列</caption>
        <thead><tr><th class="w-12"><span class="cf-sr">排名</span></th><th>{{ nameLabel }}</th><th class="w-24 text-right">请求次数</th></tr></thead>
        <tbody>
          <tr v-for="(entry, index) in entries.slice(0, 10)" :key="entry.name">
            <td class="num text-muted-foreground">{{ index + 1 }}</td>
            <td class="break-all font-mono text-xs">{{ entry.name }}</td>
            <td class="num break-all text-right">{{ formatMonitorNumber(entry.count) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </SectionCard>
</template>
<script setup lang="ts">
import SectionCard from '@/components/common/SectionCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { formatMonitorNumber } from '@/types/oxidnsMonitor'

defineProps<{
  title: string
  nameLabel: string
  entries: Array<{ name: string; count: number }>
  emptyTitle: string
  emptyDescription: string
}>()
</script>
