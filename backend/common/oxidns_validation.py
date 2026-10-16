"""One read-only OxiDNS validator for editor diagnostics and publication.

Rules follow the pinned official source recorded in schemas/oxidns. Validation
never inserts defaults, drops unknown keys, or prints configuration values.
The target Agent additionally runs the installed core's static check before
activation; filesystem/network/build availability remains a target concern.
"""
from functools import lru_cache
import ipaddress
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.common.oxidns_settings import OxidnsSettingsError

SCHEMA_ROOT = Path(__file__).resolve().parents[1] / 'schemas' / 'oxidns'
MAX_ISSUES = 200
U64 = 18446744073709551615
ENV = re.compile(r'(?<!\$)\$\{[^}]+\}')


class OxidnsValidationError(OxidnsSettingsError):
    def __init__(self, issues):
        self.issues = issues
        first = next((item for item in issues if item.get('severity', 'error') == 'error'), None)
        location = format_path(first['path']) if first else 'config.yaml'
        super().__init__('OxiDNS 配置校验未通过：' + location + ' ' + (first['message'] if first else '请检查参数'))


def format_path(path):
    return ''.join(('[' + str(part) + ']') if isinstance(part, int) else ('.' if index else '') + str(part)
                   for index, part in enumerate(path)) or 'config.yaml'


@lru_cache(maxsize=1)
def schemas():
    global_schema = json.loads((SCHEMA_ROOT / 'global.json').read_text())
    plugin_schema = json.loads((SCHEMA_ROOT / 'plugins.json').read_text())
    return global_schema['global'], {**global_schema['plugins'], **plugin_schema['plugins']}


@lru_cache(maxsize=1)
def _dns_names():
    return json.loads((SCHEMA_ROOT / 'dns-codes.json').read_text())


def _uint(raw, maximum=U64):
    if not isinstance(raw, str) or not re.fullmatch(r'\+?[0-9]+', raw):
        return None
    digits = raw.lstrip('+').lstrip('0') or '0'
    upper = str(maximum)
    if len(digits) > len(upper) or (len(digits) == len(upper) and digits > upper):
        return None
    return int(digits)


def dns_code_valid(kind, token):
    if isinstance(token, bool) or not isinstance(token, (str, int)):
        return False
    raw = str(token)
    if re.fullmatch(r'\+?[0-9]+', raw):
        return _uint(raw, 15 if kind in ('base-rcode', 'dns-base-rcode') else 65535) is not None
    key = kind.removeprefix('dns-')
    if key == 'base-rcode':
        return raw.upper() in _dns_names()['rcode'][:11]
    return raw.upper() in _dns_names().get(key, [])


def _endpoint(value, *, listen=False, socks=False):
    if not isinstance(value, str):
        return False
    raw = value.strip() if socks else value
    if socks and '@' in raw:
        credentials, raw = raw.rsplit('@', 1)
        if ':' not in credentials:
            return False
    if ':' not in raw:
        return False
    host, port = raw.rsplit(':', 1)
    if _uint(port, 65535) is None:
        return False
    if socks:
        if host.startswith('[') or host.endswith(']'):
            if not (host.startswith('[') and host.endswith(']')):
                return False
            host = host[1:-1]
        return bool(host)
    if not host:
        return listen
    try:
        address = ipaddress.ip_address(host[1:-1] if host.startswith('[') and host.endswith(']') else host)
        return '%' not in host and (address.version == 4 or host.startswith('['))
    except ValueError:
        return False


