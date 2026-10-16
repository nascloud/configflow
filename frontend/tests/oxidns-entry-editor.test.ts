import { afterEach, describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import { load } from 'js-yaml'
import OxidnsEntryEditor from '@/components/generate/OxidnsEntryEditor.vue'
import { encodeEntries, entryYaml, parseEntryModel, parseEntryYaml, parseUpstreams } from '@/lib/oxidnsEntries'

const wrappers: ReturnType<typeof mount>[] = []
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()) })
const render = (value: unknown, kind = 'upstreams') => {
  const wrapper = mount(defineComponent({
    components: { OxidnsEntryEditor },
    setup: () => ({ value: ref(value), kind, valid: ref(true) }),
    template: '<OxidnsEntryEditor v-model="value" :kind="kind" label="测试列表" id="test-entries" @validity="valid = $event" />'
  }))
  wrappers.push(wrapper)
  return wrapper
}
const button = (wrapper: ReturnType<typeof mount>, text: string) => wrapper.findAll('button').find(item => item.text() === text)!

describe('OxiDNS entry serialization', () => {
  it('accepts legacy lines without changing protocol and preserves explicit false', () => {
    expect(parseUpstreams('223.5.5.5\ntls://dns.example.test bootstrap=192.0.2.1 enable_pipeline=false')).toEqual([
      { addr: '223.5.5.5' }, { addr: 'tls://dns.example.test', bootstrap: '192.0.2.1', enable_pipeline: false }
    ])
    expect(parseUpstreams('[2001:db8::53]:53')).toEqual([{ addr: '[2001:db8::53]:53' }])
  })
  it('preserves nested upstream options, arrays and unknown values across serialization', () => {
    const entries = parseUpstreams('- addr: https://dns.example.test/query?a=b\n  enable_pipeline: false\n  extra:\n    test: [1, false, null]\n- "udp://[2001:db8::53]:53"')
    expect(parseUpstreams(encodeEntries(entries, 'upstreams') as string)).toEqual(entries)
    expect(parseEntryYaml(entryYaml(entries), 'upstreams')).toEqual(entries)
    expect(encodeEntries([], 'upstreams')).toBe('')
  })
  it('keeps Hosts line contents, IPv6, comments, blanks and trailing newline', () => {
    const text = '# host comment\nfull:test.example 192.0.2.1 2001:db8::1\n\n'
    expect(encodeEntries(parseEntryYaml(entryYaml(parseEntryModel(text, 'lines')), 'lines'), 'lines')).toBe(text)
  })
  it.each(['- addr: [broken', '[broken', '- bootstrap: 192.0.2.1'])('rejects invalid upstream input %s', text => {
    expect(() => parseUpstreams(text)).toThrow()
  })
  it('rejects invalid numbers, object lists, cycles and unsafe properties', () => {
    for (const text of ['[0]', '[65536]', '["65"]', '[1.1]']) expect(() => parseEntryYaml(text, 'numbers')).toThrow()
    expect(() => parseEntryYaml('unexpected: mapping', 'strings')).toThrow()
    expect(() => parseEntryYaml('- &a {addr: x, loop: *a}', 'upstreams')).toThrow('循环')
    expect(() => parseEntryYaml('- {addr: x, __proto__: {bad: true}}', 'upstreams')).toThrow('字段')
  })
})

describe('OxiDNS entry editor', () => {
  it('adds, edits, reorders and deletes upstreams while preserving extensions', async () => {
    const wrapper = render('- addr: udp://192.0.2.1\n  extra: {nested: [1, false]}\n  enable_pipeline: false')
    await wrapper.get('input').setValue('udp://192.0.2.2')
    expect(load((wrapper.vm as any).value)).toEqual([{ addr: 'udp://192.0.2.2', extra: { nested: [1, false] }, enable_pipeline: false }])
    await button(wrapper, '添加上游').trigger('click')
    expect((wrapper.vm as any).valid).toBe(false)
    await wrapper.get('[aria-label="测试列表第 2 项地址"]').setValue('udp://192.0.2.3')
    expect((wrapper.vm as any).valid).toBe(true)
    await wrapper.get('[aria-label="上移测试列表第 2 项"]').trigger('click')
    expect((load((wrapper.vm as any).value) as any[])[0].addr).toBe('udp://192.0.2.3')
    await wrapper.get('[aria-label="删除测试列表第 1 项"]').trigger('click')
    expect((load((wrapper.vm as any).value) as any[])[0].extra.nested).toEqual([1, false])
  })
  it('keeps invalid YAML drafts and last valid model until repaired', async () => {
    const wrapper = render('udp://192.0.2.1')
    await button(wrapper, 'YAML').trigger('click')
    await wrapper.get('textarea').setValue('- addr: [broken')
    await button(wrapper, '条目').trigger('click')
    expect(wrapper.find('textarea').exists()).toBe(true)
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('- addr: [broken')
    expect((wrapper.vm as any).value).toBe('udp://192.0.2.1')
    expect((wrapper.vm as any).valid).toBe(false)
    await wrapper.get('textarea').setValue('- addr: udp://192.0.2.2\n  extra: false')
    await button(wrapper, '条目').trigger('click')
    expect(wrapper.find('textarea').exists()).toBe(false)
    expect(load((wrapper.vm as any).value)).toEqual([{ addr: 'udp://192.0.2.2', extra: false }])
  })
  it('edits reactive numeric arrays and clears them without converting to strings', async () => {
    const wrapper = render([65, 255], 'numbers')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    await wrapper.get('input').setValue('28')
    expect((wrapper.vm as any).value).toEqual([28, 255])
    await button(wrapper, 'YAML').trigger('click')
    await wrapper.get('textarea').setValue('[]')
    await flushPromises()
    expect((wrapper.vm as any).value).toEqual([])
    expect((wrapper.vm as any).valid).toBe(true)
  })
})
