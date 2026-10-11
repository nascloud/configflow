"""Explicit health-check defaults; legacy records are never mutated."""
PROVIDER_DEFAULTS = {
    'enable': True,
    'url': 'https://www.gstatic.com/generate_204',
    'interval': 300,
    'timeout': 5000,
    'lazy': True,
    'expected-status': '204',
}


GROUP_HEALTH_TYPES = ('url-test', 'fallback', 'load-balance')
GROUP_DEFAULTS = {
    'url': 'https://www.gstatic.com/generate_204',
    'interval': 60,
    'timeout': 5000,
    'lazy': False,
    'expected-status': '204',
}


def initialize_group_health(group):
    """Creation only. Never apply these defaults while loading/editing legacy groups."""
    if group.get('type') in GROUP_HEALTH_TYPES:
        for key, value in GROUP_DEFAULTS.items():
            group.setdefault(key, value)
        if group['type'] == 'url-test':
            group.setdefault('tolerance', 50)


def initialize_provider_health(source):
    """Creation defaults, preserving the legacy API's explicit probe URL."""
    if 'health_check' not in source:
        source['health_check'] = dict(PROVIDER_DEFAULTS)
        if source.get('health_check_url'):
            source['health_check']['url'] = source['health_check_url']


def validate_health(values, *, provider=False):
    """Validate persisted engine fields without coercion or implicit migration."""
    from urllib.parse import urlsplit
    import re
    if not isinstance(values, dict):
        raise ValueError('健康检查必须是对象')
    allowed = set(PROVIDER_DEFAULTS) if provider else set(GROUP_DEFAULTS) | {'tolerance'}
    if provider and set(values) - allowed:
        raise ValueError('健康检查包含未知字段')
    for key in allowed & values.keys():
        value = values[key]
        if key in ('enable', 'lazy'):
            if type(value) is not bool:
                raise ValueError(f'{key} 必须为布尔值')
        elif key in ('interval', 'timeout', 'tolerance'):
            minimum = 0 if key == 'tolerance' else 1
            maximum = {'interval': 86400, 'timeout': 600000, 'tolerance': 600000}[key]
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f'{key} 必须为 {minimum}–{maximum} 的整数')
        elif key == 'url':
            if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
                raise ValueError('探测 URL 必须是有效 HTTP(S) 地址')
            try:
                parsed = urlsplit(value)
                port = parsed.port
                valid = (parsed.scheme in ('http', 'https') and parsed.hostname
                         and parsed.username is None and parsed.password is None
                         and not parsed.fragment and (port is None or 1 <= port <= 65535))
            except ValueError:
                valid = False
            if not valid:
                raise ValueError('探测 URL 必须是无凭据的 HTTP(S) 地址')
        elif key == 'expected-status':
            if not isinstance(value, str) or not re.fullmatch(r'[0-9]{3}(?:-[0-9]{3})?(?:/[0-9]{3}(?:-[0-9]{3})?)*', value):
                raise ValueError('expected-status 格式应为 204 或 200-299/304')
            for item in value.split('/'):
                bounds = [int(n) for n in item.split('-')]
                if not all(100 <= n <= 599 for n in bounds) or bounds[0] > bounds[-1]:
                    raise ValueError('expected-status 范围必须在 100–599 内且递增')


def provider_health_check(source):
    validate_health(source['health_check'], provider=True)
    return {**PROVIDER_DEFAULTS, **source['health_check']}
