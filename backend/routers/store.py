"""Profile-scoped router definitions (``profile['routers']['items']``)."""
import secrets
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

ROUTER_TYPES = ('ikuai', 'routeros')
EDITABLE_FIELDS = ('name', 'type', 'endpoint', 'username', 'verify_tls', 'proxy_gateway_agent_id', 'dns_agent_id',
                   'ip_rule_sets')


class RouterValidationError(ValueError):
    pass


def routers_of(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    routers = config.get('routers')
    if not isinstance(routers, dict) or not isinstance(routers.get('items'), list):
        return []
    return [item for item in routers['items'] if isinstance(item, dict)]


def find_router(config: Dict[str, Any], router_id: str) -> Optional[Dict[str, Any]]:
    return next((item for item in routers_of(config) if item.get('id') == router_id), None)


def public_view(router: Dict[str, Any]) -> Dict[str, Any]:
    """Router as returned to the UI: never expose the password."""
    result = {key: router.get(key) for key in ('id', *EDITABLE_FIELDS, 'last_probe', 'last_release')}
    result['has_password'] = bool(router.get('password'))
    return result


def normalize(payload: Dict[str, Any], existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise RouterValidationError('路由器参数必须是对象')
    router = dict(existing or {})
    for key in EDITABLE_FIELDS:
        if key in payload:
            router[key] = payload[key]
    password = payload.get('password')
    if isinstance(password, str) and password:
        router['password'] = password          # empty keeps the stored password
    elif not existing:
        router['password'] = ''

    router['name'] = str(router.get('name') or '').strip()
    router['type'] = str(router.get('type') or '')
    router['endpoint'] = str(router.get('endpoint') or '').strip().rstrip('/')
    router['username'] = str(router.get('username') or '').strip()
    router['verify_tls'] = router.get('verify_tls') is not False
    for key in ('proxy_gateway_agent_id', 'dns_agent_id'):
        router[key] = str(router.get(key) or '')
    rule_sets = router.get('ip_rule_sets') or []
    if not isinstance(rule_sets, list) or any(not isinstance(item, str) or not item for item in rule_sets):
        raise RouterValidationError('IP 路由规则集必须是规则集 ID 列表')
    router['ip_rule_sets'] = list(dict.fromkeys(rule_sets))

    if not router['name']:
        raise RouterValidationError('请填写名称')
    if router['type'] not in ROUTER_TYPES:
        raise RouterValidationError('路由器类型只支持 iKuai 4.x 或 RouterOS')
    parsed = urlparse(router['endpoint'])
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.path not in ('', '/'):
        raise RouterValidationError('管理地址格式应为 http(s)://主机[:端口]')
    if not router['username']:
        raise RouterValidationError('请填写用户名')
    if not router.get('password'):
        raise RouterValidationError('请填写密码')
    router.setdefault('id', f'router_{secrets.token_hex(6)}')
    return router


def redact_routers(routers: Any) -> Any:
    """Desensitized export: drop credentials and management endpoints."""
    if not isinstance(routers, dict):
        return routers
    items = []
    for item in routers.get('items') or []:
        if isinstance(item, dict):
            clean = {key: value for key, value in item.items() if key not in ('password', 'last_probe', 'last_release')}
            if clean.get('username'):
                clean['username'] = '[REDACTED]'
            if clean.get('endpoint'):
                clean['endpoint'] = '[REDACTED]'
            items.append(clean)
    return {**routers, 'items': items}
