import { afterEach, describe, expect, it } from 'vitest'
import { defineComponent, ref } from 'vue'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { JSON_SCHEMA, load } from 'js-yaml'
import OxidnsFilesEditor from '@/components/generate/OxidnsFilesEditor.vue'

const wrappers: VueWrapper[] = []
afterEach(() => wrappers.splice(0).forEach(wrapper => wrapper.unmount()))
function render(value: any[] = []) {
  const wrapper = mount(defineComponent({
    components: { OxidnsFilesEditor },
    setup: () => ({ value: ref(value), error: ref('') }),
    template: '<OxidnsFilesEditor v-model="value" @error="error = $event" />',
  }))
  wrappers.push(wrapper)
  return wrapper
}
async function click(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll('button').find(button => button.text().trim() === text)
  expect(button, text).toBeDefined()
  await button!.trigger('click')
  await flushPromises()
}
async function upload(wrapper: VueWrapper, files: any[]) {
  const control = wrapper.get('[aria-label="上传附加文件"]')
  Object.defineProperty(control.element, 'files', { configurable: true, value: files })
  await control.trigger('change')
  await flushPromises()
}
const file = (name: string, bytes: number[]) => ({ name, size: bytes.length, arrayBuffer: async () => new Uint8Array(bytes).buffer })

describe('OxiDNS managed file editor', () => {
  it('preserves YAML drafts with a reactive v-model and never replaces invalid edits with old files', async () => {
    const wrapper = render([{ path: 'rules/a.txt', encoding: 'text', content: '' }])
    await click(wrapper, 'YAML')
    const draft = '# keep formatting until a form edit\n- path: rules/a.txt\n  encoding: text\n  content: "false\\n0\\n"\n'
    await wrapper.get('[aria-label="附加文件 YAML"]').setValue(draft)
    expect((wrapper.get('[aria-label="附加文件 YAML"]').element as HTMLTextAreaElement).value).toBe(draft)
    expect((wrapper.vm as any).value).toEqual(load(draft, { schema: JSON_SCHEMA }))
    await wrapper.get('[aria-label="附加文件 YAML"]').setValue('- path: [broken')
    expect((wrapper.vm as any).error).not.toBe('')
    await click(wrapper, '文件')
    expect((wrapper.get('[aria-label="附加文件 YAML"]').element as HTMLTextAreaElement).value).toBe('- path: [broken')
    expect((wrapper.vm as any).value).toEqual(load(draft, { schema: JSON_SCHEMA }))
    await wrapper.get('[aria-label="附加文件 YAML"]').setValue('[]')
    await click(wrapper, '文件')
    expect((wrapper.vm as any).value).toEqual([])
    expect((wrapper.vm as any).error).toBe('')
  })

  it('retains duplicate/incomplete form entries and permits repairing them in YAML', async () => {
    const wrapper = render([{ path: 'rules/a.txt', encoding: 'text', content: 'a' }])
    await click(wrapper, '添加')
    expect((wrapper.vm as any).error).toContain('路径')
    await wrapper.get('[aria-label="文件 2 路径"]').setValue('./rules/a.txt')
    expect((wrapper.vm as any).error).toContain('重复')
    await click(wrapper, 'YAML')
    expect((wrapper.get('[aria-label="附加文件 YAML"]').element as HTMLTextAreaElement).value).toContain('./rules/a.txt')
    await wrapper.get('[aria-label="附加文件 YAML"]').setValue('- {path: rules/a.txt, encoding: text, content: a}\n- {path: rules/b.txt, encoding: text, content: ""}')
    expect((wrapper.vm as any).error).toBe('')
    await click(wrapper, '文件')
    await wrapper.get('[aria-label="删除文件 1"]').trigger('click')
    expect((wrapper.vm as any).value).toEqual([{ path: 'rules/b.txt', encoding: 'text', content: '' }])
  })

  it.each([
    [{ path: '../escape', encoding: 'text', content: '' }, '相对路径'],
    [{ path: 'state/cache.dump', encoding: 'text', content: '' }, '运行数据'],
    [{ path: 'a\nfile', encoding: 'text', content: '' }, '相对路径'],
    [{ path: 'a.bin', encoding: 'base64', content: 'broken%%' }, 'Base64'],
  ])('blocks invalid file metadata %j', (entry, message) => {
    const wrapper = render([entry])
    expect((wrapper.vm as any).error).toContain(message)
  })

  it('detects file/directory conflicts and limits text by UTF-8 bytes', () => {
    const conflict = render([{ path: 'rules', encoding: 'text', content: '' }, { path: 'rules/a.txt', encoding: 'text', content: '' }])
    expect((conflict.vm as any).error).toContain('冲突')
    const oversized = render([{ path: 'large.txt', encoding: 'text', content: '字'.repeat(6 * 1024 * 1024) }])
    expect((oversized.vm as any).error).toContain('16 MiB')
  })

  it('uploads UTF-8 with a BOM and arbitrary binary bytes without data loss', async () => {
    const wrapper = render()
    await upload(wrapper, [file('rules.txt', [0xef, 0xbb, 0xbf, 0x61, 0x0a]), file('image.bin', [0, 0xff, 0x80, 1])])
    expect((wrapper.vm as any).value).toEqual([
      { path: 'rules.txt', content: '\ufeffa\n', encoding: 'text' },
      { path: 'image.bin', content: 'AP+AAQ==', encoding: 'base64' },
    ])
    expect((wrapper.vm as any).error).toBe('')
  })

  it('leaves existing files saveable after a rejected upload and avoids partial additions', async () => {
    const original = [{ path: 'present.txt', encoding: 'text', content: 'keep' }]
    const wrapper = render(original)
    await upload(wrapper, [file('new.txt', [0x61]), file('present.txt', [0x62])])
    expect((wrapper.vm as any).value).toEqual(original)
    expect(wrapper.get('[role="alert"]').text()).toContain('重复')
    expect((wrapper.vm as any).error).toBe('')
    await click(wrapper, 'YAML')
    expect(wrapper.find('[aria-label="附加文件 YAML"]').exists()).toBe(true)
  })

  it('blocks saving while reading and cancels stale uploads after profile/model replacement', async () => {
    const wrapper = render()
    let finish!: (value: ArrayBuffer) => void
    const pending = new Promise<ArrayBuffer>(resolve => { finish = resolve })
    await upload(wrapper, [{ name: 'slow.txt', size: 1, arrayBuffer: () => pending }])
    expect((wrapper.vm as any).error).toContain('正在读取')
    const replacement = [{ path: 'next.txt', encoding: 'text', content: 'next profile' }]
    ;(wrapper.vm as any).value = replacement
    await flushPromises()
    finish(new Uint8Array([0x61]).buffer)
    await flushPromises()
    expect((wrapper.vm as any).value).toEqual(replacement)
    expect((wrapper.vm as any).error).toBe('')
  })
})
