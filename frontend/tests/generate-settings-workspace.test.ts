import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { nextTick } from 'vue'
import api, { customConfigApi, proxyGroupApi, ruleApi, ruleSetApi } from '@/api'
import Generate from '@/views/Generate.vue'
import GenerateStudio from '@/components/generate/GenerateStudio.vue'
import SettingsWorkspace from '@/components/generate/SettingsWorkspace.vue'
import { notify } from '@/lib/feedback'
import { scopedRequests, setActiveProfileId } from '@/profileContext'

vi.mock('@/router', () => ({ default: { push: vi.fn() } }))

const originalAdapter = api.defaults.adapter
const wrappers: ReturnType<typeof shallowMount>[] = []
const initialConfig = '# Keep this comment\nmixed-port: 7890\n'

beforeEach(() => {
  setActiveProfileId('workspace-profile')
  scopedRequests.value = 0
  api.defaults.adapter = async config => ({ config, data: {}, status: 200, statusText: 'OK', headers: {} })
  for (const method of ['getMihomo', 'getSurge', 'getLoon', 'getMosdns'] as const) {
    vi.spyOn(customConfigApi, method).mockResolvedValue({ data: { config: initialConfig, smart_groups: [{ group_id: 'group-1', policy_priority: 'HK:0' }] } } as any)
  }
  for (const method of ['saveMihomo', 'saveSurge', 'saveLoon', 'saveMosdns'] as const) {
    vi.spyOn(customConfigApi, method).mockResolvedValue({ data: { success: true } } as any)
  }
  vi.spyOn(ruleApi, 'getAll').mockResolvedValue({ data: [] } as any)
  vi.spyOn(ruleSetApi, 'getAll').mockResolvedValue({ data: [] } as any)
  vi.spyOn(proxyGroupApi, 'getAll').mockResolvedValue({ data: [{ id: 'group-1', name: 'Group 1', type: 'url-test' }] } as any)
  vi.spyOn(notify, 'error').mockImplementation(() => 0)
  vi.spyOn(notify, 'success').mockImplementation(() => 0)
})

afterEach(() => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  api.defaults.adapter = originalAdapter
  scopedRequests.value = 0
  sessionStorage.clear()
  localStorage.clear()
  vi.restoreAllMocks()
})

async function openSettings(target: string, label: string) {
  const wrapper = shallowMount(Generate)
  wrappers.push(wrapper)
  await flushPromises()
  const action = wrapper.getComponent(GenerateStudio).props('targets')
    .find((item: any) => item.key === target)!.actions.find((item: any) => item.label === label)!
  await action.run()
  await flushPromises()
  const workspace = wrapper.findAllComponents(SettingsWorkspace).find(item => item.props('open'))!
  expect(workspace).toBeDefined()
  return { wrapper, workspace, draft: wrapper.vm as any }
}

