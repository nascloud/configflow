/** Canonical client IP/CIDR, matching the server's device-policy normalization. */
export function normalizeClientRange(value: string): string {
  const parts = value.trim().split('/')
  const host = parts[0]
  if (parts.length > 2 || !/^[0-9a-fA-F:.]+$/.test(host)) throw new Error('Invalid IP')

  function ipv4(value: string): bigint {
    const octets = value.split('.')
    if (octets.length !== 4 || octets.some(part => !/^(0|[1-9]\d{0,2})$/.test(part) || Number(part) > 255)) throw new Error('Invalid IPv4')
    return octets.reduce((address, part) => (address << 8n) | BigInt(part), 0n)
  }

  let bits = host.includes(':') ? 128 : 32
  let address: bigint
  if (bits === 128) {
    if (host.includes('.')) ipv4(host.slice(host.lastIndexOf(':') + 1))
    const canonical = new URL(`http://[${host}]/`).hostname.slice(1, -1)
    const halves = canonical.split('::').map(half => half ? half.split(':') : [])
    const groups = halves.length === 2
      ? [...halves[0], ...Array(8 - halves[0].length - halves[1].length).fill('0'), ...halves[1]]
      : halves[0]
    address = groups.reduce((result, group) => (result << 16n) | BigInt(`0x${group}`), 0n)
  } else address = ipv4(host)

  if (parts.length === 2 && !/^[0-9]{1,3}$/.test(parts[1])) throw new Error('Invalid prefix')
  let prefix = parts.length === 2 ? Number(parts[1]) : bits
  if (prefix > bits) throw new Error('Invalid prefix')
  if (bits === 128 && address >> 32n === 0xffffn) {
    if (prefix < 96) throw new Error('Ambiguous mapped IPv4 range')
    address &= 0xffffffffn
    bits = 32
    prefix -= 96
  }
  const hostBits = BigInt(bits - prefix)
  const network = (address >> hostBits) << hostBits
  const multicast = bits === 32
    ? prefix >= 4 && network >> 28n === 14n
    : prefix >= 8 && network >> 120n === 255n
  if (multicast || (prefix === bits && network === 0n)) throw new Error('Not a client address')

  const formatted = bits === 32
    ? [24n, 16n, 8n, 0n].map(shift => String((network >> shift) & 255n)).join('.')
    : new URL(`http://[${Array.from({ length: 8 }, (_, index) => ((network >> BigInt((7 - index) * 16)) & 65535n).toString(16)).join(':')}]/`).hostname.slice(1, -1)
  return prefix === bits ? formatted : `${formatted}/${prefix}`
}
