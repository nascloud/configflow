"""客户端配置导入：把 Mihomo / Surge / Loon / Shadowrocket 配置文件转换为本项目的数据结构。

导入分两步：
1. ``parse_client_config`` 把原始配置解析为与客户端无关的中间结构（节点、订阅、策略组、规则集、规则）；
2. ``build_import`` 把中间结构映射到当前共享资源与新配置空间：
   - 节点、订阅、规则集写入共享资源（相同定义复用已有资源，名称冲突时自动改名）；
   - 策略组、规则编排与客户端基础配置写入新配置空间。

无法表达的内容（不支持的节点类型、规则类型、策略组类型等）跳过并在 warnings 中说明。
"""
import copy
import json
import re
import uuid
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

import yaml

CLIENT_TYPES = ('mihomo', 'surge', 'loon', 'shadowrocket')

GROUP_TYPES = {'select', 'url-test', 'fallback', 'load-balance'}

# 与「策略规则」页面一致，另加 Mihomo 也支持的常用类型；其余类型跳过
SUPPORTED_RULE_TYPES = {
    'DOMAIN', 'DOMAIN-SUFFIX', 'DOMAIN-KEYWORD', 'DOMAIN-REGEX', 'IP-CIDR', 'IP-CIDR6', 'IP-SUFFIX',
    'IP-ASN', 'SRC-IP-CIDR', 'DST-PORT', 'SRC-PORT', 'IN-PORT', 'GEOIP', 'GEOSITE', 'PROCESS-NAME',
    'PROCESS-PATH', 'NETWORK', 'RULE-SET', 'AND', 'OR', 'NOT', 'MATCH',
}
RULE_TYPE_ALIASES = {
    'FINAL': 'MATCH',
    'DEST-PORT': 'DST-PORT',
    'SRC-IP': 'SRC-IP-CIDR',
    'IP6-CIDR': 'IP-CIDR6',
}
# 规则尾部可选参数，导入时只保留 no-resolve
RULE_OPTIONS = {'no-resolve', 'extended-matching', 'dns-failed', 'pre-matching', 'src'}

BUILTIN_POLICY_MAP = {
    'DIRECT': 'DIRECT',
    'REJECT': 'REJECT',
    'REJECT-DROP': 'REJECT',
    'REJECT-TINYGIF': 'REJECT',
    'REJECT-NO-DROP': 'REJECT',
    'REJECT-IMG': 'REJECT',
    'REJECT-DICT': 'REJECT',
    'REJECT-ARRAY': 'REJECT',
    'REJECT-200': 'REJECT',
}

# Mihomo 策略组可透传的参数（与生成器保持一致）
MIHOMO_GROUP_PASSTHROUGH = ('tolerance', 'lazy', 'strategy', 'timeout', 'max-failed-times',
                            'expected-status', 'disable-udp', 'hidden', 'icon', 'exclude-filter', 'exclude-type')
MIHOMO_GENERATED_KEYS = ('proxies', 'proxy-groups', 'proxy-providers', 'rules', 'rule-providers', 'sub-rules')

SURGE_GENERATED_SECTIONS = {'Proxy', 'Proxy Group', 'Rule'}
LOON_GENERATED_SECTIONS = {'Proxy', 'Remote Proxy', 'Remote Filter', 'Proxy Group', 'Rule', 'Remote Rule'}

LOAD_BALANCE_STRATEGIES = {'round-robin': 'round-robin', 'pcc': 'consistent-hashing', 'random': 'round-robin'}


class ConfigImportError(ValueError):
    """配置内容无法解析。"""


class _Warnings:
    """按类别汇总的提示，避免同类问题逐行刷屏。"""

    def __init__(self):
        self._items: "OrderedDict[str, List[str]]" = OrderedDict()

    def add(self, category: str, detail: str = ''):
        bucket = self._items.setdefault(category, [])
        if detail and detail not in bucket:
            bucket.append(detail)

    def to_list(self) -> List[str]:
        result = []
        for category, details in self._items.items():
            if not details:
                result.append(category)
                continue
            shown = '、'.join(details[:8])
            more = f' 等 {len(details)} 项' if len(details) > 8 else ''
            result.append(f'{category}：{shown}{more}')
        return result


def _empty_plan() -> Dict[str, Any]:
    return {
        'proxies': [],          # mihomo 格式节点字典
        'aliases': {},          # 节点别名 -> DIRECT/REJECT
        'providers': [],        # {'name', 'url'}
        'groups': [],           # 中间策略组
        'rule_providers': {},   # name -> {'url' | 'content', 'behavior'}
        'rules': [],            # {'rule_type', 'value', 'policy', 'no_resolve'}
        'custom_config': None,  # (engine, text)
        'warnings': _Warnings(),
    }


# ---------------------------------------------------------------------------
# 通用文本工具
# ---------------------------------------------------------------------------

def _split_args(text: str, sep: str = ',') -> List[str]:
    """按分隔符切分，忽略引号、括号内的分隔符。"""
    parts, buf, depth, quote = [], [], 0, ''
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ''
            continue
        if ch in '"\'':
            quote = ch
        elif ch in '([{':
            depth += 1
        elif ch in ')]}':
            depth = max(0, depth - 1)
        elif ch == sep and depth == 0:
            parts.append(''.join(buf).strip())
            buf = []
            continue
        buf.append(ch)
    tail = ''.join(buf).strip()
    if tail or parts:
        parts.append(tail)
    return parts


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
        return value[1:-1]
    return value


def _split_kv(items: List[str]) -> Tuple[List[str], Dict[str, str]]:
    """把参数列表拆成位置参数与 key=value 参数。"""
    positional, options = [], {}
    for item in items:
        if not item:
            continue
        key, sep, value = item.partition('=')
        if sep and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', key.strip()):
            options[key.strip().lower()] = _unquote(value)
        else:
            positional.append(_unquote(item))
    return positional, options


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')


def _int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _port(value: Any) -> int:
    port = _int(value)
    if port is None or not 0 < port < 65536:
        raise ValueError(f'端口无效：{value}')
    return port


