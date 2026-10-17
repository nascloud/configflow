<template>
  <div class="min-w-0">
    <div class="flex min-w-0 flex-wrap items-center gap-x-2">
      <span class="min-w-0 break-words">{{ op.summary }}</span>
      <Button v-if="hasOperationDetails(op)" variant="ghost" class="min-h-11 shrink-0 px-2 text-xs text-primary-accent" :aria-expanded="open" @click="open = !open">
        <ChevronDown class="size-3.5" :class="open ? 'rotate-180' : ''" />{{ open ? '收起详情' : '查看详情' }}
      </Button>
    </div>
    <div v-if="open" class="mt-2 min-w-0 space-y-3 rounded-lg border border-border bg-background/50 p-3">
      <template v-if="groups.length">
        <div class="grid min-w-0 gap-2 lg:grid-cols-2">
          <Select v-model="category"><SelectTrigger class="min-h-11 w-full" aria-label="变更内容分类"><SelectValue /></SelectTrigger>
            <SelectContent><SelectItem v-for="group in groups" :key="group.title" :value="group.title">{{ group.title }}（{{ group.rows.length }}）</SelectItem></SelectContent>
          </Select>
          <Input v-model="search" class="min-h-11" aria-label="搜索变更详情" placeholder="搜索地址、名称或字段…" />
        </div>
        <p v-if="group?.note" class="m-0 text-xs text-muted-foreground">{{ group.note }}</p>
        <div v-if="rows.length" class="max-h-80 overflow-auto rounded-md border border-border">
          <dl class="m-0 divide-y divide-border">
            <div v-for="(row, index) in pageRows" :key="index" class="grid min-w-0 gap-1 px-3 py-2.5 text-xs sm:grid-cols-[minmax(6rem,1fr)_minmax(0,3fr)] sm:gap-3">
              <dt class="min-w-0 break-words" :class="row.action === '新增' ? 'text-success-accent' : row.action === '移除' ? 'text-destructive-accent' : 'text-muted-foreground'">{{ row.label }}</dt>
              <dd class="m-0 min-w-0 whitespace-pre-wrap break-all font-mono leading-relaxed">
                <template v-if="row.before !== undefined"><span class="block text-muted-foreground">原：{{ row.before }}</span><span class="block">新：{{ row.value }}</span></template>
                <template v-else>{{ row.value }}</template>
              </dd>
            </div>
          </dl>
        </div>
        <p v-else class="m-0 py-3 text-sm text-muted-foreground">没有匹配的变更内容。</p>
        <div class="flex flex-wrap items-center justify-end gap-2 text-xs text-muted-foreground">
          <span class="num">{{ rows.length }} 项<span v-if="pages > 1"> · {{ page }} / {{ pages }} 页</span></span>
          <template v-if="pages > 1"><Button variant="outline" class="min-h-11" :disabled="page === 1" @click="page--">上一页</Button><Button variant="outline" class="min-h-11" :disabled="page === pages" @click="page++">下一页</Button></template>
        </div>
      </template>
      <p v-else class="m-0 text-sm text-muted-foreground">本次计划未提供更多字段。</p>
    </div>
  </div>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ChevronDown } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { hasOperationDetails, operationDetails, type RouterPlanOperation } from '@/lib/routerOperationDetails'
const props = defineProps<{ op: RouterPlanOperation }>()
const open = ref(false), category = ref(''), search = ref(''), page = ref(1)
// Large bundles are formatted only on demand, and only 50 rows are mounted.
const groups = computed(() => open.value ? operationDetails(props.op) : [])
const group = computed(() => groups.value.find(item => item.title === category.value) || groups.value[0])
const rows = computed(() => (group.value?.rows || []).filter(row => `${row.label} ${row.value} ${row.before || ''}`.toLowerCase().includes(search.value.trim().toLowerCase())))
const pages = computed(() => Math.max(1, Math.ceil(rows.value.length / 50)))
const pageRows = computed(() => rows.value.slice((page.value - 1) * 50, page.value * 50))
watch(groups, values => { if (!values.some(item => item.title === category.value)) category.value = values[0]?.title || '' })
watch([category, search], () => { page.value = 1 })
watch(() => props.op, () => { open.value = false; category.value = ''; search.value = ''; page.value = 1 })
</script>
