<script lang="ts">
export interface OxidnsNavigationItem {
  id: string
  label: string
  code?: string
  children?: OxidnsNavigationItem[]
  kind?: 'section' | 'entry'
  defaultExpanded?: boolean
  available?: boolean
  help?: string
  disabled?: boolean
}
</script>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Search, X } from '@lucide/vue'
import { Input } from '@/components/ui/input'
import OxidnsConfigNavigationItem from './OxidnsConfigNavigationItem.vue'

const props = defineProps<{
  items: OxidnsNavigationItem[]
  selectedId: string
  disabled?: boolean
}>()
const emit = defineEmits<{ select: [id: string] }>()

const search = ref('')
const expanded = ref(new Set<string>())
const knownIds = new Set<string>()
const query = computed(() => search.value.trim().toLocaleLowerCase())

function pathTo(items: OxidnsNavigationItem[], id: string): OxidnsNavigationItem[] {
  for (const item of items) {
    if (item.id === id) return [item]
    const childPath = pathTo(item.children || [], id)
    if (childPath.length) return [item, ...childPath]
  }
  return []
}
const selectedPath = computed(() => pathTo(props.items, props.selectedId))
const ancestorIds = computed(() => new Set(selectedPath.value.slice(0, -1).map(item => item.id)))
const currentPath = computed(() => selectedPath.value.map(item => item.label).join(' / '))

watch(() => props.items, items => {
  const currentIds = new Set<string>()
  const next = new Set(expanded.value)
  function visit(nodes: OxidnsNavigationItem[], depth: number) {
    for (const item of nodes) {
      currentIds.add(item.id)
      if (!knownIds.has(item.id) && depth < 2 && item.children?.length && item.defaultExpanded !== false) next.add(item.id)
      visit(item.children || [], depth + 1)
    }
  }
  visit(items, 0)
  for (const id of next) if (!currentIds.has(id)) next.delete(id)
  knownIds.clear()
  for (const id of currentIds) knownIds.add(id)
  expanded.value = next
}, { immediate: true, deep: true })

watch(() => selectedPath.value.map(item => item.id).join('\u0000'), () => {
  expanded.value = new Set([...expanded.value, ...ancestorIds.value])
}, { immediate: true })

function filterItems(items: OxidnsNavigationItem[]): OxidnsNavigationItem[] {
  return items.flatMap(item => {
    const ownMatch = `${item.label} ${item.code || ''} ${item.help || ''}`.toLocaleLowerCase().includes(query.value)
    const children = filterItems(item.children || [])
    if (!ownMatch && !children.length) return []
    return [{ ...item, children: ownMatch ? item.children : children }]
  })
}
const visibleItems = computed(() => query.value ? filterItems(props.items) : props.items)

function toggle(id: string) {
  if (props.disabled || query.value) return
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}
function select(id: string) {
  if (!props.disabled) emit('select', id)
}
</script>

<template>
  <nav aria-label="OxiDNS 配置目录" class="flex min-h-0 min-w-0 flex-col gap-3">
    <div class="relative shrink-0">
      <Search class="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
      <Input
        v-model="search"
        aria-label="搜索配置目录"
        placeholder="搜索名称或参数路径…"
        class="min-h-11 pl-8 pr-9 text-xs md:min-h-9 [@media(pointer:coarse)]:min-h-11"
        :disabled="disabled"
      />
      <button
        v-if="search"
        type="button"
        aria-label="清空目录搜索"
        class="absolute right-0 top-0 flex size-11 items-center justify-center rounded-md text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-primary-accent disabled:pointer-events-none disabled:opacity-50 md:size-9 [@media(pointer:coarse)]:size-11"
        :disabled="disabled"
        @click="search = ''"
      >
        <X class="size-3.5" aria-hidden="true" />
      </button>
    </div>
    <p v-if="currentPath" class="px-1 text-xs leading-relaxed text-muted-foreground [overflow-wrap:anywhere]" aria-label="当前配置位置">
      <span class="sr-only">当前位置：</span>{{ currentPath }}
    </p>
    <div class="min-h-0 overflow-y-auto overscroll-contain pr-1">
      <ul v-if="visibleItems.length" class="space-y-1">
        <OxidnsConfigNavigationItem
          v-for="item in visibleItems"
          :key="item.id"
          :item="item"
          :selected-id="selectedId"
          :ancestor-ids="ancestorIds"
          :expanded="expanded"
          :search-active="Boolean(query)"
          :disabled="disabled"
          @select="select"
          @toggle="toggle"
        />
      </ul>
      <p v-else role="status" class="px-2 py-5 text-center text-xs text-muted-foreground">
        {{ query ? '没有匹配的配置分组' : '暂无配置分组' }}
      </p>
    </div>
  </nav>
</template>