def _parse_sections(text: str) -> "OrderedDict[str, List[str]]":
    """解析 INI 风格配置，返回 section -> 原始行（含注释，便于原样保留）。"""
    sections: "OrderedDict[str, List[str]]" = OrderedDict()
    current = None
    for raw in text.splitlines():
        stripped = raw.strip()
        match = re.fullmatch(r'\[([^\]]+)\]', stripped)
        if match:
            current = match.group(1).strip()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(raw.rstrip())
    return sections


def _content_lines(lines: List[str]) -> List[str]:
    result = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(('#', ';', '//')):
            continue
        result.append(line)
    return result


def _name_value(line: str) -> Tuple[str, str]:
    name, sep, value = line.partition('=')
    if not sep:
        raise ValueError('缺少 “=”')
    return _unquote(name.strip()), value.strip()


def _custom_sections_text(sections: "OrderedDict[str, List[str]]", generated: set) -> str:
    blocks = []
    for name, lines in sections.items():
        if name in generated:
            continue
        body = '\n'.join(lines).strip('\n')
        blocks.append(f'[{name}]\n{body}' if body else f'[{name}]')
    return '\n\n'.join(blocks) + ('\n' if blocks else '')


def _ruleset_name_from_url(url: str) -> str:
    path = unquote(urlsplit(url).path or '')
    stem = path.rstrip('/').rsplit('/', 1)[-1]
    stem = re.sub(r'\.(list|txt|conf|yaml|yml|mrs|lsr|snippet)$', '', stem, flags=re.I)
    return stem or 'RuleSet'


def _subscription_name_from_url(url: str) -> str:
    host = urlsplit(url).hostname or ''
    return host or '导入订阅'


# ---------------------------------------------------------------------------
# 规则行
# ---------------------------------------------------------------------------

def _parse_rule_line(line: str, warnings: _Warnings) -> Optional[Dict[str, Any]]:
    parts = _split_args(line)
    if not parts or not parts[0]:
        return None
    rule_type = parts[0].strip().upper()
    rule_type = RULE_TYPE_ALIASES.get(rule_type, rule_type)
    if rule_type == 'MATCH':
        if len(parts) < 2:
            warnings.add('规则格式无效，已跳过', line)
            return None
        return {'rule_type': 'MATCH', 'value': '', 'policy': _unquote(parts[1]), 'no_resolve': False}
    if len(parts) < 3:
        warnings.add('规则格式无效，已跳过', line)
        return None
    # 尾部参数（no-resolve 等）位于策略之后
    tail = parts[2:]
    no_resolve = False
    while len(tail) > 1 and (tail[-1].lower() in RULE_OPTIONS or '=' in tail[-1]):
        no_resolve = no_resolve or tail[-1].lower() == 'no-resolve'
        tail.pop()
    if len(tail) != 1:
        warnings.add('规则格式无效，已跳过', line)
        return None
    if rule_type not in SUPPORTED_RULE_TYPES:
        warnings.add('不支持的规则类型，已跳过', rule_type)
        return None
    return {'rule_type': rule_type, 'value': parts[1].strip(), 'policy': _unquote(tail[0]),
            'no_resolve': no_resolve}


# ---------------------------------------------------------------------------
# Mihomo
# ---------------------------------------------------------------------------

def _parse_mihomo(content: str, plan: Dict[str, Any]):
    warnings = plan['warnings']
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise ConfigImportError(f'YAML 解析失败：{error}') from error
    if not isinstance(data, dict):
        raise ConfigImportError('Mihomo 配置必须是 YAML 对象')

    for proxy in data.get('proxies') or []:
        if not isinstance(proxy, dict) or not proxy.get('name') or not proxy.get('type'):
            warnings.add('节点格式无效，已跳过', str(proxy.get('name', '')) if isinstance(proxy, dict) else '')
            continue
        proxy = copy.deepcopy(proxy)
        proxy['name'] = str(proxy['name'])
        if str(proxy['type']).lower() in ('direct', 'reject', 'dns'):
            plan['aliases'][proxy['name']] = 'REJECT' if str(proxy['type']).lower() == 'reject' else 'DIRECT'
            continue
        plan['proxies'].append(proxy)

    for name, provider in (data.get('proxy-providers') or {}).items():
        if not isinstance(provider, dict) or provider.get('type') != 'http' or not provider.get('url'):
            warnings.add('仅支持 http 类型的 proxy-providers，已跳过', str(name))
            continue
        plan['providers'].append({'name': str(name), 'url': str(provider['url'])})

    for name, provider in (data.get('rule-providers') or {}).items():
        name = str(name)
        if not isinstance(provider, dict):
            continue
        behavior = provider.get('behavior') or 'classical'
        if provider.get('format') == 'mrs':
            warnings.add('暂不支持 mrs 格式的规则集，已跳过', name)
            continue
        if provider.get('type') == 'http' and provider.get('url'):
            plan['rule_providers'][name] = {'url': str(provider['url']), 'behavior': behavior}
        elif provider.get('type') == 'inline' and isinstance(provider.get('payload'), list):
            plan['rule_providers'][name] = {'content': '\n'.join(str(item) for item in provider['payload']),
                                            'behavior': behavior}
        else:
            warnings.add('仅支持 http / inline 类型的 rule-providers，已跳过', name)

    for group in data.get('proxy-groups') or []:
        if not isinstance(group, dict) or not group.get('name'):
            continue
        group_type = str(group.get('type', '')).lower()
        if group_type not in GROUP_TYPES:
            warnings.add('不支持的策略组类型，已跳过', f'{group.get("name")}({group_type})')
            continue
        include_all = bool(group.get('include-all'))
        item = {
            'name': str(group['name']),
            'type': group_type,
            'members': [str(member) for member in group.get('proxies') or []],
            'providers': [str(name) for name in group.get('use') or []],
            'filter': str(group.get('filter') or ''),
            'include_all_proxies': include_all or bool(group.get('include-all-proxies')),
            'include_all_providers': include_all or bool(group.get('include-all-providers')),
            'url': group.get('url'),
            'interval': _int(group.get('interval')),
            'extra': {key: copy.deepcopy(group[key]) for key in MIHOMO_GROUP_PASSTHROUGH if key in group},
        }
        plan['groups'].append(item)

    for line in data.get('rules') or []:
        if not isinstance(line, str):
            continue
        if line.strip().upper().startswith('SUB-RULE'):
            warnings.add('不支持 SUB-RULE，已跳过', line.strip())
            continue
        rule = _parse_rule_line(line, warnings)
        if rule:
            plan['rules'].append(rule)
    if data.get('sub-rules'):
        warnings.add('sub-rules 未导入')

    custom = {key: value for key, value in data.items() if key not in MIHOMO_GENERATED_KEYS}
    if custom:
        plan['custom_config'] = ('mihomo', yaml.safe_dump(custom, allow_unicode=True, sort_keys=False))


