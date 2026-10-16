import { afterEach, describe, expect, it } from 'vitest'
import { defineComponent, ref } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { load } from 'js-yaml'
import OxidnsEntryEditor from '@/components/generate/OxidnsEntryEditor.vue'

const wrappers: VueWrapper[] = []
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))

describe('extra upstream drafts', () => {
  it('keeps invalid extra drafts when address or common connection options change', async () => {
    const wrapper = mount(defineComponent({
      components: { OxidnsEntryEditor },
      setup: () => ({ value: ref('- addr: tls://dns.example\n  max_conns: 4'), error: ref('') }),
      template: '<OxidnsEntryEditor v-model="value" kind="upstreams" label="上游" id="upstreams" @error="error = $event" />',
    }))
    wrappers.push(wrapper)
    await wrapper.get('[aria-label="上游第 1 项上游参数"]').trigger('click')
    const extra = wrapper.get('[aria-label="上游第 1 项其他上游参数.max_conns 值"]')
    await extra.setValue('4e')
    expect((wrapper.vm as any).error).toContain('数字')
    await wrapper.get('[aria-label="上游第 1 项地址"]').setValue('tls://changed.example')
    await wrapper.get('[aria-label="上游第 1 项 bootstrap"]').setValue('223.5.5.5:53')
    expect((extra.element as HTMLInputElement).value).toBe('4e')
    expect((wrapper.vm as any).error).toContain('数字')
    await extra.setValue('8')
    await flushPromises()
    expect((wrapper.vm as any).error).toBe('')
    expect(load((wrapper.vm as any).value)).toEqual([{ addr: 'tls://changed.example', bootstrap: '223.5.5.5:53', max_conns: 8 }])
  })
})
