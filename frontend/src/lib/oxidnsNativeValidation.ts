/** Validation rules live on the server; the editor only presents their results. */
export interface OxidnsValidationIssue {
  path: Array<string | number>
  message: string
  code: string
  severity: 'error' | 'warning'
  source?: string
}

export interface OxidnsValidationResult {
  valid: boolean
  issues: OxidnsValidationIssue[]
  warnings?: OxidnsValidationIssue[]
}

/** Paths use zero-based indices, matching the validation API and YAML arrays. */
export function formatOxidnsIssuePath(path: Array<string | number>): string {
  return path.reduce<string>((text, part) => typeof part === 'number'
    ? `${text}[${part}]`
    : `${text}${text ? '.' : ''}${part}`, '') || '完整配置'
}

export function readOxidnsValidationResult(value: unknown): OxidnsValidationResult {
  if (!value || typeof value !== 'object') throw new Error('配置检查返回了无效结果，请重试。')
  const result = value as Partial<OxidnsValidationResult>
  if (typeof result.valid !== 'boolean' || !Array.isArray(result.issues)) throw new Error('配置检查返回了无效结果，请重试。')
  const issues = [...result.issues, ...(Array.isArray(result.warnings) ? result.warnings : [])]
  if (issues.some(issue => !issue || !Array.isArray(issue.path)
    || issue.path.some(part => typeof part !== 'string' && (typeof part !== 'number' || !Number.isInteger(part) || part < 0))
    || typeof issue.message !== 'string' || typeof issue.code !== 'string'
    || !['error', 'warning'].includes(issue.severity)
    || (issue.source !== undefined && typeof issue.source !== 'string'))) throw new Error('配置检查返回了无效结果，请重试。')
  const unique = issues.filter((issue, index) => issues.findIndex(other => JSON.stringify(other) === JSON.stringify(issue)) === index)
  // An inconsistent response must not be presented as a successful check.
  if (!result.valid && !unique.some(issue => issue.severity === 'error')) throw new Error('配置检查未通过，服务端未返回具体原因，请重试。')
  return { valid: result.valid && !unique.some(issue => issue.severity === 'error'), issues: unique }
}
