import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import OxidnsConfigNavigation, { type OxidnsNavigationItem } from '@/components/generate/OxidnsConfigNavigation.vue'

function fixture(): OxidnsNavigationItem[] {
  return [
    {
      id: 'globals', label: '全局参数', kind: 'section', children: [
        {
          id: 'api', label: '管理 API', code: 'api', children: [
            {
              id: 'http', label: 'HTTP 服务', code: 'api.http', children: [
                { id: 'auth', label: '身份认证', code: 'api.http.auth' },
                { id: 'webui', label: 'WebUI', code: 'api.http.webui' },
              ],
            },
          ],
        },
        { id: 'log', label: '日志', code: 'log', children: [{ id: 'rotation', label: '日志轮转', code: 'log.rotation' }] },
      ],
    },
    { id: 'plugins', label: '插件', kind: 'section', code: 'plugins', children: [{ id: 'forward', label: 'forward_local', code: 'plugins[1]' }] },
  ]
}
const button = (wrapper: ReturnType<typeof mount>, label: string) => wrapper.get(`button[aria-label="${label}"]`)

describe('OxiDNS configuration directory', () => {
  it('finds deep groups by label or native path while retaining and expanding ancestors', async () => {
    const wrapper = mount(OxidnsConfigNavigation, { props: { items: fixture(), selectedId: 'globals' } })
    expect(wrapper.find('[aria-label="编辑 身份认证（api.http.auth）"]').exists()).toBe(false)
    for (const query of ['身份认证', 'API.HTTP.AUTH']) {
      await wrapper.get('input').setValue(query)
      for (const label of ['全局参数', '管理 API（api）', 'HTTP 服务（api.http）', '身份认证（api.http.auth）']) {
        expect(button(wrapper, `编辑 ${label}`).exists()).toBe(true)
      }
      expect(wrapper.find('[aria-label="编辑 日志（log）"]').exists()).toBe(false)
      expect(wrapper.find('[aria-label="编辑 WebUI（api.http.webui）"]').exists()).toBe(false)
    }
    await wrapper.get('input').setValue('找不到')
    expect(wrapper.get('[role="status"]').text()).toBe('没有匹配的配置分组')
    await button(wrapper, '清空目录搜索').trigger('click')
    expect(button(wrapper, '编辑 日志（log）').exists()).toBe(true)
    expect(wrapper.emitted('select')).toBeUndefined()
  })

  it('keeps choosing and expanding groups independent without mutating input configuration', async () => {
    const items = fixture()
    const before = structuredClone(items)
    const wrapper = mount(OxidnsConfigNavigation, { props: { items, selectedId: 'globals' } })
    await button(wrapper, '收起 管理 API（api）').trigger('click')
    expect(wrapper.find('[aria-label="编辑 HTTP 服务（api.http）"]').exists()).toBe(false)
    expect(wrapper.emitted('select')).toBeUndefined()
    await button(wrapper, '编辑 管理 API（api）').trigger('click')
    expect(wrapper.emitted('select')).toEqual([['api']])
    expect(wrapper.find('[aria-label="编辑 HTTP 服务（api.http）"]').exists()).toBe(false)
    await button(wrapper, '展开 管理 API（api）').trigger('click')
    expect(button(wrapper, '编辑 HTTP 服务（api.http）').exists()).toBe(true)
    expect(items).toEqual(before)
  })

  it('reveals the selected item and its full ancestor path when selection changes', async () => {
    const wrapper = mount(OxidnsConfigNavigation, { props: { items: fixture(), selectedId: 'globals' } })
    await button(wrapper, '收起 全局参数').trigger('click')
    await wrapper.setProps({ selectedId: 'auth' })
    expect(button(wrapper, '编辑 身份认证（api.http.auth）').attributes('aria-current')).toBe('true')
    expect(wrapper.get('[aria-label="当前配置位置"]').text()).toBe('当前位置：全局参数 / 管理 API / HTTP 服务 / 身份认证')
    expect(wrapper.findAll('[data-contains-selection]')).toHaveLength(3)
    expect(wrapper.find('[role="tree"]').exists()).toBe(false)
  })

  it('reflects renamed and removed entries and disables navigation when editing is unavailable', async () => {
    const items = fixture()
    const wrapper = mount(OxidnsConfigNavigation, { props: { items, selectedId: 'forward' } })
    items[1].children![0].label = 'forward_remote'
    await wrapper.setProps({ items: structuredClone(items) })
    expect(button(wrapper, '编辑 forward_remote（plugins[1]）').attributes('aria-current')).toBe('true')
    await wrapper.setProps({ disabled: true })
    expect(wrapper.findAll('button').every(node => node.attributes('disabled') !== undefined)).toBe(true)
    await button(wrapper, '编辑 插件（plugins）').trigger('click')
    expect(wrapper.emitted('select')).toBeUndefined()
    await wrapper.setProps({ items: [] })
    expect(wrapper.text()).toContain('暂无配置分组')
  })

  it('searches and selects a 24-level configuration with a long unbroken tag', async () => {
    const longTag = `forward_${'deep_custom_upstream_'.repeat(16)}`
    const labels = Array.from({ length: 23 }, (_, index) => `分组 ${index + 1}`)
    const codes = Array.from({ length: 24 }, (_, index) => `field_${index + 1}`)
    let root: OxidnsNavigationItem = { id: 'deep-target', label: longTag, code: codes.join('.') }
    for (let index = 22; index >= 0; index--) {
      root = { id: `group-${index}`, label: labels[index], code: codes.slice(0, index + 1).join('.'), children: [root] }
    }
    const items = [root]
    const before = structuredClone(items)
    const wrapper = mount(OxidnsConfigNavigation, { props: { items, selectedId: root.id } })
    await wrapper.get('input').setValue(longTag)
    const targetLabel = `编辑 ${longTag}（${codes.join('.')}）`
    await button(wrapper, targetLabel).trigger('click')
    expect(wrapper.emitted('select')).toEqual([['deep-target']])
    await wrapper.setProps({ selectedId: 'deep-target' })
    await button(wrapper, '清空目录搜索').trigger('click')
    expect(button(wrapper, targetLabel).attributes('aria-current')).toBe('true')
    expect(wrapper.get('[aria-label="当前配置位置"]').text()).toBe(`当前位置：${[...labels, longTag].join(' / ')}`)
    expect(items).toEqual(before)
  })

  it('shows an unconfigured capability as an add action and emits its identifier without changing the catalog', async () => {
    const items: OxidnsNavigationItem[] = [{
      id: 'caching', label: '缓存', kind: 'section', code: '1 已配置 · 1 未配置', children: [
        { id: 'cache-existing', label: 'lazy_cache', code: 'cache' },
        { id: 'add-reverse', label: '反向查询缓存', code: 'reverse_lookup', available: true },
      ],
    }]
    const before = structuredClone(items)
    const wrapper = mount(OxidnsConfigNavigation, { props: { items, selectedId: 'cache-existing' } })
    const add = button(wrapper, '添加 反向查询缓存（reverse_lookup）')
    expect(add.text()).toContain('未配置')
    expect(button(wrapper, '编辑 缓存（1 已配置 · 1 未配置）').text()).toContain('1 已配置 · 1 未配置')
    expect(button(wrapper, '编辑 lazy_cache（cache）').text()).not.toContain('未配置')
    expect(add.element.parentElement?.querySelector('svg.lucide-plus')).not.toBeNull()
    await add.trigger('click')
    expect(wrapper.emitted('select')).toEqual([['add-reverse']])
    expect(items).toEqual(before)
    wrapper.unmount()
  })

  it('honors collapsed categories while revealing a matching available capability by its explanation', async () => {
    const items: OxidnsNavigationItem[] = [{
      id: 'integrations', label: '特色功能', kind: 'section', defaultExpanded: false, children: [
        { id: 'add-router', label: 'RouterOS 地址列表', available: true, help: '将解析结果同步到路由器地址列表。' },
      ],
    }]
    const wrapper = mount(OxidnsConfigNavigation, { props: { items, selectedId: 'elsewhere' } })
    expect(button(wrapper, '展开 特色功能').attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('[aria-label="添加 RouterOS 地址列表"]').exists()).toBe(false)
    await wrapper.get('input').setValue('路由器')
    await button(wrapper, '添加 RouterOS 地址列表').trigger('click')
    expect(wrapper.emitted('select')).toEqual([['add-router']])
    await button(wrapper, '清空目录搜索').trigger('click')
    expect(wrapper.find('[aria-label="添加 RouterOS 地址列表"]').exists()).toBe(false)
    await button(wrapper, '展开 特色功能').trigger('click')
    expect(button(wrapper, '添加 RouterOS 地址列表').exists()).toBe(true)
    wrapper.unmount()
  })

  it('prevents adding disabled capabilities and respects a disabled parent or whole editor', async () => {
    const items: OxidnsNavigationItem[] = [
      { id: 'disabled-capability', label: '未开放功能', available: true, disabled: true },
      { id: 'enabled-capability', label: '可添加功能', available: true },
      { id: 'disabled-category', label: '禁用分类', disabled: true, children: [{ id: 'disabled-child', label: '子功能', available: true }] },
    ]
    const wrapper = mount(OxidnsConfigNavigation, { props: { items, selectedId: 'elsewhere' } })
    for (const label of ['添加 未开放功能', '编辑 禁用分类', '收起 禁用分类', '添加 子功能']) {
      expect(button(wrapper, label).attributes('disabled')).toBeDefined()
      await button(wrapper, label).trigger('click')
    }
    expect(wrapper.emitted('select')).toBeUndefined()
    expect(button(wrapper, '添加 可添加功能').attributes('disabled')).toBeUndefined()
    await wrapper.setProps({ disabled: true })
    await button(wrapper, '添加 可添加功能').trigger('click')
    expect(wrapper.emitted('select')).toBeUndefined()
    wrapper.unmount()
  })

  it('opens capability help without adding or selecting it and keeps help outside the action button', async () => {
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
    const wrapper = mount(OxidnsConfigNavigation, {
      props: { items: [{ id: 'add-cache', label: '缓存', available: true, help: '复用 DNS 查询结果，减少重复请求。' }], selectedId: 'elsewhere' },
      attachTo: document.body,
    })
    try {
      const help = button(wrapper, '缓存说明')
      expect(help.element.parentElement?.closest('button')).toBeNull()
      await help.trigger('click')
      await flushPromises()
      expect(document.querySelector('[data-field-help-content]')?.textContent).toContain('复用 DNS 查询结果，减少重复请求。')
      expect(wrapper.emitted('select')).toBeUndefined()
    } finally {
      wrapper.unmount()
      vi.unstubAllGlobals()
    }
  })
})