# ---------------------------------------------------------------------------
# Surge / Shadowrocket 节点
# ---------------------------------------------------------------------------

def _ws_headers(value: str) -> Dict[str, str]:
    headers = {}
    for pair in value.split('|'):
        key, sep, val = pair.partition(':')
        if sep and key.strip():
            headers[key.strip()] = _unquote(val.strip())
    return headers


def _apply_common_tls(proxy: Dict[str, Any], options: Dict[str, str], sni_key: str = 'sni'):
    sni = options.get('sni') or options.get('peer') or options.get('tls-name') or options.get('obfs-host-sni')
    if sni:
        proxy[sni_key] = sni
    if 'skip-cert-verify' in options or 'allowinsecure' in options:
        proxy['skip-cert-verify'] = _truthy(options.get('skip-cert-verify', options.get('allowinsecure')))
    if options.get('alpn'):
        proxy['alpn'] = [item.strip() for item in options['alpn'].split(',') if item.strip()]


def _apply_surge_ws(proxy: Dict[str, Any], options: Dict[str, str]):
    obfs = options.get('obfs', '').lower()
    if _truthy(options.get('ws', '')) or obfs in ('websocket', 'ws'):
        proxy['network'] = 'ws'
        ws_opts = {'path': options.get('ws-path') or options.get('path') or options.get('obfs-path') or '/'}
        headers = _ws_headers(options.get('ws-headers', ''))
        host = options.get('obfs-host')
        if host and 'Host' not in headers:
            headers['Host'] = host
        if headers:
            ws_opts['headers'] = headers
        proxy['ws-opts'] = ws_opts
    elif obfs == 'grpc':
        proxy['network'] = 'grpc'
        if options.get('path'):
            proxy['grpc-opts'] = {'grpc-service-name': options['path']}