def format_error(name, value):
    """Return a safe, value-free diagnostic; schema types run before formats."""
    if value is None or (isinstance(value, str) and ENV.search(value)):
        return None
    if name in ('non-empty-string', 'nonempty-string'):
        return None if isinstance(value, str) and value.strip() else '必须填写非空文本'
    if name == 'duration':
        if type(value) is int:
            valid = 0 <= value <= U64
        else:
            match = re.fullmatch(r'([0-9]+)\s*(ms|s|m|h|d)?', value.strip(), re.I) if isinstance(value, str) else None
            valid = bool(match) and _uint(match[1]) is not None
        return None if valid else '时长须为非负整数秒或整数加 ms、s、m、h、d 单位'
    if name in ('ip', 'ip-address', 'ip-network'):
        try:
            if not isinstance(value, str) or '%' in value:
                raise ValueError()
            (ipaddress.ip_network(value, strict=False) if name == 'ip-network' else ipaddress.ip_address(value))
            return None
        except ValueError:
            return '必须为有效 IP/CIDR' if name == 'ip-network' else '必须为有效 IP 地址'
    if name in ('socket-address', 'socks5'):
        return None if _endpoint(value, listen=name == 'socket-address', socks=name == 'socks5') else '地址格式无效，请检查主机、端口及 IPv6 方括号'
    if name in ('upstream-address', 'http-url'):
        try:
            if not isinstance(value, str) or any(c.isspace() for c in value):
                raise ValueError()
            raw = value if '//' in value or name == 'http-url' else 'udp://' + value
            parts = urlsplit(raw)
            schemes = ('http', 'https') if name == 'http-url' else ('udp', 'tcp', 'tls', 'quic', 'doq', 'https', 'doh', 'h3', 'tcp+pipeline', 'tls+pipeline')
            if not parts.hostname or parts.scheme not in schemes:
                raise ValueError()
            parts.port
            return None
        except (ValueError, TypeError):
            return 'URL/上游地址无效或协议不受支持'
    if name == 'http-method':
        return None if isinstance(value, str) and re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", value.strip()) else 'HTTP 方法须为有效的 token'
    if name == 'timezone':
        try:
            ZoneInfo(value.strip())
            return None
        except (ZoneInfoNotFoundError, ValueError, TypeError, AttributeError):
            return '时区须为有效 IANA 时区，例如 Asia/Shanghai'
    if name.startswith('dns-') and name != 'dns-record':
        return None if dns_code_valid(name, value) else 'DNS 类型/类别/响应码无效或超出允许范围'
    if name == 'plugin-tag':
        from backend.common.oxidns_native import TAG
        return None if isinstance(value, str) and 0 < len(value) <= 64 and TAG.fullmatch(value) and not value.startswith(('qs.exec.', 'qs.match.', 'qs.cron.')) else '需要有效的 1–64 位插件标识'
    if name in ('dns-record', 'domain-expression'):
        from backend.common.oxidns_plugin_constraints import validate_plugin_format
        return validate_plugin_format(name, value)
    raise RuntimeError('Unsupported OxiDNS schema format: ' + name)


def _is_type(value, kind):
    return {'object': isinstance(value, dict), 'array': isinstance(value, list),
            'string': isinstance(value, str), 'integer': type(value) is int,
            'number': type(value) is int or (type(value) is float and math.isfinite(value)),
            'boolean': type(value) is bool, 'null': value is None}.get(kind, False)


def validate_schema(value, schema, path=()):
    issues = []

    def add(message, code, severity='error', at=path):
        issues.append({'path': list(at), 'message': message, 'code': code, 'severity': severity})

    if schema.get('x-ignored-args'):
        return issues
    if isinstance(value, str) and ENV.search(value):
        add('含动态占位符，实际值需由目标环境解析；静态校验无法确认展开后的参数', 'deferred', 'warning')
        return issues
    if 'anyOf' in schema:
        variants = [validate_schema(value, option, path) for option in schema['anyOf']]
        valid = next((items for items in variants if not any(i['severity'] == 'error' for i in items)), None)
        if valid is not None:
            return valid
        # Prefer the matching structural alternative for useful nested paths.
        candidates = [(option, items) for option, items in zip(schema['anyOf'], variants)
                      if not option.get('type') or any(_is_type(value, kind) for kind in
                          (option['type'] if isinstance(option['type'], list) else [option['type']]))]
        def rank(candidate):
            option, items = candidate
            matches = sum(1 for key, field in option.get('properties', {}).items()
                          if isinstance(value, dict) and key in value and 'enum' in field
                          and value[key] in field['enum'])
            return -matches, sum(i['severity'] == 'error' for i in items)
        return min(candidates or list(zip(schema['anyOf'], variants)), key=rank)[1]
    kinds = schema.get('type')
    if kinds and not any(_is_type(value, kind) for kind in (kinds if isinstance(kinds, list) else [kinds])):
        add('参数类型无效，需要 ' + ' / '.join(kinds if isinstance(kinds, list) else [kinds]), 'type')
        return issues
    if 'enum' in schema and not any(type(value) is type(option) and value == option for option in schema['enum']):
        add('不在该参数允许的选项中', 'enum')
    if value is None:
        return issues
    if type(value) in (int, float):
        for key, fails, label in (('minimum', lambda a,b: a < b, '不能小于'), ('maximum', lambda a,b: a > b, '不能大于'), ('exclusiveMinimum', lambda a,b: a <= b, '必须大于')):
            if key in schema and fails(value, schema[key]):
                add(label + ' ' + str(schema[key]), 'range')
    if isinstance(value, str) and len(value) < schema.get('minLength', 0):
        add('文本长度不足', 'length')
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0):
            add('至少需要 ' + str(schema['minItems']) + ' 个条目', 'required')
        if 'maxItems' in schema and len(value) > schema['maxItems']:
            add('条目数量超过 ' + str(schema['maxItems']), 'length')
        for index, item in enumerate(value):
            issues.extend(validate_schema(item, schema.get('items', {}), [*path, index]))
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        for key in schema.get('required', []):
            if key not in value:
                add('缺少必填参数', 'required', at=[*path, key])
        for key, item in value.items():
            if key in properties:
                issues.extend(validate_schema(item, properties[key], [*path, key]))
            elif isinstance(schema.get('additionalProperties'), dict):
                issues.extend(validate_schema(item, schema['additionalProperties'], [*path, key]))
            elif schema.get('additionalProperties') is False:
                add('官方核心不接受此对象中的未知字段', 'unknown-field', at=[*path, key])
            elif 'properties' in schema:
                add('扩展字段已保留，不在当前版本的校验目录中', 'unknown-field', 'warning', [*path, key])
    if schema.get('format'):
        error = format_error(schema['format'], value)
        if error:
            add(error, 'format')
    return sorted(issues, key=lambda item: item.get('severity', 'error') != 'error')[:MAX_ISSUES]


