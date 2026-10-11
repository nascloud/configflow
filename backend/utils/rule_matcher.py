"""规则匹配工具模块

提供域名和IP的规则匹配功能
"""
import re
import ipaddress
from threading import RLock
from typing import Any, Callable, Dict, Optional, Tuple

from backend.utils.logger import get_logger
from backend.utils.url_utils import safe_exception_details

logger = get_logger(__name__)


def is_valid_domain(query: str) -> bool:
    """验证输入是否为有效域名

    Args:
        query: 待验证的字符串

    Returns:
        是否为有效域名
    """
    if not query or len(query) > 253:
        return False

    # 域名正则表达式
    domain_pattern = r'^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$'
    return bool(re.match(domain_pattern, query))


def is_valid_ip(query: str) -> bool:
    """验证输入是否为有效IP地址（IPv4或IPv6）

    Args:
        query: 待验证的字符串

    Returns:
        是否为有效IP地址
    """
    try:
        ipaddress.ip_address(query)
        return True
    except ValueError:
        return False


def parse_rule_line(line: str) -> Optional[Tuple[str, str]]:
    """解析规则行，提取规则类型和值（支持 Clash、MosDNS 及 List 格式）

    Args:
        line: 规则行，如 "DOMAIN-SUFFIX,google.com"、"domain:google.com" 或 "+.google.com"

    Returns:
        (规则类型, 规则值) 元组，如果解析失败返回 None
    """
    line = line.strip()

    # 跳过注释和空行
    if not line or line.startswith('#') or line.startswith('!') or line.startswith('//'):
        return None

    # 处理 YAML 列表前缀 (- value)
    if line.startswith('-'):
        line = line[1:].strip()
        if not line:
            return None

    # 去掉行内注释（# 之后内容）
    if '#' in line:
        line = line.split('#', 1)[0].strip()
        if not line:
            return None

    # 去掉包裹引号
    line = line.strip('\'"')

    # 1. Clash/YAML 逗号分隔格式
    if ',' in line:
        parts = line.split(',', 1)
        rule_type = parts[0].strip().upper()
        rule_value = parts[1].strip()
        # 剥离尾部的 no-resolve 参数，它不属于规则值（否则 IP 匹配和查重都会失效）
        if rule_value.lower().endswith(',no-resolve'):
            rule_value = rule_value[:-len(',no-resolve')].strip()
        return (rule_type, rule_value)

    # 2. MosDNS 格式（domain: / full: / keyword: / regexp: / ip:）
    if ':' in line:
        prefix, value = line.split(':', 1)
        prefix = prefix.strip().lower()
        value = value.strip()

        # YAML 规则集的 payload: 头行不是规则
        if prefix == 'payload' and not value:
            return None

        if prefix == 'domain':
            return ('DOMAIN-SUFFIX', value)
        if prefix == 'full':
            return ('DOMAIN', value)
        if prefix == 'keyword':
            return ('DOMAIN-KEYWORD', value)
        if prefix in ('regexp', 'regex'):
            return ('REGEX', value)
        if prefix == 'ip':
            return ('IP', value)

    # 3. List 格式（+.example.com / .example.com / *.example.com / example.com / IP）
    if line.startswith('+.'):
        return ('DOMAIN-SUFFIX', line[2:].strip())
    if line.startswith('*.'):
        return ('DIRECT-SUBDOMAIN', line[2:].strip())
    if line.startswith('.') and not line.startswith('..'):
        return ('SUBDOMAIN-ONLY', line[1:].strip())

    # 4. 纯 IP 或 CIDR
    try:
        ipaddress.ip_address(line)
        return ('IP', line)
    except ValueError:
        try:
            ipaddress.ip_network(line, strict=False)
            return ('IP-CIDR', line)
        except ValueError:
            pass

    # 5. 默认当作精确域名
    return ('DOMAIN', line)


