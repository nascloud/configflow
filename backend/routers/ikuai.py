"""iKuai 4.x driver.

iKuai has no public API; this uses the same endpoints as its web UI:
``POST /Action/login`` then ``POST /Action/call`` with ``func_name``/``action``.
Only 4.x is supported: responses are ``{"code": 0, "results": {...}}``.
Write payloads mirror the web UI forms, including its string encoding.
"""
import base64
import hashlib
import ipaddress
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

import requests

from backend.routers.base import (
    NatBypassEntry, RouteEntry, RouterDriver, RouterError, RouterInfo, RouterState,
)

TIMEOUT = 10
LIST_LIMIT = '0,1000'

# Escapes used by the web UI besides '%' (escaped first) and whitespace (-> %20).
_ENCODE = [('"', '%22'), ('`', '%60'), ('$', '%24'), ('&', '%26'), (';', '%3B'),
           ('\\', '%5C'), ('|', '%7C'), ("'", '%27')]
ROUTE_FIELDS = ('id', 'tagname', 'ip_type', 'interface', 'dst_addr', 'gateway', 'netmask', 'prio', 'comment')
NAT_FIELDS = ('id', 'enabled', 'tagname', 'comment', 'action', 'protocol', 'iinterface', 'ointerface',
              'src_addr', 'dst_addr', 'src_port', 'dst_port', 'nat_addr', 'nat_port', 'src_addr_inv', 'dst_addr_inv')


