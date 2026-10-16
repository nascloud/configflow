<template>
  <div class="space-y-4">
    <div class="grid grid-cols-2 gap-3.5 xl:grid-cols-3">
      <OxiDNSMetric label="DNS 请求" :value="overview.metrics.requests_total" hint="自核心启动以来" />
      <OxiDNSMetric label="近期请求速率" :value="qps" unit="次/秒" :precision="2" :hint="qps === null ? '等待两次有效采样' : '最近两次采样之间的平均值'" />
      <OxiDNSMetric label="平均响应时间" :value="overview.metrics.avg_latency_ms" unit="ms" :precision="2" hint="自核心启动以来" />
      <OxiDNSMetric label="缓存命中率" :value="cacheRate" unit="%" :precision="1" :hint="selectedCache === '__all__' ? '全部缓存 · 命中数 / 查找次数' : selectedTag + ' · 命中数 / 查找次数'" />
      <OxiDNSMetric label="失败请求" :value="overview.metrics.failed_total" hint="自核心启动以来" />
      <OxiDNSMetric label="处理中" :value="overview.metrics.inflight" hint="本次采样时正在处理的请求" />
    </div>

    <SectionCard title="缓存统计" description="自核心启动以来。命中包含新鲜与过期缓存，命中率按缓存查找次数计算，与 DNS 请求总数的口径不同。" :padded="false">
      <template v-if="overview.caches.length > 1" #actions>
        <Select v-model="selectedCache">
          <SelectTrigger class="min-h-11 w-[180px]" aria-label="选择缓存实例"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="__all__">全部缓存</SelectItem>
            <SelectItem v-for="cache in overview.caches" :key="cache.tag" :value="'cache:' + cache.tag">{{ cache.tag }}</SelectItem>
          </SelectContent>
        </Select>
      </template>
      <EmptyState v-if="!overview.caches.length" title="暂无缓存统计" description="当前核心没有返回缓存实例的统计数据。" />
      <div v-else class="cf-table-wrap" tabindex="0" role="region" aria-label="缓存实例统计">
        <table class="cf-table w-full">
          <thead><tr><th class="text-left">实例</th><th class="text-right">命中率</th><th class="text-right">查找</th><th class="text-right">新鲜命中</th><th class="text-right">过期命中</th><th class="text-right">未命中</th><th class="text-right">条目</th></tr></thead>
          <tbody>
            <tr v-for="cache in visibleCaches" :key="cache.tag">
              <td class="font-mono text-xs">{{ cache.tag }}</td>
              <td class="num text-right">{{ formatMonitorNumber(cache.hit_rate, 1) }}<span v-if="monitorNumber(cache.hit_rate)">%</span></td>
              <td class="num text-right">{{ formatMonitorNumber(cache.lookups) }}</td>
              <td class="num text-right">{{ formatMonitorNumber(cache.fresh_hits) }}</td>
              <td class="num text-right">{{ formatMonitorNumber(cache.stale_hits) }}</td>
              <td class="num text-right">{{ formatMonitorNumber(cache.misses) }}</td>
              <td class="num text-right">{{ formatMonitorNumber(cache.entries) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </SectionCard>

    <SectionCard title="监听服务统计" description="各 DNS 监听服务自核心启动以来的统计。" :padded="false">
      <EmptyState v-if="!overview.servers.length" title="暂无监听服务统计" description="核心尚未返回监听服务指标。" />
      <div v-else class="cf-table-wrap" tabindex="0" role="region" aria-label="DNS 监听服务统计">
        <table class="cf-table w-full">
          <thead><tr><th class="text-left">服务</th><th class="text-left">协议</th><th class="text-right">请求</th><th class="text-right">失败</th><th class="text-right">平均耗时</th><th class="text-right">处理中</th></tr></thead>
          <tbody>
            <tr v-for="server in overview.servers" :key="server.tag + ':' + server.protocol">
              <td class="font-mono text-xs">{{ server.tag }}</td>
              <td class="font-mono text-xs">{{ server.protocol || '—' }}</td>
              <td class="num text-right">{{ formatMonitorNumber(server.requests) }}</td>
              <td class="num text-right">{{ formatMonitorNumber(server.failed) }}</td>
              <td class="num text-right">{{ formatMonitorNumber(server.avg_latency_ms, 2) }}<span v-if="monitorNumber(server.avg_latency_ms)"> ms</span></td>
              <td class="num text-right">{{ formatMonitorNumber(server.inflight) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </SectionCard>
  </div>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import SectionCard from '@/components/common/SectionCard.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import OxiDNSMetric from './OxiDNSMetric.vue'
import { formatMonitorNumber, monitorNumber, type OxiDNSOverview } from '@/types/oxidnsMonitor'
const props = defineProps<{ overview: OxiDNSOverview; qps: number | null }>()
const selectedCache = ref('__all__')
const selectedTag = computed(() => selectedCache.value.startsWith('cache:') ? selectedCache.value.slice(6) : '')
watch(() => props.overview.caches, caches => {
  if (!caches.some(cache => cache.tag === selectedTag.value)) selectedCache.value = '__all__'
})
const visibleCaches = computed(() => selectedCache.value === '__all__' ? props.overview.caches : props.overview.caches.filter(cache => cache.tag === selectedTag.value))
const cacheRate = computed(() => selectedCache.value === '__all__' ? props.overview.metrics.cache_hit_rate : visibleCaches.value[0]?.hit_rate ?? null)
</script>
