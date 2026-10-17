<template>
  <div class="space-y-2">
    <StatusDot :tone="status.tone" :label="status.label" />
    <template v-if="dependency.lifecycle?.managed">
      <Segmented
        :model-value="mode"
        :options="options"
        label="Sub-Store 运行模式"
        class="flex w-fit max-w-full [&_button]:min-h-11 [&_button]:px-3"
        :disabled="saving || dependency.update.state === 'running' || dependency.lifecycle.state === 'updating'"
        @update:model-value="saveMode"
      />
      <p class="m-0 text-[12px] text-muted-foreground" aria-live="polite">
        {{ saving ? '正在保存…' : mode === 'on_demand' ? '需要转换时自动启动，空闲 5 分钟后休眠。首次使用稍有等待。' : '持续运行，转换时无需等待启动。' }}
      </p>
    </template>
    <p v-if="dependency.lifecycle?.error" class="m-0 text-[12px] text-destructive-accent" role="status">
      {{ dependency.lifecycle.error }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { isAxiosError } from 'axios'
import { dependenciesApi, type DependencyStatus } from '@/api'
import Segmented from '@/components/common/Segmented.vue'
import StatusDot from '@/components/common/StatusDot.vue'
import { notify } from '@/lib/feedback'

const props = defineProps<{ dependency: DependencyStatus }>()
const emit = defineEmits<{ (e: 'changed'): void }>()
type Mode = 'on_demand' | 'always'
const options: Array<{ value: Mode; label: string }> = [
  { value: 'on_demand', label: '按需启动' }, { value: 'always', label: '常驻运行' }
]
const mode = ref<Mode>('on_demand')
const saving = ref(false)
watch(() => props.dependency.lifecycle?.mode, value => { mode.value = value === 'always' ? 'always' : 'on_demand' }, { immediate: true })
const status = computed(() => {
  const state = props.dependency.lifecycle?.state || (props.dependency.running ? 'running' : 'failed')
  return {
    sleeping: { tone: 'muted', label: '休眠中' },
    starting: { tone: 'warning', label: '启动中' },
    updating: { tone: 'warning', label: '更新中' },
    running: { tone: 'success', label: '运行中' },
    failed: { tone: 'danger', label: '无法连接' }
  }[state] as { tone: 'muted' | 'warning' | 'success' | 'danger'; label: string }
})
async function saveMode(value: Mode) {
  if (saving.value || value === mode.value) return
  saving.value = true
  try {
    await dependenciesApi.setSubStoreMode(value)
    mode.value = value
    notify.success(value === 'on_demand' ? '已启用按需启动' : '已启用常驻运行')
    emit('changed')
  } catch (error) {
    notify.error(isAxiosError(error) ? error.response?.data?.message || '运行模式保存失败' : '运行模式保存失败')
  } finally {
    saving.value = false
  }
}
</script>
