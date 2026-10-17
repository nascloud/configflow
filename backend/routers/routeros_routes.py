"""Conservative manual IPv4 route editing over RouterOS REST.

The original release rollback helpers intentionally are not used here: a user
can edit the same route through WinBox while this request is in flight.
"""
import copy
import ipaddress
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from backend.routers.base import RouterConflictError, RouterError, RouterRollbackError, is_managed
from backend.routers.routeros import ROUTES, ROUTE_FIELDS, UncertainWrite


RUNTIME_FIELDS = {
    'active', 'inactive', 'immediate-gw', 'belongs-to', 'hw-offloaded', 'debug.fwp-ptr',
    'actual-interface', 'bytes', 'packets', 'uptime', 'last-updated',
}
SPECIAL_FLAGS = {'connect', 'ecmp', 'blackhole', 'unreachable', 'prohibit',
                 'bgp', 'ospf', 'rip', 'dhcp', 'vpn', 'modem', 'local', 'invalid'}
FLAGS = SPECIAL_FLAGS | {'dynamic', 'static', 'disabled', 'suppress-hw-offload'}
KNOWN_FIELDS = ROUTE_FIELDS | RUNTIME_FIELDS | FLAGS | {'.id'}
DEFAULTS = {
    'routing-table': 'main', 'disabled': 'false', 'comment': '', 'distance': '1',
    'scope': '30', 'target-scope': '10', 'check-gateway': 'none', 'pref-src': '',
    'vrf-interface': '', 'suppress-hw-offload': 'false',
    **{key: 'false' for key in SPECIAL_FLAGS | {'dynamic'}}, 'static': 'true',
}
EDIT_FIELDS = ('dst-address', 'gateway', 'disabled', 'comment')


def _flag(value: Any) -> Optional[bool]:
    if type(value) is bool:
        return value
    if isinstance(value, str) and value.lower() in ('true', 'yes', 'false', 'no'):
        return value.lower() in ('true', 'yes')
    return None


def _snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    return {key: copy.deepcopy(value) for key, value in row.items() if key not in RUNTIME_FIELDS}


def _entry(row: Dict[str, Any], duplicate_ids: set) -> Dict[str, Any]:
    row_id = row.get('.id')
    reasons = []
    if not isinstance(row_id, str) or not re.fullmatch(r'\*[0-9a-fA-F]+', row_id):
        reasons.append('路由缺少有效对象 ID')
    elif row_id in duplicate_ids:
        reasons.append('路由对象 ID 不唯一')
    cidr = str(row.get('dst-address') or '')
    try:
        cidr = str(ipaddress.IPv4Network(cidr, strict=False))
    except ValueError:
        reasons.append('仅支持普通 IPv4 目标网段')
    gateway = str(row.get('gateway') or '')
    try:
        gateway = str(ipaddress.IPv4Address(gateway))
    except ValueError:
        reasons.append('仅支持单个 IPv4 下一跳；接口、递归和多路径路由只读')
    table = row.get('routing-table', 'main')
    if table != 'main':
        reasons.append('仅支持 main 主路由表')
    for name in sorted(FLAGS & row.keys()):
        if _flag(row[name]) is None:
            reasons.append(f'{name} 状态无法识别')
    if _flag(row.get('dynamic', 'false')):
        reasons.append('动态路由只读')
    if 'static' in row and _flag(row['static']) is False:
        reasons.append('非静态路由只读')
    if _flag(row.get('connect', 'false')):
        reasons.append('直连路由只读')
    if any(_flag(row.get(key, 'false')) for key in SPECIAL_FLAGS - {'connect'}):
        reasons.append('ECMP、无效或特殊路由只读')
    if row.get('vrf-interface'):
        reasons.append('绑定 VRF 接口的路由只读')
    unknown = sorted(set(row) - KNOWN_FIELDS)
    if unknown:
        reasons.append('包含无法安全恢复的字段：' + '、'.join(unknown))
    if any(not isinstance(value, (str, bool, int)) or isinstance(value, float)
           for key, value in row.items() if key not in RUNTIME_FIELDS):
        reasons.append('路由配置字段格式无法识别')
    comment = row.get('comment', '')
    if not isinstance(comment, str):
        reasons.append('路由备注格式无法识别')
    return {
        'id': row_id if isinstance(row_id, str) else '', 'cidr': cidr, 'gateway': gateway,
        'enabled': _flag(row.get('disabled', 'false')) is False,
        'comment': comment if isinstance(comment, str) else str(comment),
        'table': str(table), 'editable': not reasons, 'reason': '；'.join(dict.fromkeys(reasons)),
        'managed': is_managed(comment if isinstance(comment, str) else ''), 'snapshot': _snapshot(row),
    }


