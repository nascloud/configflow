"""Drift report: compare the desired side-gateway state with what the router has.

Statuses per desired item:
- ``synced``     managed by ConfigFlow and correct
- ``adoptable``  correct but created manually; P1 adopts it (tags it) instead of recreating
- ``conflict``   same destination exists but differs (wrong gateway / disabled)
- ``missing``    not present
- ``not_required`` ROS forwarding cannot match any potential source-NAT rule
- ``unknown``    ROS source-NAT requirement cannot be safely determined
Managed objects that are no longer desired are reported as ``extra``.
"""
import ipaddress
from typing import Any, Dict, Iterable, List

from backend.routers.base import RouterState, is_managed
from backend.routers.dhcp_dns import build_dns_report
from backend.routers.routeros_nat import assess_nat


def _networks(values: Iterable[str]) -> List[ipaddress.IPv4Network]:
    """Parse CIDRs, single IPs and ``a-b`` ranges (iKuai style) into IPv4 networks."""
    result = []
    for value in values:
        text = str(value).strip()
        try:
            if '-' in text:
                start, end = (ipaddress.IPv4Address(part.strip()) for part in text.split('-', 1))
                result.extend(ipaddress.summarize_address_range(start, end))
            else:
                network = ipaddress.ip_network(text, strict=False)
                if isinstance(network, ipaddress.IPv4Network):
                    result.append(network)
        except ValueError:
            continue
    return result


def _route_items(desired: Dict[str, Any], state: RouterState) -> Dict[str, Any]:
    gateway = desired.get('proxy_gateway') or ''
    desired_cidrs = {entry['cidr'] for entry in desired['proxy_cidrs']}
    items = []
    for entry in desired['proxy_cidrs']:
        matches = [route for route in state.routes if route.dst == entry['cidr']]
        good = [route for route in matches if route.enabled and gateway and route.gateway == gateway]
        if any(is_managed(route.comment) for route in good):
            status, detail = 'synced', ''
        elif good:
            status, detail = 'adoptable', f'已有手工路由（{good[0].comment or "无备注"}），将直接接管'
        elif matches:
            route = matches[0]
            reason = '已停用' if not route.enabled else f'下一跳为 {route.gateway}'
            status, detail = 'conflict', f'同网段路由{reason}，期望下一跳 {gateway or "未知"}'
        else:
            status, detail = 'missing', ''
        items.append({'cidr': entry['cidr'], 'sources': entry['sources'], 'status': status, 'detail': detail,
                      'route_ids': [route.id for route in matches]})
    extra = [{'cidr': route.dst, 'gateway': route.gateway, 'route_id': route.id, 'comment': route.comment}
             for route in state.routes if is_managed(route.comment) and route.dst not in desired_cidrs]
    # Manual routes to the proxy gateway that ConfigFlow does not derive (e.g. public DNS
    # addresses routed for TUN DNS hijack) are informational, never touched.
    unmanaged_other = [{'cidr': route.dst, 'gateway': route.gateway, 'comment': route.comment, 'enabled': route.enabled}
                       for route in state.routes
                       if gateway and route.gateway == gateway and not is_managed(route.comment)
                       and route.dst not in desired_cidrs]
    return {'items': items, 'extra': extra, 'unmanaged_other': unmanaged_other}


def _nat_items(desired: Dict[str, Any], state: RouterState) -> Dict[str, Any]:
    rules = [rule for rule in state.nat_bypass if rule.enabled]
    items = []
    for entry in desired['proxy_cidrs']:
        target = ipaddress.IPv4Network(entry['cidr'])
        covering = [rule for rule in rules if any(target.subnet_of(net) for net in _networks(rule.dst))]
        if any(is_managed(rule.comment) for rule in covering):
            status, detail = 'synced', ''
        elif covering:
            status, detail = 'adoptable', f'已被手工规则「{covering[0].comment or covering[0].id}」覆盖，将直接接管'
        elif state.nat_context is not None:
            assessment = assess_nat(entry['cidr'], desired.get('proxy_gateway') or '', state)
            status, detail = assessment['status'], assessment['detail']
        else:
            status, detail = 'missing', '未发现覆盖该网段的 NAT 免转换规则，需结合源 NAT 配置确认是否影响客户端 IP'
        items.append({'cidr': entry['cidr'], 'status': status, 'detail': detail,
                      'rule_ids': [rule.id for rule in covering]})
    disabled = [{'rule_id': rule.id, 'comment': rule.comment, 'dst': rule.dst}
                for rule in state.nat_bypass if not rule.enabled]
    return {'items': items, 'disabled_rules': disabled}


def build_report(desired: Dict[str, Any], state: RouterState) -> Dict[str, Any]:
    routes = _route_items(desired, state)
    nat = _nat_items(desired, state)
    dns = build_dns_report(desired, state.dhcp_dns)
    summary: Dict[str, int] = {}
    for section in (routes['items'], nat['items']):
        for item in section:
            summary[item['status']] = summary.get(item['status'], 0) + 1
    summary['extra'] = len(routes['extra'])
    if dns['status'] != 'disabled':
        summary[dns['status']] = summary.get(dns['status'], 0) + 1
    in_sync = all(item['status'] in {'synced', 'not_required'}
                  for item in routes['items'] + nat['items']) and not routes['extra']
    in_sync = in_sync and dns['status'] in {'disabled', 'synced'}
    return {'desired': desired, 'routes': routes, 'nat_bypass': nat, 'dhcp_dns': dns,
            'summary': summary, 'in_sync': in_sync}