def match_domain(domain: str, rule_type: str, rule_value: str) -> bool:
    """匹配域名规则

    Args:
        domain: 待匹配的域名
        rule_type: 规则类型（DOMAIN, DOMAIN-SUFFIX, DOMAIN-KEYWORD等）
        rule_value: 规则值

    Returns:
        是否匹配
    """
    domain = domain.lower()
    rule_value = rule_value.lower()

    if rule_type == 'DOMAIN':
        # 完全匹配
        return domain == rule_value

    elif rule_type == 'DOMAIN-SUFFIX':
        # 后缀匹配
        if domain == rule_value:
            return True
        return domain.endswith('.' + rule_value)

    elif rule_type == 'DOMAIN-KEYWORD':
        # 关键字匹配
        return rule_value in domain
    elif rule_type == 'SUBDOMAIN-ONLY':
        suffix = rule_value
        if not suffix or not domain.endswith('.' + suffix):
            return False
        return domain != suffix
    elif rule_type == 'DIRECT-SUBDOMAIN':
        suffix = rule_value
        if not suffix or not domain.endswith('.' + suffix):
            return False
        prefix = domain[:-(len(suffix) + 1)]
        return prefix != '' and '.' not in prefix
    elif rule_type in ('REGEX', 'REGEXP'):
        try:
            return bool(re.search(rule_value, domain))
        except re.error:
            return False

    return False


def match_ip(ip_str: str, rule_type: str, rule_value: str) -> bool:
    """匹配IP规则

    Args:
        ip_str: 待匹配的IP地址字符串
        rule_type: 规则类型（IP-CIDR, IP-CIDR6等）
        rule_value: 规则值（CIDR格式）

    Returns:
        是否匹配
    """
    try:
        ip = ipaddress.ip_address(ip_str)

        if rule_type in ('IP-CIDR', 'IP-CIDR6'):
            # CIDR匹配
            network = ipaddress.ip_network(rule_value, strict=False)
            return ip in network
        if rule_type == 'IP':
            return ip == ipaddress.ip_address(rule_value)

        return False
    except (ValueError, TypeError):
        return False


def match_query(query: str, rule_type: str, rule_value: str) -> bool:
    """根据查询内容自动判断并匹配规则

    Args:
        query: 待匹配的查询（域名或IP）
        rule_type: 规则类型
        rule_value: 规则值

    Returns:
        是否匹配
    """
    # 判断查询类型
    if is_valid_ip(query):
        # IP匹配
        return match_ip(query, rule_type, rule_value)
    elif is_valid_domain(query):
        # 域名匹配
        return match_domain(query, rule_type, rule_value)

    return False


class _RuleIndex:
    """Index common rule types while retaining the first matching source line.

    Entries in every index carry their original position. A more specific suffix
    must never overtake an earlier broad suffix, keyword or regular expression.
    """

    def __init__(self, entries):
        self._exact = {}
        self._suffix = {}
        self._subdomain = {}
        self._direct_subdomain = {}
        self._domain_patterns = []
        self._ips = {}
        self._networks = {4: {}, 6: {}}
        domain_indexes = {
            'DOMAIN': self._exact,
            'DOMAIN-SUFFIX': self._suffix,
            'SUBDOMAIN-ONLY': self._subdomain,
            'DIRECT-SUBDOMAIN': self._direct_subdomain,
        }
        for position, (rule_type, value, line) in enumerate(entries):
            candidate = (position, line)
            if rule_type in domain_indexes:
                domain_indexes[rule_type].setdefault(value.lower(), candidate)
            elif rule_type == 'DOMAIN-KEYWORD':
                self._domain_patterns.append((position, line, value.lower(), None))
            elif rule_type in ('REGEX', 'REGEXP'):
                try:
                    # match_domain has always lowercased both pattern and query.
                    pattern = re.compile(value.lower())
                except re.error:
                    continue
                self._domain_patterns.append((position, line, None, pattern))
            elif rule_type == 'IP':
                try:
                    self._ips.setdefault(ipaddress.ip_address(value), candidate)
                except (ValueError, TypeError):
                    continue
            elif rule_type in ('IP-CIDR', 'IP-CIDR6'):
                try:
                    network = ipaddress.ip_network(value, strict=False)
                except (ValueError, TypeError):
                    continue
                networks = self._networks[network.version].setdefault(network.prefixlen, {})
                networks.setdefault(int(network.network_address), candidate)

    @staticmethod
    def _earlier(current, candidate):
        if candidate is not None and (current is None or candidate[0] < current[0]):
            return candidate
        return current

    def match_domain(self, domain: str) -> Optional[str]:
        best = self._exact.get(domain)
        suffix = domain
        is_subdomain = False
        while suffix:
            best = self._earlier(best, self._suffix.get(suffix))
            if is_subdomain:
                best = self._earlier(best, self._subdomain.get(suffix))
            _, separator, suffix = suffix.partition('.')
            if not separator:
                break
            is_subdomain = True
        _, separator, parent = domain.partition('.')
        if separator:
            best = self._earlier(best, self._direct_subdomain.get(parent))
        for position, line, keyword, pattern in self._domain_patterns:
            if best is not None and position >= best[0]:
                break
            if (keyword is not None and keyword in domain) or (pattern is not None and pattern.search(domain)):
                best = (position, line)
                break
        return best[1] if best is not None else None

    def match_ip(self, ip) -> Optional[str]:
        best = self._ips.get(ip)
        address = int(ip)
        bits = ip.max_prefixlen
        for prefix, networks in self._networks[ip.version].items():
            # At most 33 IPv4 / 129 IPv6 prefix lengths, regardless of rule count.
            host_bits = bits - prefix
            network = (address >> host_bits) << host_bits
            best = self._earlier(best, networks.get(network))
        return best[1] if best is not None else None


