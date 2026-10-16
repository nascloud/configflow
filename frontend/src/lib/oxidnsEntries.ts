import { dump, JSON_SCHEMA, load } from 'js-yaml'

export type EntryKind = 'upstreams' | 'strings' | 'numbers' | 'lines'
export type EntryValue = string | number | Record<string, any>

export function assertPlainData(value: unknown, ancestors = new Set<object>(), depth = 0): void {
  if (depth > 40) throw new Error('配置嵌套过深，请减少嵌套层级。')
  if (value === null || ['string', 'boolean'].includes(typeof value)) return
  if (typeof value === 'number' && Number.isFinite(value)) return
  if (typeof value !== 'object' || !value) throw new Error('配置包含不支持的值。')
  if (ancestors.has(value)) throw new Error('配置不能包含循环引用。')
  if (!Array.isArray(value) && Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) throw new Error('配置必须由普通对象和列表组成。')
  ancestors.add(value)
  for (const [key, item] of Object.entries(value)) {
    if (['__proto__', 'constructor', 'prototype'].includes(key)) throw new Error('配置包含不支持的字段名称。')
    assertPlainData(item, ancestors, depth + 1)
  }
  ancestors.delete(value)
}

export const entryYaml = (value: unknown) => dump(value, { schema: JSON_SCHEMA, noRefs: true, lineWidth: -1 })

export function validateEntries(value: unknown, kind: EntryKind): asserts value is EntryValue[] {
  assertPlainData(value)
  if (!Array.isArray(value)) throw new Error('请填写 YAML 列表。')
  for (const [index, item] of value.entries()) {
    if (kind === 'upstreams') {
      const address = typeof item === 'string' ? item : item && typeof item === 'object' && !Array.isArray(item) ? item.addr : undefined
      if (typeof address !== 'string' || !address.trim()) throw new Error(`第 ${index + 1} 个上游需要填写 addr 地址。`)
    } else if (kind === 'numbers') {
      if (typeof item !== 'number' || !Number.isInteger(item) || item < 1 || item > 65535) throw new Error(`第 ${index + 1} 项必须是 1–65535 的整数。`)
    } else if (typeof item !== 'string' || (kind === 'strings' && !item.trim())) {
      throw new Error(`第 ${index + 1} 项需要填写文本。`)
    }
  }
}

export function parseUpstreams(text: string): EntryValue[] {
  if (!text.trim()) return []
  let parsed: unknown
  try { parsed = load(text, { schema: JSON_SCHEMA }) } catch {
    if (/^\s*(?:-\s|\{|addr\s*:)/m.test(text) || (text.trim().startsWith('[') && !/^\[[0-9a-fA-F:]+\](?::\d+)?$/.test(text.trim()))) throw new Error('上游 YAML 格式有误，请修正后再切换。')
  }
  if (parsed && typeof parsed === 'object') {
    const entries = Array.isArray(parsed) ? parsed : [parsed]
    validateEntries(entries, 'upstreams')
    return entries
  }
  const entries = text.split('\n').filter(line => line.trim() && !line.trim().startsWith('#')).map(line => {
    const [addr, ...parts] = line.trim().split(/\s+/)
    if (/^[-{}]/.test(addr)) throw new Error('上游 YAML 格式有误，请修正后再切换。')
    const entry: Record<string, any> = { addr }
    parts.forEach((part, index) => {
      if (part.startsWith('#')) throw new Error('请把上游注释放在独立行，或使用 YAML。')
      if (!part.includes('=') && index === 0) entry.bootstrap = part
      else if (part.startsWith('bootstrap=')) entry.bootstrap = part.slice(10)
      else if (/^enable_pipeline=(true|false|yes|no|1|0)$/i.test(part)) entry.enable_pipeline = /^(true|yes|1)$/i.test(part.split('=')[1])
      else throw new Error('复杂上游参数请使用包含 addr 的 YAML 对象。')
    })
    return entry
  })
  validateEntries(entries, 'upstreams')
  return entries
}

export function parseEntryModel(value: unknown, kind: EntryKind): EntryValue[] {
  if (kind === 'upstreams') return parseUpstreams(String(value ?? ''))
  const entries = kind === 'lines' ? (value ? String(value).split('\n') : []) : (value ?? [])
  validateEntries(entries, kind)
  return JSON.parse(JSON.stringify(entries))
}

export function parseEntryYaml(text: string, kind: EntryKind): EntryValue[] {
  const value = load(text, { schema: JSON_SCHEMA }) ?? []
  validateEntries(value, kind)
  return value
}

export function encodeEntries(entries: EntryValue[], kind: EntryKind): string | EntryValue[] {
  validateEntries(entries, kind)
  if (kind === 'upstreams') return entries.length ? entryYaml(entries) : ''
  if (kind === 'lines') return entries.join('\n')
  return JSON.parse(JSON.stringify(entries))
}