def encode_value(value: Any, key: str = '') -> Any:
    """Encode strings like the iKuai UI (``$("encode", form)``) before add/edit calls."""
    if isinstance(value, dict):
        return {k: encode_value(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [encode_value(item) for item in value]
    if not isinstance(value, str):
        return value
    text = value.strip() if key == 'comment' else value
    text = text.replace('%', '%25')
    if key == 'comment':
        text = text.replace('\r\n', '%0A').replace('\n', '%0A').replace('\r', '%0A')
    text = ''.join(' ' if char.isspace() else char for char in text).replace(' ', '%20')
    for raw, encoded in _ENCODE:
        text = text.replace(raw, encoded)
    return text


def decode_value(value: Any, key: str = '') -> Any:
    if isinstance(value, dict):
        return {k: decode_value(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [decode_value(item) for item in value]
    if not isinstance(value, str):
        return value
    text = value.replace('%0A', '\n').replace('%0a', '\n') if key == 'comment' else value
    text = text.replace('%20', ' ')
    for raw, encoded in _ENCODE:
        text = text.replace(encoded, raw).replace(encoded.lower(), raw)
    return text.replace('%2F', '/').replace('%2f', '/').replace('%25', '%')


def _address_field(value: Any) -> Dict[str, Any]:
    """iKuai returns empty address lists as ``{}``; the forms send ``[]``."""
    value = value if isinstance(value, dict) else {}
    return {key: value.get(key) if isinstance(value.get(key), list) else [] for key in ('custom', 'object')}


class IKuaiDriver(RouterDriver):
    vendor = 'ikuai'
    supports_write = True

    def __init__(self, endpoint: str, username: str, password: str, verify_tls: bool = True):
        self.endpoint = endpoint.rstrip('/')
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.verify = verify_tls
        self._logged_in = False

    def close(self):
        self.session.close()

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = self.session.post(f'{self.endpoint}{path}', json=payload,
                                         timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException as error:
            raise RouterError(f'无法连接 iKuai：{error.__class__.__name__}') from None
        if response.status_code != 200:
            raise RouterError(f'iKuai 返回 HTTP {response.status_code}')
        try:
            data = response.json()
        except ValueError:
            raise RouterError('iKuai 返回的不是 JSON，请确认地址指向管理页面') from None
        if not isinstance(data, dict):
            raise RouterError('iKuai 返回格式无法识别')
        return data

    def _login(self):
        if self._logged_in:
            return
        payload = {
            'username': self.username,
            'passwd': hashlib.md5(self.password.encode()).hexdigest(),
            'pass': base64.b64encode(f'salt_11{self.password}'.encode()).decode(),
        }
        data = self._post('/Action/login', payload)
        if data.get('Result') != 10000 and data.get('code') != 0:
            raise RouterError(f'iKuai 登录失败：{data.get("message") or "账号或密码错误"}')
        self._logged_in = True

    def call(self, func_name: str, param: Dict[str, Any], action: str = 'show') -> Dict[str, Any]:
        self._login()
        data = self._post('/Action/call', {'func_name': func_name, 'action': action, 'param': param})
        if data.get('code') == 1003:
            raise RouterError(f'iKuai 拒绝访问 {func_name}（需要管理员账号）')
        if data.get('code') != 0 or not isinstance(data.get('results'), dict):
            if 'Data' in data or 'Result' in data:
                raise RouterError('检测到 iKuai 3.x 的响应格式，只支持 iKuai 4.x')
            raise RouterError(f'iKuai 调用 {func_name} 失败：{data.get("message") or data.get("code")}')
        return data['results']

    def _list(self, func_name: str) -> List[Dict[str, Any]]:
        results = self.call(func_name, {'TYPE': 'total,data', 'limit': LIST_LIMIT})
        rows = results.get('data') or []
        return [row for row in rows if isinstance(row, dict)]

    def probe(self) -> RouterInfo:
        verinfo = self.call('sysstat', {'TYPE': 'verinfo'}).get('verinfo') or {}
        version = str(verinfo.get('version') or '')
        if not version.startswith('4.'):
            raise RouterError(f'只支持 iKuai 4.x，当前版本 {version or "未知"}')
        capabilities = []
        for func, capability in (('static_rt', 'routes'), ('nat_rule', 'nat_bypass'), ('route_object', 'address_sets')):
            try:
                self._list(func)
                capabilities.append(capability)
            except RouterError:
                pass
        return RouterInfo(vendor='ikuai', version=version,
                          model=str(verinfo.get('verstring') or ''), capabilities=capabilities)

    def read_state(self) -> RouterState:
        state = RouterState()
        for row in self._list('static_rt'):
            try:
                network = ipaddress.ip_network(f'{row.get("dst_addr")}/{row.get("netmask")}', strict=False)
            except ValueError:
                continue
            state.routes.append(RouteEntry(
                id=str(row.get('id', '')), dst=str(network), gateway=str(row.get('gateway') or ''),
                enabled=row.get('enabled') == 'yes', comment=unquote(str(row.get('comment') or ''))))
        groups = {str(row.get('group_id')): row for row in self._list('route_object')}
        for row in groups.values():
            state.address_sets[str(row.get('group_name') or row.get('tagname'))] = [
                str(item.get('ip')) for item in row.get('group_value') or [] if isinstance(item, dict) and item.get('ip')]
        for row in self._list('nat_rule'):
            if row.get('action') != 'filter':
                continue
            state.nat_bypass.append(NatBypassEntry(
                id=str(row.get('id', '')), dst=_addresses(row.get('dst_addr'), state.address_sets),
                src=_addresses(row.get('src_addr'), state.address_sets),
                enabled=row.get('enabled') == 'yes', comment=unquote(str(row.get('comment') or ''))))
        return state


    # --- writes -------------------------------------------------------------

    def _write(self, func_name: str, action: str, param: Dict[str, Any]) -> Dict[str, Any]:
        self._login()
        data = self._post('/Action/call', {'func_name': func_name, 'action': action, 'param': encode_value(param)})
        if data.get('code') != 0:
            raise RouterError(f'iKuai {func_name} {action} 失败：{data.get("message") or data.get("code")}')
        return data.get('results') if isinstance(data.get('results'), dict) else {}

    def _row(self, func_name: str, row_id: Any) -> Dict[str, Any]:
        for row in self._list(func_name):
            if str(row.get('id')) == str(row_id):
                return decode_value(row)
        raise RouterError(f'iKuai {func_name} 中找不到 id={row_id}，可能已被手动删除')

    def _find_id(self, func_name: str, match) -> str:
        for row in self._list(func_name):
            if match(decode_value(row)):
                return str(row.get('id'))
        raise RouterError(f'iKuai {func_name} 已提交但读不回新对象')

    def _route_payload(self, row: Dict[str, Any], **changes) -> Dict[str, Any]:
        payload = {key: row.get(key) for key in ROUTE_FIELDS if key in row}
        payload.update(changes)
        payload['netmask'] = str(payload.get('netmask') or '').split(' ')[0]
        return payload

    def _nat_payload(self, row: Dict[str, Any], **changes) -> Dict[str, Any]:
        payload = {key: row.get(key) for key in NAT_FIELDS if key in row}
        for key in ('src_addr', 'dst_addr', 'src_port', 'dst_port'):
            payload[key] = _address_field(payload.get(key))
        payload.update(changes)
        return payload

    def _add_route(self, cidr: str, gateway: str, comment: str) -> str:
        network = ipaddress.IPv4Network(cidr)
        self._write('static_rt', 'add', {
            'tagname': '', 'ip_type': '4', 'interface': 'auto', 'dst_addr': str(network.network_address),
            'gateway': gateway, 'netmask': str(network.netmask), 'prio': 1, 'comment': comment})
        return self._find_id('static_rt', lambda row: row.get('dst_addr') == str(network.network_address)
                             and row.get('netmask') == str(network.netmask) and row.get('gateway') == gateway
                             and row.get('comment') == comment)

    def apply(self, op: Dict[str, Any]) -> Dict[str, Any]:
        kind = op['kind']
        if kind == 'route_add':
            route_id = self._add_route(op['cidr'], op['gateway'], op['comment'])
            return {'kind': 'route_delete', 'id': route_id}
        if kind == 'route_delete':
            row = self._row('static_rt', op['id'])
            self._write('static_rt', 'del', {'id': row['id']})
            network = ipaddress.ip_network(f'{row["dst_addr"]}/{row["netmask"]}', strict=False)
            return {'kind': 'route_add', 'cidr': str(network), 'gateway': row.get('gateway'),
                    'comment': row.get('comment') or ''}
        if kind in ('route_comment', 'route_restore'):
            row = self._row('static_rt', op['id'] if kind == 'route_comment' else op['record']['id'])
            payload = (self._route_payload(row, comment=op['comment']) if kind == 'route_comment'
                       else self._route_payload(op['record']))
            self._write('static_rt', 'edit', payload)
            return {'kind': 'route_restore', 'record': row}
        if kind == 'nat_update':
            row = self._row('nat_rule', op['id'])
            dst = _address_field(row.get('dst_addr'))
            dst['custom'] = dst['custom'] + [cidr for cidr in op.get('add_dst') or [] if cidr not in dst['custom']]
            self._write('nat_rule', 'edit', self._nat_payload(row, dst_addr=dst, comment=op['comment']))
            return {'kind': 'nat_restore', 'record': row}
        if kind == 'nat_restore':
            row = self._row('nat_rule', op['record']['id'])
            self._write('nat_rule', 'edit', self._nat_payload(op['record']))
            return {'kind': 'nat_restore', 'record': row}
        if kind == 'nat_add':
            self._write('nat_rule', 'add', self._nat_payload({}, enabled='yes', tagname='', action='filter',
                                                             protocol='any', iinterface='any', ointerface='any',
                                                             src_addr={'custom': op['src'], 'object': []},
                                                             dst_addr={'custom': op['dst'], 'object': []},
                                                             nat_addr='', nat_port='', src_addr_inv=0,
                                                             dst_addr_inv=0, comment=op['comment']))
            rule_id = self._find_id('nat_rule', lambda row: row.get('action') == 'filter'
                                    and row.get('comment') == op['comment'])
            return {'kind': 'nat_delete', 'id': rule_id}
        if kind == 'nat_delete':
            row = self._row('nat_rule', op['id'])
            self._write('nat_rule', 'del', {'id': row['id']})
            return {'kind': 'nat_add', 'src': _address_field(row.get('src_addr'))['custom'],
                    'dst': _address_field(row.get('dst_addr'))['custom'], 'comment': row.get('comment') or ''}
        raise RouterError(f'不支持的操作 {kind}')


def _addresses(value: Any, address_sets: Dict[str, List[str]]) -> List[str]:
    """Flatten iKuai ``{"custom": [...], "object": [...]}`` into concrete CIDRs/ranges."""
    if not isinstance(value, dict):
        return []
    result = [str(item) for item in value.get('custom') or [] if isinstance(item, str)]
    objects = value.get('object')
    for ref in objects if isinstance(objects, list) else []:
        if isinstance(ref, dict):
            result.extend(address_sets.get(str(ref.get('gp_name')), []))
    return result
