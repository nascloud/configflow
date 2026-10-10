/**
 * 从节点名识别地区：中文名、城市名或两字母缩写。
 * 拉丁缩写要求两侧不是字母，避免把单词片段（如 RUSSIA 里的 US）误判。
 */
export interface Region {
  code: string
  name: string
}

const RULES: Array<[Region, RegExp]> = [
  [{ code: 'HK', name: '香港' }, /港|(?<![A-Za-z])HK(?![A-Za-z])|Hong\s?Kong/i],
  [{ code: 'TW', name: '台湾' }, /台湾|台北|(?<![A-Za-z])TW(?![A-Za-z])|Taiwan/i],
  [{ code: 'JP', name: '日本' }, /日本|东京|大阪|(?<![A-Za-z])JP(?![A-Za-z])|Japan|Tokyo/i],
  [{ code: 'SG', name: '新加坡' }, /新加坡|狮城|(?<![A-Za-z])SG(?![A-Za-z])|Singapore/i],
  [{ code: 'US', name: '美国' }, /美国|洛杉矶|硅谷|(?<![A-Za-z])US(?![A-Za-z])|United\s?States|America/i],
  [{ code: 'KR', name: '韩国' }, /韩国|首尔|(?<![A-Za-z])KR(?![A-Za-z])|Korea/i],
  [{ code: 'GB', name: '英国' }, /英国|伦敦|(?<![A-Za-z])(UK|GB)(?![A-Za-z])|Britain|London/i],
  [{ code: 'DE', name: '德国' }, /德国|法兰克福|(?<![A-Za-z])DE(?![A-Za-z])|Germany/i]
]

export const OTHER_REGION: Region = { code: '其他', name: '其他' }

export const regionOf = (name: string): Region =>
  RULES.find(([, re]) => re.test(name || ''))?.[0] ?? OTHER_REGION
