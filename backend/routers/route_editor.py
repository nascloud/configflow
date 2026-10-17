"""Explicit single-route editing, independent of generated deployments.

A preview binds user intent to the router connection and the exact stable route
snapshot. Drivers re-read immediately before writing, verify afterward, and only
compensate resources they can still identify as this operation's own change.
"""
from contextlib import contextmanager
import hashlib
import ipaddress
import json
import threading
from typing import Any, Dict

from backend.routers.base import RouterConflictError, RouterError, is_managed


class RouteEditValidationError(ValueError):
    pass


class RouteEditConflict(RouterConflictError):
    pass


_locks = {}
_locks_guard = threading.Lock()


@contextmanager
def router_operation_lock(router):
    # Serialize our own manual/automatic operations on the same connection.
    # Vendor APIs have no conditional-write primitive: external writers still
    # require the driver's immediate read-before-write and conservative undo.
    key = (router.get('type'), str(router.get('endpoint') or '').rstrip('/'))
    with _locks_guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        yield


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def public_route(row):
    fields = ('id', 'cidr', 'gateway', 'enabled', 'comment', 'table', 'editable', 'reason', 'managed')
    result = {key: row[key] for key in fields}
    result['revision'] = _digest({'fields': result, 'snapshot': row['snapshot']})
    return result


def _adapter(driver):
    if driver.vendor == 'routeros':
        from backend.routers import routeros_routes
        return routeros_routes
    if driver.vendor == 'ikuai':
        from backend.routers import ikuai_routes
        return ikuai_routes
    raise RouterError('该路由器暂不支持手工路由管理')


def read_routes(driver):
    rows = _adapter(driver).read_routes(driver)
    if (not isinstance(rows, list)
            or any(not isinstance(row, dict) or not isinstance(row.get('id'), str)
                   or not row['id'] or not isinstance(row.get('snapshot'), dict) for row in rows)
            or len({row['id'] for row in rows}) != len(rows)):
        raise RouterError('路由列表缺少完整、唯一的对象信息，停止操作')
    return rows


def _values(raw):
    fields = {'cidr', 'gateway', 'enabled', 'comment'}
    if not isinstance(raw, dict) or raw.keys() != fields:
        raise RouteEditValidationError('请提供网段、下一跳、启用状态和备注，不能包含其他字段')
    if not isinstance(raw['cidr'], str) or not isinstance(raw['gateway'], str):
        raise RouteEditValidationError('网段和下一跳必须为 IPv4 地址文本')
    try:
        cidr = str(ipaddress.IPv4Network(raw['cidr'].strip(), strict=False))
        gateway = ipaddress.IPv4Address(raw['gateway'].strip())
    except (ValueError, TypeError):
        raise RouteEditValidationError('网段必须为有效 IPv4 网段，下一跳必须为单个 IPv4 地址') from None
    if gateway.is_unspecified or gateway.is_multicast or gateway.is_loopback or int(gateway) == 0xffffffff:
        raise RouteEditValidationError('下一跳必须为可用的单播 IPv4 地址')
    if type(raw['enabled']) is not bool:
        raise RouteEditValidationError('启用状态必须为 true 或 false')
    if not isinstance(raw['comment'], str) or len(raw['comment']) > 512 or '\x00' in raw['comment']:
        raise RouteEditValidationError('备注最多 512 个字符，且不能包含空字符')
    # Match the vendor form's outer whitespace normalization and preview it.
    return {'cidr': cidr, 'gateway': str(gateway), 'enabled': raw['enabled'],
            'comment': raw['comment'].strip()}


def normalize_request(payload):
    if not isinstance(payload, dict) or set(payload) - {'action', 'id', 'revision', 'values', 'fingerprint'}:
        raise RouteEditValidationError('路由操作参数格式错误')
    action = payload.get('action')
    if action not in ('add', 'update', 'delete'):
        raise RouteEditValidationError('只支持添加、编辑或删除单条路由')
    result = {'action': action}
    if action != 'add':
        row_id, revision = payload.get('id'), payload.get('revision')
        if (not isinstance(row_id, str) or not row_id or len(row_id) > 128
                or any(ord(char) < 32 for char in row_id)):
            raise RouteEditValidationError('请选择有效的路由记录')
        if not isinstance(revision, str) or len(revision) != 64 or any(c not in '0123456789abcdef' for c in revision):
            raise RouteEditValidationError('缺少路由原始版本，请刷新列表后重试')
        result.update(id=row_id, revision=revision)
    elif payload.get('id') or payload.get('revision'):
        raise RouteEditValidationError('新增路由不能指定已有路由的身份')
    if action != 'delete':
        result['values'] = _values(payload.get('values'))
    elif payload.get('values') is not None:
        raise RouteEditValidationError('删除操作不能同时修改路由字段')
    return result


def build_route_plan(router: Dict[str, Any], rows, payload):
    intent = normalize_request(payload)
    action = intent['action']
    current = None
    if action != 'add':
        current = next((row for row in rows if row['id'] == intent['id']), None)
        if current is None:
            raise RouteEditConflict('该路由已不存在，请刷新列表后重试')
        if public_route(current)['revision'] != intent['revision']:
            raise RouteEditConflict('路由已被其他操作修改，请刷新列表后重新预览')
        if not current['editable']:
            raise RouteEditConflict(current.get('reason') or '该路由为只读，不能修改或删除')
    values = intent.get('values')
    if values is not None:
        duplicate = next((row for row in rows if row['cidr'] == values['cidr']
                          and row.get('table', 'main') == 'main'
                          and (current is None or row['id'] != current['id'])), None)
        if duplicate:
            raise RouteEditConflict(f'主路由表中已存在 {values["cidr"]}，请编辑已有路由，避免重复添加')
    before = public_route(current) if current else None
    after = ({**(before or {'id': '', 'table': 'main'}), **values,
              'managed': is_managed(values['comment'])} if values is not None else None)
    empty = bool(before and after and all(before[key] == after[key] for key in values))
    label = {'add': '添加', 'update': '编辑', 'delete': '删除'}[action]
    description = after or before
    summary = f'{label}静态路由 {description["cidr"]} → {description["gateway"]}'
    warnings = []
    if (before and before['managed']) or (after and after['managed']):
        warnings.append('此路由带有 ConfigFlow 管理标记；后续自动发布可能接管、调整或删除它。手工维护的路由建议移除备注中的 cf 管理前缀。')
    fingerprint = _digest({
        'protocol': 1,
        'connection': {key: router.get(key) for key in ('id', 'type', 'endpoint', 'username', 'verify_tls')},
        'intent': intent, 'before': before, 'after': after,
    })
    return {'action': action, 'id': intent.get('id'), 'values': values,
            'before': before, 'after': after, 'summary': summary, 'warnings': warnings,
            'fingerprint': fingerprint, 'empty': empty}


def execute_route_plan(driver, rows, plan):
    if plan['empty']:
        return {'changed': False, 'entry': plan['before']}
    expected = next((row for row in rows if row['id'] == plan['id']), None) if plan['id'] else None
    result = _adapter(driver).mutate_route(driver, plan['action'], expected, plan['values'])
    entry = result.get('entry')
    return {'changed': result.get('changed') is True, 'entry': public_route(entry) if entry else None}
