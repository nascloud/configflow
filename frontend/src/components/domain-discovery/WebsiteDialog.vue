<template>
  <Dialog :open="!!domain" @update:open="value => { if (!value) emit('close') }">
    <DialogContent class="sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>网站信息</DialogTitle>
        <DialogDescription class="break-all">{{ domain }} · 服务归属不代表正在浏览的具体页面</DialogDescription>
      </DialogHeader>
      <div class="flex min-w-0 flex-col gap-4 text-[13px]">
        <div v-if="website?.service" class="rounded-md bg-muted p-3">
          <p class="font-medium">{{ website.service.name }} · {{ website.service.category }}</p>
          <p class="mt-1 text-xs text-muted-foreground">{{ website.service.evidence }}</p>
        </div>
        <div class="flex flex-col gap-2">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <p class="font-medium">网页自报信息</p>
            <Button size="sm" variant="outline" :disabled="busy || identifying" @click="identify">
              <Loader2 v-if="identifying || busy" class="size-3.5 animate-spin" />
              {{ website?.page ? '重新识别' : '识别网站' }}
            </Button>
          </div>
          <template v-if="website?.page">
            <p class="break-words">{{ website.page.name || website.page.reason }}</p>
            <p v-if="website.page.description" class="break-words text-muted-foreground">{{ website.page.description }}</p>
            <p class="break-all text-xs text-muted-foreground">
              探测子域 {{ website.page.host }} · {{ relativeTime(website.page.checked_at) }}
              <template v-if="website.page.final_host && website.page.final_host !== website.page.host"> · 跳转至 {{ website.page.final_host }}</template>
            </p>
          </template>
          <p v-else class="text-xs text-muted-foreground">尚未读取网页。API、资源域名或验证页可能无法识别。</p>
          <p class="text-xs text-muted-foreground">由 Agent 读取首页，结果缓存 30 天。需先在「地区限制」开启区域检测并部署配置。</p>
        </div>
        <div class="flex flex-col gap-2">
          <Label for="website-note">手动备注</Label>
          <Input id="website-note" v-model="note" maxlength="200" placeholder="例如：公司文档服务（最多 200 字）" :disabled="saving" @keydown.enter="saveNote" />
          <p class="text-xs text-muted-foreground">优先显示备注；留空保存可清除，不影响识别结果。</p>
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" @click="emit('close')">关闭</Button>
        <Button :disabled="saving || note === (website?.note || '')" @click="saveNote">
          <Loader2 v-if="saving" class="size-3.5 animate-spin" />保存备注
        </Button>
      </DialogFooter>
    </DialogContent>
  </Dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { isAxiosError } from 'axios'
import { Loader2 } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import api from '@/api'
import { notify } from '@/lib/feedback'
import { relativeTime } from '@/lib/format'

export interface WebsiteInfo {
  service: { name: string; category: string; evidence: string } | null
  page: { name: string; description: string; reason: string; host: string; final_host?: string; checked_at: string } | null
  note: string
}

const props = defineProps<{ domain: string; website: WebsiteInfo | null; busy: boolean }>()
const emit = defineEmits<{
  close: []
  saved: [domain: string, note: string]
  job: [job: { id: string; kind: 'probe' | 'region' | 'website'; status: 'running' | 'done' | 'failed';
    total: number; done: number; errors: Array<{ message: string }>; message: string }]
}>()
const note = ref('')
const saving = ref(false)
const identifying = ref(false)
watch(() => props.domain, () => { note.value = props.website?.note || '' }, { immediate: true })

const message = (error: unknown, fallback: string) =>
  isAxiosError<{ message?: string }>(error) ? error.response?.data?.message || fallback : fallback

async function identify() {
  if (identifying.value || props.busy) return
  identifying.value = true
  try {
    const { data } = await api.post('/domain-discovery/website/identify', { domains: [props.domain] })
    emit('job', data.job)
  } catch (error) {
    if (isAxiosError(error) && error.response?.status === 409 && error.response.data?.job) emit('job', error.response.data.job)
    notify.error(message(error, '无法开始网站识别'))
  } finally {
    identifying.value = false
  }
}

async function saveNote() {
  if (saving.value) return
  const domain = props.domain
  saving.value = true
  try {
    const { data } = await api.put('/domain-discovery/website/note', { domain, note: note.value })
    emit('saved', domain, data.note)
    if (props.domain === domain) note.value = data.note
    notify.success(data.note ? '备注已保存' : '备注已清除')
  } catch (error) {
    notify.error(message(error, '保存备注失败'))
  } finally {
    saving.value = false
  }
}
</script>
