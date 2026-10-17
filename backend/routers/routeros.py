"""RouterOS v7 driver (read-only) using the REST API (``/rest``, RouterOS 7.1+)."""
import ipaddress
import re
from typing import Any, Dict, List

import requests

from backend.routers.base import (
    NatBypassEntry, RouteEntry, RouterDriver, RouterError, RouterInfo, RouterState,
)

TIMEOUT = 10


class RouterOSDriver(RouterDriver):
    vendor = 'routeros'

    def __init__(self, endpoint: str, username: str, password: str, verify_tls: bool = True):
        self.endpoint = endpoint.rstrip('/')
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.verify = verify_tls
        self.session.auth = (username, password)

    def close(self):
        self.session.close()

    def _get(self, path: str) -> Any:
        try:
            response = self.session.get(f'{self.endpoint}/rest{path}', timeout=TIMEOUT, allow_redirects=False)
        except requests.RequestException as error:
            raise RouterError(f'无法连接 RouterOS：{error.__class__.__name__}') from None
        if response.status_code == 401:
            raise RouterError('RouterOS 认证失败，请检查用户名、密码以及用户组的 rest-api 权限')
        if response.status_code == 404:
            raise RouterError('RouterOS 未提供 REST API（需要 7.1+ 并启用 www-ssl 服务）')
        if response.status_code != 200:
            raise RouterError(f'RouterOS 返回 HTTP {response.status_code}')
        try:
            return response.json()
        except ValueError:
            raise RouterError('RouterOS 返回的不是 JSON') from None

    def _list(self, path: str) -> List[Dict[str, Any]]:
        rows = self._get(path)
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def probe(self) -> RouterInfo:
        resource = self._get('/system/resource')
        if not isinstance(resource, dict):
            raise RouterError('RouterOS 系统信息格式无法识别')
        version = str(resource.get('version') or '')
        match = re.match(r'(\d+)\.(\d+)', version)
        if not match or (int(match.group(1)), int(match.group(2))) < (7, 1):
            raise RouterError(f'需要 RouterOS 7.1+，当前版本 {version or "未知"}')
        capabilities = []
        for path, capability in (('/ip/route', 'routes'), ('/ip/firewall/nat', 'nat_bypass'),
                                 ('/ip/firewall/address-list', 'address_sets')):
            try:
                self._list(path)
                capabilities.append(capability)
            except RouterError:
                pass
        return RouterInfo(vendor='routeros', version=version,
                          model=str(resource.get('board-name') or ''), capabilities=capabilities)

    def read_state(self) -> RouterState:
        state = RouterState()
        for row in self._list('/ip/firewall/address-list'):
            if _truthy(row.get('disabled')):
                continue
            state.address_sets.setdefault(str(row.get('list') or ''), []).append(str(row.get('address') or ''))
        for row in self._list('/ip/route'):
            dst = str(row.get('dst-address') or '')
            try:
                dst = str(ipaddress.ip_network(dst, strict=False))
            except ValueError:
                continue
            state.routes.append(RouteEntry(
                id=str(row.get('.id', '')), dst=dst, gateway=str(row.get('gateway') or ''),
                enabled=not _truthy(row.get('disabled')), comment=str(row.get('comment') or '')))
        for row in self._list('/ip/firewall/nat'):
            if row.get('chain') != 'srcnat' or row.get('action') != 'accept':
                continue
            dst = [str(row['dst-address'])] if row.get('dst-address') else []
            if row.get('dst-address-list'):
                dst.extend(state.address_sets.get(str(row['dst-address-list']), []))
            src = [str(row['src-address'])] if row.get('src-address') else []
            if row.get('src-address-list'):
                src.extend(state.address_sets.get(str(row['src-address-list']), []))
            state.nat_bypass.append(NatBypassEntry(
                id=str(row.get('.id', '')), dst=dst, src=src,
                enabled=not _truthy(row.get('disabled')), comment=str(row.get('comment') or '')))
        return state


def _truthy(value: Any) -> bool:
    return value is True or str(value).lower() in ('true', 'yes')
