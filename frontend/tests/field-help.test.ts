import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { DialogContent, DialogRoot, DialogTitle } from 'reka-ui'
import FieldHelp from '@/components/common/FieldHelp.vue'
import FormField from '@/components/common/FormField.vue'

const wrappers: ReturnType<typeof mount>[] = []
beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
})
afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  document.body.innerHTML = ''
  vi.unstubAllGlobals()
})

function render(component: any, props = {}) {
  const wrapper = mount(component, { props, attachTo: document.body })
  wrappers.push(wrapper)
  return wrapper
}

const visibleHelp = () => document.querySelector('[data-field-help-content]')

describe('FieldHelp interaction', () => {
  it('opens on focus, closes on blur, and toggles on touch without activating a field label', async () => {
    const wrapper = render(defineComponent({
      components: { FormField },
      setup: () => ({ parentClicks: ref(0) }),
      template: '<div @click="parentClicks++"><FormField label="测试开关" help="解释用途" html-for="test-toggle"><input id="test-toggle" type="checkbox" /></FormField></div>',
    }))
    const button = wrapper.get('button')
    expect(button.element.closest('label')).toBeNull()
    await button.trigger('focus')
    await flushPromises()
    expect(visibleHelp()?.textContent).toContain('解释用途')
    expect(button.attributes('aria-describedby')).toBeTruthy()
    await button.trigger('blur')
    await flushPromises()
    expect(visibleHelp()).toBeNull()

    await button.trigger('pointerdown', { pointerType: 'touch' })
    await button.trigger('focus')
    expect(visibleHelp()).toBeNull()
    await button.trigger('click')
    await flushPromises()
    expect(visibleHelp()).not.toBeNull()
    expect((wrapper.get('input').element as HTMLInputElement).checked).toBe(false)
    expect((wrapper.vm as any).parentClicks).toBe(0)
    await button.trigger('click')
    await flushPromises()
    expect(visibleHelp()).toBeNull()
  })

  it('opens on mouse hover and closes when pressing outside', async () => {
    const wrapper = render(FieldHelp, { label: '备用 DNS', text: '等待主上游后再启动备用。' })
    await wrapper.get('button').trigger('pointermove', { pointerType: 'mouse' })
    await new Promise(resolve => setTimeout(resolve, 275))
    await flushPromises()
    expect(visibleHelp()).not.toBeNull()
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await flushPromises()
    expect(visibleHelp()).toBeNull()
  })

  it('dismisses help before its enclosing dialog when Escape is pressed', async () => {
    const wrapper = render(defineComponent({
      components: { DialogRoot, DialogContent, DialogTitle, FieldHelp },
      setup: () => ({ dialogOpen: ref(true) }),
      template: '<DialogRoot v-model:open="dialogOpen"><DialogContent :aria-describedby="undefined" @open-auto-focus.prevent><DialogTitle>设置</DialogTitle><FieldHelp label="缓存" text="缓存说明" /></DialogContent></DialogRoot>',
    }))
    await flushPromises()
    await wrapper.get('button').trigger('click')
    await flushPromises()
    expect(visibleHelp()).not.toBeNull()
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await flushPromises()
    expect(visibleHelp()).toBeNull()
    expect((wrapper.vm as any).dialogOpen).toBe(true)
    wrapper.get('button').element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }))
    await flushPromises()
    expect((wrapper.vm as any).dialogOpen).toBe(false)
  })
})
