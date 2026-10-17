import type { OxiDNSDevicePolicy } from '@/types/oxidnsMonitor'
import { normalizeClientRange } from './oxidnsClientRange'

/** Generated source ranges can be split around direct devices or merged.
 * Only open a single authoring row when it wholly contains the generated range.
 */
export function sourcePolicyIndex(address: string, policies: OxiDNSDevicePolicy[]): number {
  try {
    const canonical = normalizeClientRange(address)
    const exact = policies.findIndex(row => normalizeClientRange(row.ip) === canonical)
    if (exact >= 0) return exact
    const range = (value: string) => {
      const [host, suffix = '32'] = normalizeClientRange(value).split('/')
      if (host!.includes(':')) throw new Error('Not IPv4')
      const prefix = Number(suffix), start = host!.split('.').reduce((n, octet) => n * 256 + Number(octet), 0)
      return { prefix, start, end: start + 2 ** (32 - prefix) - 1 }
    }
    const target = range(address)
    return policies.map((row, index) => {
      if (row.mode !== 'smart' || row.ip.includes(':')) return null
      const network = range(row.ip)
      return network.start <= target.start && network.end >= target.end ? { index, prefix: network.prefix } : null
    }).filter(row => row !== null).sort((a, b) => b!.prefix - a!.prefix)[0]?.index ?? -1
  } catch { return -1 }
}