def _parse_surge_proxy(name: str, value: str, sections, plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    args = _split_args(value)
    proxy_type = args[0].strip().lower() if args else ''
    if proxy_type in ('direct', 'reject', 'reject-tinygif', 'reject-drop'):
        plan['aliases'][name] = 'DIRECT' if proxy_type == 'direct' else 'REJECT'
        return None
    positional, options = _split_kv(args[1:])
    udp = _truthy(options.get('udp-relay', options.get('udp', 'false')))

    if proxy_type == 'wireguard':
        return _parse_surge_wireguard(name, options, sections)
    if len(positional) < 2:
        raise ValueError('缺少服务器或端口')
    server, port = positional[0], _port(positional[1])
    extra = positional[2:]
    proxy: Dict[str, Any] = {'name': name, 'server': server, 'port': port}
    if udp:
        proxy['udp'] = True
    if _truthy(options.get('tfo', 'false')) or _truthy(options.get('fast-open', 'false')):
        proxy['tfo'] = True

    if proxy_type == 'ss':
        proxy.update(type='ss', cipher=options.get('encrypt-method') or options.get('method') or 'aes-128-gcm',
                     password=options.get('password', extra[0] if extra else ''))
        obfs = options.get('obfs', '').lower()
        if obfs in ('http', 'tls'):
            proxy['plugin'] = 'obfs'
            proxy['plugin-opts'] = {'mode': obfs, 'host': options.get('obfs-host', '')}
        elif obfs in ('websocket', 'ws'):
            proxy['plugin'] = 'v2ray-plugin'
            proxy['plugin-opts'] = {'mode': 'websocket', 'host': options.get('obfs-host', ''),
                                    'path': options.get('path') or options.get('obfs-uri') or '/',
                                    'tls': _truthy(options.get('tls', 'false'))}
        if options.get('shadow-tls-password'):
            proxy['plugin'] = 'shadow-tls'
            proxy['plugin-opts'] = {'password': options['shadow-tls-password'],
                                    'host': options.get('shadow-tls-sni', ''),
                                    'version': _int(options.get('shadow-tls-version'), 3)}
    elif proxy_type in ('vmess', 'vless'):
        proxy['type'] = proxy_type
        proxy['uuid'] = options.get('username') or options.get('password') or (extra[0] if extra else '')
        if proxy_type == 'vmess':
            proxy['cipher'] = options.get('method') or options.get('encrypt-method') or 'auto'
            proxy['alterId'] = _int(options.get('alterid'), 0)
        if options.get('flow') or options.get('xtls'):
            proxy['flow'] = options.get('flow') or 'xtls-rprx-vision'
        if _truthy(options.get('tls', 'false')) or _truthy(options.get('over-tls', 'false')):
            proxy['tls'] = True
        _apply_common_tls(proxy, options, sni_key='servername')
        if options.get('publickey') or options.get('public-key'):
            proxy['tls'] = True
            proxy['reality-opts'] = {'public-key': options.get('publickey') or options.get('public-key'),
                                     'short-id': options.get('shortid') or options.get('short-id') or ''}
        _apply_surge_ws(proxy, options)
    elif proxy_type == 'trojan':
        proxy.update(type='trojan', password=options.get('password', extra[0] if extra else ''))
        _apply_common_tls(proxy, options)
        _apply_surge_ws(proxy, options)
    elif proxy_type in ('http', 'https', 'socks5', 'socks5-tls'):
        proxy['type'] = 'socks5' if proxy_type.startswith('socks5') else 'http'
        username = options.get('username', extra[0] if len(extra) > 0 else '')
        password = options.get('password', extra[1] if len(extra) > 1 else '')
        if username:
            proxy['username'] = username
        if password:
            proxy['password'] = password
        if proxy_type in ('https', 'socks5-tls') or _truthy(options.get('tls', 'false')):
            proxy['tls'] = True
            _apply_common_tls(proxy, options)
    elif proxy_type == 'snell':
        proxy.update(type='snell', psk=options.get('psk', ''), version=_int(options.get('version'), 4))
        if options.get('obfs'):
            proxy['obfs-opts'] = {'mode': options['obfs'], 'host': options.get('obfs-host', '')}
    elif proxy_type in ('hysteria2', 'hy2'):
        proxy.update(type='hysteria2', password=options.get('password', extra[0] if extra else ''))
        _apply_common_tls(proxy, options)
        if options.get('download-bandwidth'):
            proxy['down'] = f"{options['download-bandwidth']} Mbps"
        if options.get('obfs-password') or options.get('salamander-password'):
            proxy['obfs'] = 'salamander'
            proxy['obfs-password'] = options.get('obfs-password') or options.get('salamander-password')
        if options.get('port-hopping'):
            proxy['ports'] = options['port-hopping'].replace(';', ',')
        proxy['udp'] = True
    elif proxy_type in ('tuic', 'tuic-v5'):
        proxy['type'] = 'tuic'
        for key in ('uuid', 'password', 'token'):
            if options.get(key):
                proxy[key] = options[key]
        _apply_common_tls(proxy, options)
        proxy.setdefault('alpn', ['h3'])
        proxy['udp'] = True
    elif proxy_type == 'anytls':
        proxy.update(type='anytls', password=options.get('password', extra[0] if extra else ''))
        _apply_common_tls(proxy, options)
    else:
        raise ValueError(f'不支持的节点类型 {proxy_type}')
    return proxy


def _parse_surge_wireguard(name: str, options: Dict[str, str], sections) -> Dict[str, Any]:
    section = options.get('section-name', '')
    lines = sections.get(f'WireGuard {section}')
    if lines is None:
        raise ValueError(f'未找到 [WireGuard {section}]')
    fields = {}
    for line in _content_lines(lines):
        key, sep, value = line.partition('=')
        if sep:
            fields[key.strip().lower()] = value.strip()
    peer_text = fields.get('peer', '').strip()
    if peer_text.startswith('(') and peer_text.endswith(')'):
        peer_text = peer_text[1:-1]
    _, peer = _split_kv(_split_args(peer_text))
    endpoint = peer.get('endpoint', '')
    host, _, port = endpoint.rpartition(':')
    proxy = {'name': name, 'type': 'wireguard', 'server': host.strip('[]'), 'port': _port(port),
             'private-key': fields.get('private-key', ''), 'public-key': peer.get('public-key', ''),
             'udp': True}
    if fields.get('self-ip'):
        proxy['ip'] = fields['self-ip']
    if fields.get('self-ip-v6'):
        proxy['ipv6'] = fields['self-ip-v6']
    if fields.get('dns-server'):
        proxy['dns'] = [item.strip() for item in fields['dns-server'].split(',') if item.strip()]
    if fields.get('mtu'):
        proxy['mtu'] = _int(fields['mtu'])
    if peer.get('allowed-ips'):
        proxy['allowed-ips'] = [item.strip() for item in peer['allowed-ips'].split(',') if item.strip()]
    if peer.get('preshared-key'):
        proxy['pre-shared-key'] = peer['preshared-key']
    if peer.get('keepalive'):
        proxy['persistent-keepalive'] = _int(peer['keepalive'])
    if peer.get('client-id'):
        proxy['reserved'] = peer['client-id']
    return proxy


# ---------------------------------------------------------------------------
# Loon 节点
# ---------------------------------------------------------------------------

def _apply_loon_transport(proxy: Dict[str, Any], options: Dict[str, str]):
    transport = options.get('transport', 'tcp').lower()
    if transport == 'ws':
        ws_opts = {'path': options.get('path') or '/'}
        if options.get('host'):
            ws_opts['headers'] = {'Host': options['host']}
        proxy['network'] = 'ws'
        proxy['ws-opts'] = ws_opts
    elif transport == 'http':
        http_opts = {'path': [options.get('path') or '/']}
        if options.get('host'):
            http_opts['headers'] = {'Host': [options['host']]}
        proxy['network'] = 'http'
        proxy['http-opts'] = http_opts
    elif transport not in ('', 'tcp'):
        raise ValueError(f'不支持的传输方式 {transport}')


def _apply_loon_tls(proxy: Dict[str, Any], options: Dict[str, str], sni_key: str = 'sni'):
    if options.get('sni') or options.get('tls-name'):
        proxy[sni_key] = options.get('sni') or options.get('tls-name')
    if 'skip-cert-verify' in options:
        proxy['skip-cert-verify'] = _truthy(options['skip-cert-verify'])
    if options.get('alpn'):
        proxy['alpn'] = [item.strip() for item in options['alpn'].split(',') if item.strip()]
    if options.get('public-key'):
        proxy['reality-opts'] = {'public-key': options['public-key'], 'short-id': options.get('short-id', '')}


def _parse_loon_proxy(name: str, value: str, plan: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    args = _split_args(value)
    proxy_type = args[0].strip().lower() if args else ''
    if proxy_type in ('direct', 'reject'):
        plan['aliases'][name] = proxy_type.upper()
        return None
    if proxy_type == 'wireguard':
        return _parse_loon_wireguard(name, args[1:])
    positional, options = _split_kv(args[1:])
    if len(positional) < 2:
        raise ValueError('缺少服务器或端口')
    server, port = positional[0], _port(positional[1])
    extra = positional[2:]
    proxy: Dict[str, Any] = {'name': name, 'server': server, 'port': port}
    if _truthy(options.get('udp', 'false')):
        proxy['udp'] = True
    if _truthy(options.get('fast-open', 'false')):
        proxy['tfo'] = True

    if proxy_type == 'shadowsocks':
        proxy.update(type='ss', cipher=extra[0] if extra else 'aes-128-gcm',
                     password=extra[1] if len(extra) > 1 else '')
        if options.get('obfs-name'):
            proxy['plugin'] = 'obfs'
            proxy['plugin-opts'] = {'mode': options['obfs-name'], 'host': options.get('obfs-host', '')}
        elif options.get('shadow-tls-password'):
            proxy['plugin'] = 'shadow-tls'
            proxy['plugin-opts'] = {'password': options['shadow-tls-password'],
                                    'host': options.get('shadow-tls-sni', ''),
                                    'version': _int(options.get('shadow-tls-version'), 3)}
        if _truthy(options.get('udp-over-tcp', 'false')):
            proxy['udp-over-tcp'] = True
    elif proxy_type == 'shadowsocksr':
        proxy.update(type='ssr', cipher=extra[0] if extra else '', password=extra[1] if len(extra) > 1 else '',
                     protocol=options.get('protocol', 'origin'), obfs=options.get('obfs', 'plain'))
        if options.get('protocol-param'):
            proxy['protocol-param'] = options['protocol-param']
        if options.get('obfs-param'):
            proxy['obfs-param'] = options['obfs-param']
    elif proxy_type == 'vmess':
        proxy.update(type='vmess', cipher=extra[0] if extra else 'auto', uuid=extra[1] if len(extra) > 1 else '',
                     alterId=_int(options.get('alterid'), 0))
        _apply_loon_transport(proxy, options)
        if _truthy(options.get('over-tls', 'false')):
            proxy['tls'] = True
            _apply_loon_tls(proxy, options, sni_key='servername')
    elif proxy_type == 'vless':
        proxy.update(type='vless', uuid=extra[0] if extra else '')
        _apply_loon_transport(proxy, options)
        if options.get('flow'):
            proxy['flow'] = options['flow']
        if _truthy(options.get('over-tls', 'false')) or options.get('public-key'):
            proxy['tls'] = True
            _apply_loon_tls(proxy, options, sni_key='servername')
    elif proxy_type == 'trojan':
        proxy.update(type='trojan', password=extra[0] if extra else '')
        _apply_loon_transport(proxy, options)
        _apply_loon_tls(proxy, options)
    elif proxy_type == 'hysteria2':
        proxy.update(type='hysteria2', password=extra[0] if extra else '', udp=True)
        _apply_loon_tls(proxy, options)
        if options.get('salamander-password'):
            proxy['obfs'] = 'salamander'
            proxy['obfs-password'] = options['salamander-password']
        if options.get('server-ports'):
            proxy['ports'] = options['server-ports']
        if options.get('download-bandwidth'):
            proxy['down'] = f"{options['download-bandwidth']} Mbps"
    elif proxy_type == 'anytls':
        proxy.update(type='anytls', password=extra[0] if extra else '')
        _apply_loon_tls(proxy, options)
    elif proxy_type in ('http', 'https', 'socks5'):
        proxy['type'] = 'socks5' if proxy_type == 'socks5' else 'http'
        if extra:
            proxy['username'] = extra[0]
        if len(extra) > 1:
            proxy['password'] = extra[1]
        if proxy_type == 'https' or _truthy(options.get('over-tls', 'false')):
            proxy['tls'] = True
            _apply_loon_tls(proxy, options)
    else:
        raise ValueError(f'不支持的节点类型 {proxy_type}')
    return proxy


def _parse_loon_wireguard(name: str, args: List[str]) -> Dict[str, Any]:
    _, options = _split_kv(args)
    peers_text = options.get('peers', '').strip()
    peers_text = peers_text.strip('[]').strip().strip('{}')
    _, peer = _split_kv(_split_args(peers_text))
    endpoint = peer.get('endpoint', '')
    host, _, port = endpoint.rpartition(':')
    proxy = {'name': name, 'type': 'wireguard', 'server': host.strip('[]'), 'port': _port(port),
             'private-key': options.get('private-key', ''), 'public-key': peer.get('public-key', ''),
             'udp': True}
    if options.get('interface-ip'):
        proxy['ip'] = options['interface-ip']
    if options.get('interface-ipv6'):
        proxy['ipv6'] = options['interface-ipv6']
    dns = [options[key] for key in ('dns', 'dnsv6') if options.get(key)]
    if dns:
        proxy['dns'] = dns
    if options.get('mtu'):
        proxy['mtu'] = _int(options['mtu'])
    if options.get('keepalive'):
        proxy['persistent-keepalive'] = _int(options['keepalive'])
    if peer.get('allowed-ips'):
        proxy['allowed-ips'] = [item.strip() for item in peer['allowed-ips'].split(',') if item.strip()]
    if peer.get('preshared-key'):
        proxy['pre-shared-key'] = peer['preshared-key']
    reserved = peer.get('reserved', '').strip('[]')
    if reserved:
        proxy['reserved'] = [_int(item, 0) for item in reserved.split(',')]
    return proxy


# ---------------------------------------------------------------------------
# Surge / Shadowrocket / Loon 文本配置
# ---------------------------------------------------------------------------

def _parse_proxy_section(lines, plan, parser):
    warnings = plan['warnings']
    for line in _content_lines(lines):
        try:
            name, value = _name_value(line)
            proxy = parser(name, value)
        except ValueError as error:
            warnings.add('节点无法解析，已跳过', f'{line.partition("=")[0].strip()}（{error}）')
            continue
        if proxy:
            plan['proxies'].append(proxy)


def _parse_text_group(name: str, value: str, client: str, plan: Dict[str, Any],
                      filters: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    warnings = plan['warnings']
    args = _split_args(value)
    group_type = args[0].strip().lower() if args else ''
    if group_type == 'smart':
        warnings.add('Surge smart 策略组已按 url-test 导入', name)
        group_type = 'url-test'
    if group_type not in GROUP_TYPES:
        warnings.add('不支持的策略组类型，已跳过', f'{name}({group_type})')
        return None
    members, options = _split_kv(args[1:])
    group = {
        'name': name, 'type': group_type, 'members': [], 'providers': [], 'filter': '',
        'include_all_proxies': _truthy(options.get('include-all-proxies', 'false')),
        'include_all_providers': False,
        'url': options.get('url'), 'interval': _int(options.get('interval')), 'extra': {},
    }
    if options.get('tolerance'):
        group['extra']['tolerance'] = _int(options['tolerance'])
    if group_type == 'load-balance' and options.get('algorithm') in LOAD_BALANCE_STRATEGIES:
        group['extra']['strategy'] = LOAD_BALANCE_STRATEGIES[options['algorithm']]
    if options.get('include-other-group'):
        members.extend(item.strip() for item in options['include-other-group'].split(',') if item.strip())
    if options.get('policy-path'):
        provider_name = f'{name} 订阅'
        plan['providers'].append({'name': provider_name, 'url': options['policy-path']})
        group['providers'].append(provider_name)
    regexes = []
    if options.get('policy-regex-filter'):
        regexes.append(options['policy-regex-filter'])
        # Shadowrocket 的正则筛选作用于全部节点
        if client == 'shadowrocket' and not options.get('policy-path'):
            group['include_all_proxies'] = True
            group['include_all_providers'] = True
    for member in members:
        loon_filter = filters.get(member)
        if loon_filter is not None:
            group['providers'].extend(loon_filter['providers'])
            if not loon_filter['providers']:
                group['include_all_providers'] = True
            if loon_filter['regex']:
                regexes.append(loon_filter['regex'])
            continue
        group['members'].append(member)
    if len(regexes) == 1:
        group['filter'] = regexes[0]
    elif regexes:
        group['filter'] = '|'.join(f'({item})' for item in regexes)
    return group


def _parse_surge_like(content: str, plan: Dict[str, Any], client: str):
    warnings = plan['warnings']
    sections = _parse_sections(content)
    if not sections:
        raise ConfigImportError('未找到任何 [Section]，请确认配置类型是否正确')

    _parse_proxy_section(sections.get('Proxy', []), plan,
                         lambda name, value: _parse_surge_proxy(name, value, sections, plan))

    for line in _content_lines(sections.get('Proxy Group', [])):
        try:
            name, value = _name_value(line)
        except ValueError:
            warnings.add('策略组格式无效，已跳过', line)
            continue
        group = _parse_text_group(name, value, client, plan, {})
        if group:
            plan['groups'].append(group)

    for line in _content_lines(sections.get('Rule', [])):
        parts = _split_args(line)
        head = parts[0].strip().upper() if parts else ''
        if head in ('RULE-SET', 'DOMAIN-SET') and len(parts) >= 3:
            source = _unquote(parts[1])
            if not source.lower().startswith(('http://', 'https://')):
                warnings.add('内置或本地规则集无法导入，已跳过', source)
                continue
            rule_name = _register_url_ruleset(plan, source, 'domain' if head == 'DOMAIN-SET' else 'classical')
            rule = _parse_rule_line(','.join(['RULE-SET', rule_name, *parts[2:]]), warnings)
        else:
            rule = _parse_rule_line(line, warnings)
        if rule:
            plan['rules'].append(rule)

    if client == 'surge':
        text = _custom_sections_text(sections, SURGE_GENERATED_SECTIONS | {
            name for name in sections if name.startswith('WireGuard ')})
        if text.strip():
            plan['custom_config'] = ('surge', text)
    elif any(name not in SURGE_GENERATED_SECTIONS for name in sections):
        warnings.add('Shadowrocket 的 [General] 等其他段落无法转换为本项目的生成参数，未导入')


def _register_url_ruleset(plan: Dict[str, Any], url: str, behavior: str, name: str = '') -> str:
    for existing_name, provider in plan['rule_providers'].items():
        if provider.get('url') == url:
            return existing_name
    base = name or _ruleset_name_from_url(url)
    candidate, index = base, 2
    while candidate in plan['rule_providers']:
        candidate = f'{base}_{index}'
        index += 1
    plan['rule_providers'][candidate] = {'url': url, 'behavior': behavior}
    return candidate


def _parse_loon(content: str, plan: Dict[str, Any]):
    warnings = plan['warnings']
    sections = _parse_sections(content)
    if not sections:
        raise ConfigImportError('未找到任何 [Section]，请确认配置类型是否正确')

    _parse_proxy_section(sections.get('Proxy', []), plan,
                         lambda name, value: _parse_loon_proxy(name, value, plan))

    for line in _content_lines(sections.get('Remote Proxy', [])):
        try:
            name, value = _name_value(line)
        except ValueError:
            continue
        positional, options = _split_kv(_split_args(value))
        if not positional or options.get('enabled', 'true').lower() == 'false':
            continue
        plan['providers'].append({'name': name, 'url': positional[0]})
    provider_names = {provider['name'] for provider in plan['providers']}

    filters = {}
    for line in _content_lines(sections.get('Remote Filter', [])):
        try:
            name, value = _name_value(line)
        except ValueError:
            continue
        positional, options = _split_kv(_split_args(value))
        filter_type = positional[0] if positional else ''
        providers = [item for item in positional[1:] if item in provider_names]
        regex = options.get('filterkey', '') if filter_type.lower() == 'nameregex' else ''
        if filter_type.lower() != 'nameregex':
            warnings.add('仅支持 NameRegex 类型的 Remote Filter，其他过滤条件已忽略', name)
        filters[name] = {'providers': providers, 'regex': regex}

    for line in _content_lines(sections.get('Proxy Group', [])):
        try:
            name, value = _name_value(line)
        except ValueError:
            warnings.add('策略组格式无效，已跳过', line)
            continue
        group = _parse_text_group(name, value, 'loon', plan, filters)
        if group:
            # Loon 策略组可直接引用订阅名称
            group['providers'].extend(member for member in group['members'] if member in provider_names)
            group['members'] = [member for member in group['members'] if member not in provider_names]
            plan['groups'].append(group)

    final_rule = None
    for line in _content_lines(sections.get('Rule', [])):
        rule = _parse_rule_line(line, warnings)
        if rule and rule['rule_type'] == 'MATCH':
            final_rule = rule
        elif rule:
            plan['rules'].append(rule)

    # Loon 本地规则优先于订阅规则，FINAL 最后匹配
    for line in _content_lines(sections.get('Remote Rule', [])):
        positional, options = _split_kv(_split_args(line))
        if not positional or options.get('enabled', 'true').lower() == 'false':
            continue
        url = positional[0]
        if not url.lower().startswith(('http://', 'https://')):
            warnings.add('本地规则集无法导入，已跳过', url)
            continue
        if not options.get('policy'):
            warnings.add('订阅规则缺少 policy，已跳过', url)
            continue
        rule_name = _register_url_ruleset(plan, url, 'classical', options.get('tag', ''))
        plan['rules'].append({'rule_type': 'RULE-SET', 'value': rule_name, 'policy': options['policy'],
                              'no_resolve': False})
    if final_rule:
        plan['rules'].append(final_rule)

    text = _custom_sections_text(sections, LOON_GENERATED_SECTIONS)
    if text.strip():
        plan['custom_config'] = ('loon', text)


def parse_client_config(client_type: str, content: str) -> Dict[str, Any]:
    if client_type not in CLIENT_TYPES:
        raise ConfigImportError(f'不支持的配置类型：{client_type}')
    if not isinstance(content, str) or not content.strip():
        raise ConfigImportError('配置内容不能为空')
    content = content.lstrip('﻿')
    plan = _empty_plan()
    if client_type == 'mihomo':
        _parse_mihomo(content, plan)
    elif client_type == 'loon':
        _parse_loon(content, plan)
    else:
        _parse_surge_like(content, plan, client_type)
    if not plan['proxies'] and not plan['providers'] and not plan['groups'] and not plan['rules']:
        raise ConfigImportError('未从配置中解析到节点、策略组或规则，请确认配置类型是否正确')
    return plan


# ---------------------------------------------------------------------------
# 映射到本项目数据结构
# ---------------------------------------------------------------------------

def _new_id(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:12]}'


def _node_signature(node: Dict[str, Any]) -> str:
    return json.dumps({key: node.get(key) for key in ('type', 'server', 'port', 'params')},
                      sort_keys=True, ensure_ascii=False, default=str)


def _proxy_to_node(proxy: Dict[str, Any]) -> Dict[str, Any]:
    params = {key: copy.deepcopy(value) for key, value in proxy.items()
              if key not in ('name', 'type', 'server', 'port', 'dialer-proxy')}
    return {'name': proxy['name'], 'type': str(proxy['type']).lower(), 'server': proxy.get('server', ''),
            'port': proxy.get('port', 0), 'params': params, 'enabled': True}


def _unique_name(base: str, taken: set, template: str = '{base} ({index})') -> str:
    candidate, index = base, 2
    while candidate in taken:
        candidate = template.format(base=base, index=index)
        index += 1
    return candidate


def _regex_matches(pattern: str, name: str) -> bool:
    if not pattern:
        return True
    try:
        return re.search(pattern, name) is not None
    except re.error:
        return True


def build_import(plan: Dict[str, Any], shared: Dict[str, Any],
                 default_subscription_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """把中间结构映射为共享资源新增项与配置空间字段，不修改传入的 shared。"""
    warnings: _Warnings = plan['warnings']
    default_subscription_ids = [sub_id for sub_id in (default_subscription_ids or [])
                                if any(sub.get('id') == sub_id for sub in shared.get('subscriptions', []))]
    added = {'subscriptions': [], 'nodes': [], 'rule_library': []}
    reused = {'subscriptions': 0, 'nodes': 0, 'rule_library': 0}

    # 订阅：同 URL 复用
    subs_by_url = {sub.get('url'): sub for sub in shared.get('subscriptions', []) if sub.get('url')}
    sub_names = {sub.get('name') for sub in shared.get('subscriptions', [])}
    provider_ids: Dict[str, str] = {}
    for provider in plan['providers']:
        existing = subs_by_url.get(provider['url'])
        if existing:
            provider_ids[provider['name']] = existing['id']
            reused['subscriptions'] += 1
            continue
        name = _unique_name(provider['name'] or _subscription_name_from_url(provider['url']), sub_names)
        sub_names.add(name)
        sub = {'id': _new_id('sub'), 'name': name, 'url': provider['url'], 'type': 'universal',
               'interval': 86400, 'enabled': True}
        added['subscriptions'].append(sub)
        subs_by_url[sub['url']] = sub
        provider_ids[provider['name']] = sub['id']

    # 节点：名称与定义都相同则复用，名称冲突则改名
    existing_nodes = shared.get('nodes', [])
    nodes_by_name = {node.get('name'): node for node in existing_nodes}
    node_names = set(nodes_by_name)
    node_ids: Dict[str, str] = {}      # 原始节点名 -> 节点 ID
    node_final_names: Dict[str, str] = {}
    imported_node_order: List[str] = []
    dialer_skipped = []
    for proxy in plan['proxies']:
        if proxy['name'] in node_ids:
            warnings.add('节点名称重复，已跳过后出现的节点', proxy['name'])
            continue
        if proxy.get('dialer-proxy'):
            dialer_skipped.append(proxy['name'])
        node = _proxy_to_node(proxy)
        existing = nodes_by_name.get(node['name'])
        if existing and _node_signature(existing) == _node_signature(node):
            node_ids[proxy['name']] = existing['id']
            node_final_names[proxy['name']] = existing['name']
            reused['nodes'] += 1
        else:
            node['name'] = _unique_name(node['name'], node_names)
            node['id'] = _new_id('node')
            node_names.add(node['name'])
            added['nodes'].append(node)
            node_ids[proxy['name']] = node['id']
            node_final_names[proxy['name']] = node['name']
            if node['name'] != proxy['name']:
                warnings.add('节点与已有节点重名，已改名', f'{proxy["name"]} → {node["name"]}')
        imported_node_order.append(proxy['name'])
    if dialer_skipped:
        warnings.add('节点的 dialer-proxy 未导入，请在策略管理中改用代理链', '、'.join(dialer_skipped[:8]))

    # 策略组
    group_ids = {}
    proxy_groups = []
    for group in plan['groups']:
        if group['name'] in group_ids or group['name'] in BUILTIN_POLICY_MAP:
            warnings.add('策略组名称重复或与内置策略冲突，已跳过', group['name'])
            continue
        group_ids[group['name']] = _new_id('group')

    used_node_names = set()
    for group in plan['groups']:
        group_id = group_ids.get(group['name'])
        if not group_id or any(g['id'] == group_id for g in proxy_groups):
            continue
        order, manual_nodes, include_groups, subscriptions = [], [], [], []

        def add_node(node_id):
            if node_id not in manual_nodes:
                manual_nodes.append(node_id)
                order.append({'type': 'node', 'id': node_id})

        for member in group['members']:
            if member in group_ids and member != group['name']:
                if group_ids[member] not in include_groups:
                    include_groups.append(group_ids[member])
                    order.append({'type': 'strategy', 'id': group_ids[member]})
            elif member in node_ids:
                add_node(node_ids[member])
                used_node_names.add(member)
            elif member in BUILTIN_POLICY_MAP or member in plan['aliases']:
                add_node(BUILTIN_POLICY_MAP.get(member) or plan['aliases'][member])
            elif member in provider_ids:
                subscriptions.append(provider_ids[member])
            elif member != group['name']:
                warnings.add('策略组成员不存在，已忽略', f'{group["name"]} → {member}')
        for provider in group['providers']:
            if provider in provider_ids and provider_ids[provider] not in subscriptions:
                subscriptions.append(provider_ids[provider])
            elif provider not in provider_ids:
                warnings.add('策略组引用的订阅不存在，已忽略', f'{group["name"]} → {provider}')
        if group['include_all_providers']:
            for sub_id in [*provider_ids.values(), *default_subscription_ids]:
                if sub_id not in subscriptions:
                    subscriptions.append(sub_id)
        if group['include_all_proxies']:
            for name in imported_node_order:
                if _regex_matches(group['filter'], name):
                    add_node(node_ids[name])
                    used_node_names.add(name)
            for sub_id in default_subscription_ids:
                if sub_id not in subscriptions:
                    subscriptions.append(sub_id)
        if not order and not subscriptions:
            warnings.add('策略组没有可用成员，请导入后补充节点或订阅', group['name'])

        result = {
            'id': group_id, 'name': group['name'], 'type': group['type'], 'enabled': True,
            'subscriptions': subscriptions, 'regex': group['filter'] if subscriptions else '',
            'manual_nodes': manual_nodes, 'aggregations': [], 'include_groups': include_groups,
            'proxies_order': order,
        }
        if group['type'] != 'select':
            if group.get('url'):
                result['url'] = group['url']
            if group.get('interval'):
                result['interval'] = group['interval']
        result.update(copy.deepcopy(group['extra']))
        proxy_groups.append(result)

    # 规则集：同 URL 复用，名称冲突改名
    library = shared.get('rule_library', [])
    library_by_url = {item.get('url'): item for item in library if item.get('url')}
    library_names = {item.get('name') for item in library}
    library_ids: Dict[str, str] = {}

    def resolve_ruleset(name):
        if name in library_ids:
            return library_ids[name]
        provider = plan['rule_providers'].get(name)
        if provider is None:
            return None
        if provider.get('url') and provider['url'] in library_by_url:
            library_ids[name] = library_by_url[provider['url']]['id']
            reused['rule_library'] += 1
            return library_ids[name]
        # 规则集名称用于本地缓存文件与订阅链接，冲突时追加 _N 后缀
        final_name = _unique_name(name, library_names, '{base}_{index}')
        if final_name != name:
            warnings.add('规则集与已有规则集重名，已改名', f'{name} → {final_name}')
        library_names.add(final_name)
        item = {'id': _new_id('lib'), 'name': final_name, 'behavior': provider.get('behavior') or 'classical',
                'enabled': True}
        if 'content' in provider:
            item.update(source_type='content', content=provider['content'])
        else:
            item.update(source_type='url', url=provider['url'])
            library_by_url[provider['url']] = item
        added['rule_library'].append(item)
        library_ids[name] = item['id']
        return item['id']

    # 规则目标只能是策略组、已被策略组使用的节点或内置策略
    def resolve_policy(policy):
        if policy in group_ids:
            return policy
        if policy in BUILTIN_POLICY_MAP:
            return BUILTIN_POLICY_MAP[policy]
        if policy in plan['aliases']:
            return plan['aliases'][policy]
        if policy in used_node_names:
            return node_final_names[policy]
        return None

    rule_configs = []
    for rule in plan['rules']:
        policy = resolve_policy(rule['policy'])
        if policy is None:
            warnings.add('规则引用的策略不存在，已跳过', f'{rule["rule_type"]},{rule["value"]},{rule["policy"]}'.replace(',,', ','))
            continue
        if rule['rule_type'] == 'RULE-SET':
            library_id = resolve_ruleset(rule['value'])
            if library_id is None:
                warnings.add('规则集不存在或无法导入，相关规则已跳过', rule['value'])
                continue
            item = {'id': _new_id('ruleset'), 'itemType': 'ruleset', 'library_rule_id': library_id,
                    'policy': policy, 'enabled': True}
        else:
            item = {'id': _new_id('rule'), 'itemType': 'rule', 'rule_type': rule['rule_type'],
                    'value': rule['value'], 'policy': policy, 'enabled': True}
        if rule['no_resolve']:
            item['no_resolve'] = True
        rule_configs.append(item)

    profile_fields = {'proxy_groups': proxy_groups, 'rule_configs': rule_configs}
    if plan['custom_config']:
        engine, text = plan['custom_config']
        profile_fields[engine] = {'custom_config': text}

    summary = {
        'nodes': len(added['nodes']), 'nodes_reused': reused['nodes'],
        'subscriptions': len(added['subscriptions']), 'subscriptions_reused': reused['subscriptions'],
        'rule_library': len(added['rule_library']), 'rule_library_reused': reused['rule_library'],
        'proxy_groups': len(proxy_groups),
        'rules': sum(1 for item in rule_configs if item['itemType'] == 'rule'),
        'rulesets': sum(1 for item in rule_configs if item['itemType'] == 'ruleset'),
        'custom_config': plan['custom_config'][0] if plan['custom_config'] else None,
    }
    return {'shared': added, 'profile': profile_fields, 'summary': summary, 'warnings': warnings.to_list()}
