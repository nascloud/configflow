"""iKuai 4.x driver (read-only).

iKuai has no public API; this uses the same endpoints as its web UI:
``POST /Action/login`` then ``POST /Action/call`` with ``func_name``/``action``.
Only 4.x is supported: responses are ``{"code": 0, "results": {...}}``.
"""
import base64
import hashlib
import ipaddress
from typing import Any, Dict, List
from urllib.parse import unquote

import requests

from backend.routers.base import (
    NatBypassEntry, RouteEntry, RouterDriver, RouterError, RouterInfo, RouterState,
)

TIMEOUT = 10
LIST_LIMIT = '0,1000'


class IKuaiDriver(RouterDriver):
    vendor = 'ikuai'

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