class RuleConfigMatcher:
    """按 rule_configs 顺序匹配域名/IP，返回第一条命中的规则。

    每个规则集首次使用时加载并索引；域名按标签查找，IP 按前缀长度查找，
    同一实例后续查询无需逐条扫描常见规则，也不会重复解析查询的类型。
    """

    def __init__(self, rule_configs, rule_library, load_content: Callable[[dict, Optional[dict]], str]):
        # 序号按全部规则计（含禁用的），与规则列表上显示的行号一致
        self._items = [(index, item) for index, item in enumerate(rule_configs, start=1)
                       if item.get('enabled', True) and item.get('library_enabled', True)]
        self._library = {item.get('id'): item for item in rule_library if item.get('id')}
        self._load_content = load_content
        self._indexes = {}
        self._index_lock = RLock()

    def _ruleset_index(self, index: int, rule_item: dict, library_rule: Optional[dict]) -> _RuleIndex:
        with self._index_lock:
            if index not in self._indexes:
                try:
                    content = self._load_content(rule_item, library_rule) or ''
                except Exception as error:
                    logger.error('Error processing ruleset "%s": %s', rule_item.get('name', '规则集'), safe_exception_details(error))
                    content = ''
                if not content:
                    logger.warning('Skipping ruleset "%s": unable to fetch content', rule_item.get('name', '规则集'))

                def entries():
                    for line in content.splitlines():
                        try:
                            parsed = parse_rule_line(line)
                        except Exception:
                            continue
                        if parsed:
                            yield parsed[0], parsed[1], line.strip()

                # Publish only the complete immutable index to concurrent readers.
                self._indexes[index] = _RuleIndex(entries())
            return self._indexes[index]

    def match(self, query: str) -> Optional[Dict[str, Any]]:
        try:
            ip = ipaddress.ip_address(query)
        except ValueError:
            ip = None
            if not is_valid_domain(query):
                return None
        domain = query.lower() if ip is None else None
        for index, rule_item in self._items:
            item_type = rule_item.get('itemType', 'rule')
            policy = rule_item.get('policy', 'DIRECT')
            if item_type == 'rule':
                rule_type = rule_item.get('rule_type', '')
                rule_value = rule_item.get('value', '')
                if ip is None:
                    matched = match_domain(domain, rule_type, rule_value)
                else:
                    # Reuse the parsed address for direct rules as well.
                    matched = False
                    try:
                        if rule_type in ('IP-CIDR', 'IP-CIDR6'):
                            matched = ip in ipaddress.ip_network(rule_value, strict=False)
                        elif rule_type == 'IP':
                            matched = ip == ipaddress.ip_address(rule_value)
                    except (ValueError, TypeError):
                        pass
                if matched:
                    return {
                        'rule_name': f'{rule_type} 规则',
                        'rule_type': 'rule',
                        'matched_line': f'{rule_type},{rule_value}',
                        'policy': policy,
                        'source': '直接配置的规则',
                        'priority': index,
                        'rule_id': rule_item.get('id'),
                        'item_type': 'rule',
                        'behavior': 'classical',
                    }
            elif item_type == 'ruleset':
                rule_set_name = rule_item.get('name', '规则集')
                library_rule = self._library.get(rule_item.get('library_rule_id', ''))
                ruleset = self._ruleset_index(index, rule_item, library_rule)
                line = ruleset.match_domain(domain) if ip is None else ruleset.match_ip(ip)
                if line is not None:
                    return {
                        'rule_name': rule_set_name,
                        'rule_type': 'ruleset',
                        'matched_line': line,
                        'policy': policy,
                        'source': f'规则集: {rule_set_name}',
                        'priority': index,
                        'rule_id': rule_item.get('id'),
                        'item_type': 'ruleset',
                        'behavior': rule_item.get('behavior', 'classical'),
                    }
        return None
