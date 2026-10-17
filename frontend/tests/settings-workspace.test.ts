import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import SettingsWorkspace from '@/components/generate/SettingsWorkspace.vue'
import ConfirmHost from '@/components/feedback/ConfirmHost.vue'
import { confirmState, settleConfirm } from '@/lib/feedback'

const wrappers: ReturnType<typeof mount>[] = []
let media: MediaQueryList
let mediaListeners: Set<(event: MediaQueryListEvent) => void>

beforeEach(() => {
  mediaListeners = new Set()
  media = {
    matches: false,
    media: '(max-width: 1023px), (max-height: 760px)',
    addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => mediaListeners.add(listener),
    removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => mediaListeners.delete(listener),
  } as unknown as MediaQueryList
  vi.stubGlobal('matchMedia', () => media)
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})

afterEach(() => {
  if (confirmState.open) settleConfirm('cancel')
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

async function settleUi() {
  await flushPromises()
  await new Promise(resolve => setTimeout(resolve, 25))
  await flushPromises()
}

function button(label: string): HTMLButtonElement {
  const buttons = [...document.querySelectorAll<HTMLButtonElement>('button')]
  const result = buttons.find(item => item.getAttribute('aria-label') === label || item.textContent?.trim() === label)
  if (!result) throw new Error(`Missing button: ${label}`)
  return result
}

async function click(label: string) {
  button(label).click()
  await settleUi()
}

async function escape() {
  document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
  await settleUi()
}

async function render({ dirty = false, busy = false, description = '', compact = false } = {}) {
  const wrapper = mount(defineComponent({
    components: { SettingsWorkspace, ConfirmHost },
    setup: () => ({ open: ref(false), dirty: ref(dirty), busy: ref(busy), draft: ref('draft'), saveCount: ref(0), description, compact }),
    // Keep ConfirmHost before the workspace, matching the trickier portal ordering case.
    template: `<ConfirmHost />
      <button id="settings-opener" @click="open = true">打开设置</button>
      <SettingsWorkspace v-model:open="open" title="软件基础设置" :description="description" :dirty="dirty" :busy="busy" :size="compact ? 'compact' : 'wide'" @save="saveCount++">
        <input aria-label="配置草稿" v-model="draft" />
        <template #status>校验通过</template>
      </SettingsWorkspace>`,
  }), { attachTo: document.body })
  wrappers.push(wrapper)
  button('打开设置').focus()
  await click('打开设置')
  return wrapper
}

describe('SettingsWorkspace', () => {
  it('keeps the same editor and draft when expanding, restoring, or entering small-screen mode', async () => {
    await render()
    const editor = document.querySelector<HTMLInputElement>('input')!
    editor.value = 'unsaved YAML'
    editor.dispatchEvent(new Event('input', { bubbles: true }))
    const dialog = document.querySelector<HTMLElement>('[data-settings-workspace]')!
    expect(dialog.dataset.fullscreen).toBe('false')
    await click('全屏编辑')
    expect(dialog.dataset.fullscreen).toBe('true')
    expect(document.querySelector('input')).toBe(editor)
    await click('还原窗口')
    expect(dialog.dataset.fullscreen).toBe('false')

    Object.defineProperty(media, 'matches', { value: true, configurable: true })
    mediaListeners.forEach(listener => listener({ matches: true } as MediaQueryListEvent))
    await settleUi()
    expect(dialog.dataset.fullscreen).toBe('true')
    expect(document.querySelector('[aria-label="全屏编辑"]')).toBeNull()
    expect(document.querySelector('input')).toBe(editor)
    expect(editor.value).toBe('unsaved YAML')
  })

  it('closes a clean editor without confirmation and returns focus to its opener', async () => {
    const wrapper = await render()
    await click('返回生成工作台')
    expect((wrapper.vm as any).open).toBe(false)
    expect(confirmState.open).toBe(false)
    expect(document.activeElement?.id).toBe('settings-opener')
  })

  it('opens the shared confirmation above the editor and keeps the draft when cancelled', async () => {
    const wrapper = await render({ dirty: true })
    await escape()
    expect(confirmState.open).toBe(true)
    expect((wrapper.vm as any).open).toBe(true)
    const dialog = document.querySelector('[data-settings-workspace]')!
    const confirmation = document.querySelector('[data-slot="alert-dialog-content"]')!
    // The shared host can precede this editor in portal order; the workspace's
    // scoped layering rule handles that case (also checked in browser QA).
    expect(dialog.compareDocumentPosition(confirmation) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy()
    expect(confirmation.contains(document.activeElement)).toBe(true)
    const resolver = confirmState.resolve
    button('返回生成工作台').click()
    await settleUi()
    expect(confirmState.resolve).toBe(resolver)
    await click('继续编辑')
    expect((wrapper.vm as any).open).toBe(true)
    expect(document.querySelector<HTMLInputElement>('input')?.value).toBe('draft')
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it('discards only after confirmation and restores focus after both dialogs close', async () => {
    const wrapper = await render({ dirty: true })
    await click('返回生成工作台')
    expect((wrapper.vm as any).open).toBe(true)
    await click('放弃修改')
    expect((wrapper.vm as any).open).toBe(false)
    expect(document.querySelector('[data-settings-workspace]')).toBeNull()
    expect(confirmState.open).toBe(false)
    expect(document.activeElement?.id).toBe('settings-opener')
  })

  it('protects drafts on outside clicks and blocks Escape, close, and save while busy', async () => {
    const wrapper = await render({ dirty: true })
    const overlay = document.querySelector('[data-slot="dialog-overlay"]')!
    overlay.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, button: 0, cancelable: true }))
    await settleUi()
    expect(confirmState.open).toBe(true)
    await click('继续编辑')
    ;(wrapper.vm as any).busy = true
    await settleUi()
    expect(button('返回生成工作台').disabled).toBe(true)
    expect(button('保存中…').disabled).toBe(true)
    await escape()
    expect((wrapper.vm as any).open).toBe(true)
    expect(confirmState.open).toBe(false)
  })

  it('emits save once and accepts parent-driven close after saving without a discard prompt', async () => {
    const wrapper = await render({ dirty: true })
    await click('保存')
    expect((wrapper.vm as any).saveCount).toBe(1)
    ;(wrapper.vm as any).open = false
    await settleUi()
    expect(document.querySelector('[data-settings-workspace]')).toBeNull()
    expect(confirmState.open).toBe(false)
  })

  it('dismisses the help first without closing the editor or opening a discard prompt', async () => {
    const wrapper = await render({ dirty: true, description: '软件设置说明' })
    await click('软件基础设置说明')
    expect(document.querySelector('[data-field-help-content]')).not.toBeNull()
    await escape()
    expect(document.querySelector('[data-field-help-content]')).toBeNull()
    expect((wrapper.vm as any).open).toBe(true)
    expect(confirmState.open).toBe(false)
  })

  it('warns about browser unload only while an unsaved editor is open', async () => {
    const wrapper = await render({ dirty: true, compact: true })
    expect((document.querySelector('[data-settings-workspace]') as HTMLElement).style.height).toBe('auto')
    const dirtyUnload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(dirtyUnload)
    expect(dirtyUnload.defaultPrevented).toBe(true)
    ;(wrapper.vm as any).dirty = false
    await settleUi()
    const cleanUnload = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(cleanUnload)
    expect(cleanUnload.defaultPrevented).toBe(false)
  })

  it('guards actual route changes, reuses an active discard decision, and blocks navigation while saving', async () => {
    const busy = ref(false)
    const page = defineComponent({
      components: { SettingsWorkspace },
      setup: () => ({ open: ref(true), busy }),
      template: '<SettingsWorkspace v-model:open="open" title="配置编辑" dirty :busy="busy"><input value="route draft" /></SettingsWorkspace>',
    })
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/settings', component: page },
      { path: '/other', component: { template: '<p>其他页面</p>' } },
    ] })
    await router.push('/other')
    await router.push('/settings')
    const wrapper = mount(defineComponent({
      components: { ConfirmHost, RouterView },
      template: '<ConfirmHost /><RouterView />',
    }), { attachTo: document.body, global: { plugins: [router] } })
    wrappers.push(wrapper)
    await settleUi()

    // Browser Back must not silently abandon the draft.
    router.back()
    await settleUi()
    expect(confirmState.open).toBe(true)
    expect(router.currentRoute.value.path).toBe('/settings')
    await click('继续编辑')
    expect(router.currentRoute.value.path).toBe('/settings')

    busy.value = true
    await router.push('/other')
    expect(router.currentRoute.value.path).toBe('/settings')
    expect(confirmState.open).toBe(false)

    busy.value = false
    await settleUi()
    await click('返回生成工作台')
    const resolver = confirmState.resolve
    const leaving = router.push('/other')
    await settleUi()
    expect(confirmState.resolve).toBe(resolver)
    await click('放弃修改')
    await leaving
    expect(router.currentRoute.value.path).toBe('/other')
    expect(document.querySelector('[data-settings-workspace]')).toBeNull()
  })
})