def validate_native_config(document):
    global_schema, plugins = schemas()
    issues = validate_schema(document, global_schema)
    if not isinstance(document, dict):
        return issues
    # Plugin contents remain opaque to the global schema.
    for index, plugin in enumerate(document.get('plugins', []) if isinstance(document.get('plugins', []), list) else []):
        path = ['plugins', index]
        issues.extend(validate_schema(plugin, {'type': 'object', 'required': ['tag', 'type'], 'properties': {
            'tag': {'type': 'string', 'format': 'plugin-tag'}, 'type': {'type': 'string', 'minLength': 1}, 'args': {}}}, path))
        if not isinstance(plugin, dict):
            continue
        kind = plugin.get('type')
        schema = plugins.get(kind) if isinstance(kind, str) else None
        if schema is None:
            issues.append({'path': [*path, 'type'], 'message': '插件未在当前校验目录中，发布时需由目标核心确认支持', 'code': 'unknown-plugin', 'severity': 'warning'})
            continue
        if 'args' not in plugin and not schema.get('x-args-optional'):
            issues.append({'path': [*path, 'args'], 'message': '缺少插件必填参数', 'code': 'required', 'severity': 'error'})
        elif 'args' in plugin:
            issues.extend(validate_schema(plugin['args'], schema, [*path, 'args']))
        from backend.common.oxidns_plugin_constraints import collect_plugin_issues
        issues.extend(collect_plugin_issues(plugin, index, document))
    from backend.common.oxidns_global_constraints import collect_global_issues
    from backend.common.oxidns_reference_validation import collect_reference_issues
    issues.extend(collect_global_issues(document))
    issues.extend(collect_reference_issues(document, plugins))
    result, seen = [], set()
    for item in sorted(issues, key=lambda item: item.get('severity', 'error') != 'error'):
        item.setdefault('severity', 'error')
        key = (tuple(item['path']), item['message'], item['severity'])
        if key not in seen:
            result.append(item)
            seen.add(key)
        if len(result) == MAX_ISSUES:
            break
    return result


def require_valid_document(document):
    issues = validate_native_config(document)
    if any(item['severity'] == 'error' for item in issues):
        raise OxidnsValidationError(issues)
    return issues


def validate_expanded_document(document, sources):
    issues = validate_native_config(document)
    for item in issues:
        item['source'] = 'config.yaml'
        path = item['path']
        if len(path) > 1 and path[0] == 'plugins' and isinstance(path[1], int):
            origin = sources.get('plugins', [])[path[1]]
            item['source'] = origin['source']
            item['path'] = ['plugins', origin['index'], *path[2:]]
    # The core also deserializes globals in includes, even though they do not
    # override the main document. Validate their types without changing them.
    global_schema, _ = schemas()
    for name, included in sources.get('includes', []):
        for item in validate_schema(included, global_schema):
            issues.append({**item, 'source': name})
    return sorted(issues, key=lambda item: item.get('severity', 'error') != 'error')[:MAX_ISSUES]


def validate_settings(settings, config=None, base_url=''):
    """Non-persistent editor diagnostics, identical to the publication rules."""
    from copy import deepcopy
    from backend.common.oxidns_native import native_document
    from backend.common.oxidns_editor import prepare_editor
    try:
        draft = prepare_editor(settings, config or {}, base_url)
        sources = {}
        document = native_document(draft, validate_arguments=False, sources=sources)
        issues = validate_expanded_document(document, sources)
    except OxidnsValidationError as exc:
        issues = deepcopy(exc.issues)
    except OxidnsSettingsError as exc:
        issues = getattr(exc, 'issues', [{'path': [], 'message': str(exc), 'code': 'structure', 'severity': 'error', 'source': 'config.yaml'}])
    return {'valid': not any(i['severity'] == 'error' for i in issues), 'issues': issues,
            'schema_version': '1.6.1', 'core_check_required': True}
