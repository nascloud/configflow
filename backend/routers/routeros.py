"""RouterOS v7 driver using the REST API (``/rest``, RouterOS 7.1+).

Reads go through REST; release operations are rendered as an ``.rsc`` script
for the user to review and import instead of being applied directly.
"""
import ipaddress
import re
from typing import Any, Dict, List

import requests

from backend.routers.base import (
    NatBypassEntry, RouteEntry, RouterDriver, RouterError, RouterInfo, RouterState,
)

TIMEOUT = 10
PERMISSION_HELP = (
    '请用管理员登录 WinBox，在 System → Users 找到 ConfigFlow 使用的账号并确认 Group；'
    '在 Groups 中为该账号的专用用户组启用 read、write、api、rest-api，重点检查 write。'
    '保存后重新生成发布预览；连接测试成功不代表已有写入权限。'
)


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
            raise RouterError('RouterOS 认证失败，请核对 ConfigFlow 中的用户名、密码和账号允许的来源地址；'
                              '在 WinBox → System → Users / Groups 检查用户组的 api、rest-api 权限')
        if response.status_code == 403:
            raise RouterError('RouterOS 拒绝访问，请先确认所用 HTTP/HTTPS 服务允许 REST API。' + PERMISSION_HELP)
        if response.status_code == 404:
            raise RouterError('RouterOS 未提供 REST API（HTTPS 需要 7.1+ 并启用 www-ssl；HTTP 需要 7.9+ 并启用 www）')
        if response.status_code != 200:
            try:
                body = response.json()
            except ValueError:
                body = {}
            detail = ': '.join(str(body[key]) for key in ('message', 'detail') if body.get(key)) if isinstance(body, dict) else ''
            message = (f'RouterOS GET {path} 返回 HTTP {response.status_code}'
                       + (f'（{detail}）' if detail else ''))
            if re.search(r'\bnot enough permissions\b', detail, re.IGNORECASE):
                message = f'RouterOS 账号权限不足。{PERMISSION_HELP} 原始错误：{message}'
            raise RouterError(message)
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


    def render_script(self, ops: List[Dict[str, Any]]) -> str:
        lines = ['# ConfigFlow 生成：请在 RouterOS 终端中检查后执行（/import 或粘贴）']
        for op in ops:
            kind = op['kind']
            lines.append(f'# {op["summary"]}')
            if kind == 'route_add':
                lines.append(f'/ip route add dst-address={op["cidr"]} gateway={op["gateway"]} '
                             f'comment={_quote(op["comment"])}')
            elif kind == 'route_comment':
                lines.append(f'/ip route set [find where .id={op["id"]}] comment={_quote(op["comment"])}')
            elif kind == 'route_delete':
                lines.append(f'/ip route remove [find where .id={op["id"]}]')
            elif kind == 'nat_update':
                lines.append(f'/ip firewall nat set [find where .id={op["id"]}] comment={_quote(op["comment"])}')
                for cidr in op.get('add_dst') or []:
                    lines.append(f'/ip firewall nat add chain=srcnat action=accept dst-address={cidr} '
                                 f'comment={_quote(op["comment"])} place-before=0')
            elif kind == 'nat_add':
                for source in op['src']:
                    lines.append(f'/ip firewall address-list add list=cf-nat-src address={source} comment=cf')
                for cidr in op['dst']:
                    lines.append(f'/ip firewall nat add chain=srcnat action=accept src-address-list=cf-nat-src '
                                 f'dst-address={cidr} comment={_quote(op["comment"])} place-before=0')
        return '\n'.join(lines) + '\n'


def _quote(text: str) -> str:
    return '"' + str(text).replace('\\', '\\\\').replace('"', '\\"').replace('$', '\\$') + '"'


def _truthy(value: Any) -> bool:
    return value is True or str(value).lower() in ('true', 'yes')