def _entries(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ids = [row.get('.id') for row in rows if isinstance(row.get('.id'), str)]
    seen, duplicates = set(), set()
    for row_id in ids:
        if row_id in seen:
            duplicates.add(row_id)
        seen.add(row_id)
    return [_entry(row, duplicates) for row in rows]


def read_routes(driver) -> List[Dict[str, Any]]:
    return _entries(driver._list(ROUTES))


def _canonical(snapshot: Dict[str, Any], *, ignore_id: bool = False) -> Dict[str, Any]:
    values = {**DEFAULTS, **_snapshot(snapshot)}
    if ignore_id:
        values.pop('.id', None)
    for key, value in values.items():
        if key in FLAGS and _flag(value) is not None:
            values[key] = 'true' if _flag(value) else 'false'
        elif key == 'dst-address':
            try:
                values[key] = str(ipaddress.IPv4Network(value, strict=False))
            except (ValueError, TypeError):
                pass
        elif key in ('distance', 'scope', 'target-scope'):
            values[key] = str(value)
    return values


def _same(row, snapshot, *, ignore_id=False):
    return _canonical(row, ignore_id=ignore_id) == _canonical(snapshot, ignore_id=ignore_id)


def _find(rows, row_id):
    found = [row for row in rows if row.get('.id') == row_id]
    if len(found) > 1:
        raise RouterError('RouterOS 路由 ID 不唯一，请刷新后重试')
    return found[0] if found else None


def _no_duplicate(rows, cidr, exclude_id=None, *, before_write=False):
    if any(item['table'] == 'main' and item['cidr'] == cidr and item['id'] != exclude_id
           for item in _entries(rows)):
        error_type = RouterConflictError if before_write else RouterError
        raise error_type(f'main 主表已存在目标 {cidr} 的路由，请刷新后检查')


def _values(values):
    if (not isinstance(values, dict) or set(values) != {'cidr', 'gateway', 'enabled', 'comment'}
            or type(values['enabled']) is not bool
            or any(not isinstance(values[key], str) for key in ('cidr', 'gateway', 'comment'))):
        raise RouterError('静态路由参数格式无效')
    try:
        cidr = str(ipaddress.IPv4Network(values['cidr'], strict=False))
        gateway = str(ipaddress.IPv4Address(values['gateway']))
    except ValueError:
        raise RouterError('静态路由需要 IPv4 目标网段和单个 IPv4 下一跳') from None
    return {'dst-address': cidr, 'gateway': gateway,
            'disabled': 'false' if values['enabled'] else 'true', 'comment': values['comment']}


def _resource(row_id):
    return f'{ROUTES}/{quote(row_id, safe="*")}'


def _restoration_write(driver, method, path, payload, verify):
    """A lost undo response is successful only when fresh reads prove recovery."""
    error = None
    response = None
    try:
        response = driver._request(method, path, payload)
    except RouterError as caught:
        error = caught
    rows = driver._list(ROUTES)
    if not verify(rows, response):
        raise RouterError(f'恢复后回读不一致{f"（{error}）" if error else ""}')


def _compensate(driver, action, before_rows, before, row_id, after_snapshot):
    rows = driver._list(ROUTES)
    if action == 'add':
        if not row_id:
            # A unique matching new row is not ownership evidence: another
            # operator may have added it while our create response was lost.
            if [_canonical(row) for row in rows] == [_canonical(row) for row in before_rows]:
                return
            raise RouterError('新增响应没有可信对象 ID，无法确认归属；保留现有路由，请刷新检查')
        current = _find(rows, row_id)
        if current is None:
            return
        if not _same(current, after_snapshot):
            raise RouterError('新增路由存在外部变化，未删除该对象')
        _restoration_write(driver, 'DELETE', _resource(row_id), None,
                           lambda latest, _: _find(latest, row_id) is None)
        return
    current = _find(rows, before['id'])
    if current is not None and _same(current, before['snapshot']):
        return  # The failed request did not change this route.
    if action == 'update':
        if current is None or not _same(current, after_snapshot):
            raise RouterError('目标路由存在外部变化，未覆盖或重建该对象')
        _no_duplicate(rows, before['cidr'], before['id'])
        original = {key: before['snapshot'].get(key, DEFAULTS.get(key, '')) for key in EDIT_FIELDS}
        _restoration_write(driver, 'PATCH', _resource(before['id']), original,
                           lambda latest, _: (row := _find(latest, before['id'])) is not None
                           and _same(row, before['snapshot']))
        return
    if current is not None:
        raise RouterError('原路由 ID 已发生外部变化，未覆盖该对象')
    _no_duplicate(rows, before['cidr'])
    original = driver._payload(ROUTES, before['snapshot'])
    prior_ids = {row.get('.id') for row in rows}

    def restored(latest, response):
        candidate_id = response.get('.id') if isinstance(response, dict) else None
        candidates = [row for row in latest if row.get('.id') not in prior_ids
                      and _same(row, before['snapshot'], ignore_id=True)]
        return (len(candidates) == 1
                and (not candidate_id or candidates[0].get('.id') == candidate_id))

    _restoration_write(driver, 'PUT', ROUTES, original, restored)


def mutate_route(driver, action: str, expected: Optional[Dict[str, Any]], values: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if action not in ('add', 'update', 'delete'):
        raise RouterError('不支持的静态路由操作')
    payload = _values(values) if action != 'delete' else None
    before_rows = driver._list(ROUTES)
    before = None
    row_id = None
    after_snapshot = None
    if action == 'add':
        if expected is not None:
            raise RouterError('新增静态路由不应包含旧对象')
        _no_duplicate(before_rows, payload['dst-address'], before_write=True)
        payload['routing-table'] = 'main'
    else:
        if not isinstance(expected, dict) or not isinstance(expected.get('snapshot'), dict):
            raise RouterConflictError('缺少原始路由快照，请刷新后重试')
        before = next((entry for entry in _entries(before_rows) if entry['id'] == expected.get('id')), None)
        if before is None or before != expected:
            raise RouterConflictError('路由在读取后发生变化，请刷新后重试')
        if not before['editable']:
            raise RouterConflictError(before['reason'] or '该路由只读')
        row_id = before['id']
        if action == 'update':
            _no_duplicate(before_rows, payload['dst-address'], row_id, before_write=True)
            after_snapshot = {**before['snapshot'], **payload}
            if _same(before['snapshot'], after_snapshot):
                return {'entry': before, 'changed': False}
    try:
        if action == 'add':
            response = driver._request('PUT', ROUTES, payload)
            candidate_id = response.get('.id') if isinstance(response, dict) else None
            if (not isinstance(candidate_id, str) or not re.fullmatch(r'\*[0-9a-fA-F]+', candidate_id)
                    or any(row.get('.id') == candidate_id for row in before_rows)):
                raise UncertainWrite('RouterOS 新增响应没有可信的新对象 ID')
            row_id = candidate_id
            # A full create response also records server-supplied advanced
            # defaults. A partial response can only prove documented defaults.
            after_snapshot = {**payload, **_snapshot(response), '.id': row_id}
            if any(_canonical(after_snapshot).get(key) != _canonical(payload).get(key) for key in payload):
                raise RouterError('RouterOS 新增响应与提交内容不一致')
        elif action == 'update':
            driver._request('PATCH', _resource(row_id), payload)
        else:
            driver._request('DELETE', _resource(row_id))
        rows = driver._list(ROUTES)
        current = _find(rows, row_id)
        if action == 'delete':
            if current is not None:
                raise RouterError('删除后回读仍发现原路由')
            return {'entry': None, 'changed': True}
        if current is None or not _same(current, after_snapshot):
            raise RouterError('写入后完整回读与预期不一致，可能发生了外部修改')
        entry = _entry(current, set())
        if not entry['editable']:
            raise RouterError('写入后的路由无法继续安全编辑：' + entry['reason'])
        _no_duplicate(rows, entry['cidr'], row_id)
        return {'entry': entry, 'changed': True}
    except RouterError as error:
        try:
            _compensate(driver, action, before_rows, before, row_id, after_snapshot)
        except RouterError as rollback_error:
            raise RouterRollbackError(f'{error}；回滚未完成：{rollback_error}') from error
        raise