describe('generation settings workspace integration', () => {
  it.each([
    ['mihomo', 'saveMihomo'], ['surge', 'saveSurge'], ['loon', 'saveLoon']
  ] as const)('%s keeps configuration text intact and restores clean state after reverting', async (target, saveMethod) => {
    const { workspace, draft } = await openSettings(target, '基础配置')
    expect(workspace.props()).toMatchObject({ size: 'wide', dirty: false, busy: false })
    draft.customConfigContent = `${initialConfig}\n# Edited comment\n`
    await nextTick()
    expect(workspace.props('dirty')).toBe(true)
    draft.customConfigContent = initialConfig
    await nextTick()
    expect(workspace.props('dirty')).toBe(false)
    const edited = '  # Keep whitespace\n\ncustom: value\n'
    draft.customConfigContent = edited
    await nextTick()
    workspace.vm.$emit('save')
    await flushPromises()
    expect(customConfigApi[saveMethod]).toHaveBeenCalledExactlyOnceWith({ config: edited }, 'workspace-profile')
    expect(workspace.props('open')).toBe(false)
  })

  it('keeps the basic draft open and dirty after a save failure, then permits retry', async () => {
    const { workspace, draft } = await openSettings('mihomo', '基础配置')
    vi.mocked(customConfigApi.saveMihomo).mockRejectedValueOnce(new Error('network unavailable'))
    draft.customConfigContent = ''
    await nextTick()
    workspace.vm.$emit('save')
    await flushPromises()
    expect(workspace.props()).toMatchObject({ open: true, dirty: true, busy: false })
    expect(draft.customConfigContent).toBe('')
    expect(notify.error).toHaveBeenCalledWith('保存基础配置失败')
    workspace.vm.$emit('save')
    await flushPromises()
    expect(customConfigApi.saveMihomo).toHaveBeenLastCalledWith({ config: '' }, 'workspace-profile')
    expect(workspace.props('open')).toBe(false)
  })

  it('tracks every MosDNS draft section without treating tab navigation as an edit', async () => {
    const { workspace, draft } = await openSettings('mosdns', '设置')
    expect(workspace.props()).toMatchObject({ size: 'form', dirty: false })
    draft.mosdnsActiveTab = 'cache'
    await nextTick()
    expect(workspace.props('dirty')).toBe(false)
    const changes: Record<string, unknown> = {
      mosdnsDirectRulesets: ['direct-set'], mosdnsProxyRulesets: ['proxy-set'],
      mosdnsDirectRules: ['direct-rule'], mosdnsProxyRules: ['proxy-rule'],
      mosdnsCustomMatches: [{ id: 'match', enabled: true, matchesText: 'qname example.org', exec: 'reject 0' }],
      mosdnsCustomMatchPosition: 'head',
      mosdnsLocalDnsEntries: [{ id: 'local', mode: 'simple', addr: '223.5.5.5', bootstrap: '', enable_pipeline: false }],
      mosdnsRemoteDnsEntries: [{ id: 'remote', mode: 'yaml', yaml_config: '- addr: [' }],
      mosdnsFallbackDnsEntries: [{ id: 'fallback', mode: 'simple', addr: '1.1.1.1', bootstrap: '', enable_pipeline: true }],
      mosdnsDefaultForward: 'forward_local', mosdnsCustomHosts: 'example.org 127.0.0.1',
      mosdnsLogEnabled: false, mosdnsLogLevel: 'error', mosdnsLogFile: './edited.log',
      mosdnsApiEnabled: false, mosdnsApiAddress: '127.0.0.1:8339',
      mosdnsCacheEnabled: false, mosdnsCacheSize: 20480, mosdnsCacheLazyTtl: 1800,
      mosdnsCacheDumpEnabled: false, mosdnsCacheDumpFile: './edited.dump', mosdnsCacheDumpInterval: 600
    }
    for (const [field, value] of Object.entries(changes)) {
      const original = draft[field]
      draft[field] = value
      await nextTick()
      expect(workspace.props('dirty'), field).toBe(true)
      draft[field] = original
      await nextTick()
      expect(workspace.props('dirty'), `${field} reverted`).toBe(false)
    }
    expect(customConfigApi.saveMosdns).not.toHaveBeenCalled()
  })

  it('retains MosDNS drafts when any save stage fails and closes only after all stages succeed', async () => {
    const { workspace, draft } = await openSettings('mosdns', '设置')
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const post = vi.spyOn(api, 'post').mockImplementation(async (url: string) => {
      if (url === '/mosdns/cache-settings') throw new Error('save failed')
      return { data: { success: true } } as any
    })
    draft.mosdnsCustomHosts = 'example.org 127.0.0.1'
    await nextTick()
    workspace.vm.$emit('save')
    await flushPromises()
    expect(workspace.props()).toMatchObject({ open: true, dirty: true, busy: false })
    expect(draft.mosdnsCustomHosts).toBe('example.org 127.0.0.1')
    post.mockResolvedValue({ data: { success: true } } as any)
    workspace.vm.$emit('save')
    await flushPromises()
    expect(post).toHaveBeenCalledWith('/mosdns/dns-servers', expect.objectContaining({ custom_hosts: 'example.org 127.0.0.1' }), { headers: { 'X-ConfigFlow-Profile': 'workspace-profile' } })
    expect(workspace.props('open')).toBe(false)
  })

  it('keeps Smart edits and the existing filtered save payload', async () => {
    const { workspace, draft } = await openSettings('surge', 'Smart')
    expect(workspace.props()).toMatchObject({ size: 'compact', dirty: false })
    draft.surgeSmartGroups[0].policy_priority = 'US:1'
    await nextTick()
    expect(workspace.props('dirty')).toBe(true)
    draft.surgeSmartGroups[0].policy_priority = 'HK:0'
    await nextTick()
    expect(workspace.props('dirty')).toBe(false)
    draft.surgeSmartGroups.push({ group_id: '', policy_priority: '' })
    await nextTick()
    expect(workspace.props('dirty')).toBe(true)
    vi.mocked(customConfigApi.saveSurge).mockRejectedValueOnce(new Error('save failed'))
    workspace.vm.$emit('save')
    await flushPromises()
    expect(workspace.props()).toMatchObject({ open: true, dirty: true, busy: false })
    workspace.vm.$emit('save')
    await flushPromises()
    expect(customConfigApi.saveSurge).toHaveBeenLastCalledWith({ smart_groups: [{ group_id: 'group-1', policy_priority: 'HK:0' }] }, 'workspace-profile')
    expect(workspace.props('open')).toBe(false)
  })
})
