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

from backend.routers.base import (
    POLICY_AUTO_COMMENT, POLICY_MANUAL_COMMENT, RouterState, as_dict, is_managed, policy_route, policy_route_ok,
)
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


def _policy_route_items(desired: Dict[str, Any], state: RouterState) -> Dict[str, Any]:
    """Proxy CIDRs routed by a policy rule that excludes the gateway as a source.

    Every enabled static route to the gateway is loop-prone: it is migrated into
    a policy rule and then deleted, including hand-made ones (kept in their own
    add-only rule, ``cf-mihomo-manual``).
    """
    gateway = desired.get('proxy_gateway') or ''
    desired_cidrs = {entry['cidr'] for entry in desired['proxy_cidrs']}
    auto = policy_route(state, POLICY_AUTO_COMMENT)
    covered = _networks(auto.dst) if auto and policy_route_ok(auto, gateway) else []
    items = []
    for entry in desired['proxy_cidrs']:
        target = ipaddress.IPv4Network(entry['cidr'])
        matches = [route for route in state.routes if route.dst == entry['cidr']]
        bad = [route for route in matches if not (route.enabled and gateway and route.gateway == gateway)]
        if bad:
            route = bad[0]
            reason = '已停用' if not route.enabled else f'下一跳为 {route.gateway}'
            status, detail = 'conflict', f'同网段路由{reason}，期望下一跳 {gateway or "未知"}'
        elif matches:
            status, detail = 'adoptable', '静态路由会让 mihomo 自身的流量回环，将迁移为排除 mihomo 的策略路由'
        elif any(target.subnet_of(network) for network in covered):
            status, detail = 'synced', ''
        else:
            status, detail = 'missing', ''
        items.append({'cidr': entry['cidr'], 'sources': entry['sources'], 'status': status, 'detail': detail,
                      'route_ids': [route.id for route in matches]})
    extra = [{'cidr': route.dst, 'gateway': route.gateway, 'route_id': route.id, 'comment': route.comment}
             for route in state.routes if is_managed(route.comment) and route.dst not in desired_cidrs]
    if auto:
        extra += [{'cidr': cidr, 'gateway': auto.gateway, 'route_id': f'policy:{auto.id}', 'comment': auto.comment}
                  for cidr in auto.dst if cidr not in desired_cidrs]
    unmanaged_other = [{'cidr': route.dst, 'gateway': route.gateway, 'comment': route.comment, 'enabled': route.enabled,
                        'migrate': route.enabled}
                       for route in state.routes
                       if gateway and route.gateway == gateway and not is_managed(route.comment)
                       and route.dst not in desired_cidrs]
    manual = policy_route(state, POLICY_MANUAL_COMMENT)
    return {'items': items, 'extra': extra, 'unmanaged_other': unmanaged_other, 'mode': 'policy',
            'policy': {'auto': as_dict(auto) if auto else None, 'manual': as_dict(manual) if manual else None}}


def _route_items(desired: Dict[str, Any], state: RouterState) -> Dict[str, Any]:
    if state.policy_routes is not None:
        return _policy_route_items(desired, state)
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
    if 'ros_policy' in desired:
        from backend.routers.ros_policy import build, overlap_check
        state.ros_policy_plan = build(desired, state)
        policy_overlap = overlap_check(desired['ros_policy']['cidrs'])
        # Policy migration owns these routes; the ordinary static cleanup must
        # never delete them a second time or delete a conflicting manual route.
        routes['extra'] = [item for item in routes['extra'] if not policy_overlap(item['cidr'])]
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
    in_sync = in_sync and not any(item.get('migrate') for item in routes['unmanaged_other'])
    in_sync = in_sync and dns['status'] in {'disabled', 'synced'}
    result = {'desired': {key: value for key, value in desired.items() if key != 'ros_policy'},
              'routes': routes, 'nat_bypass': nat, 'dhcp_dns': dns, 'summary': summary, 'in_sync': in_sync}
    if state.ros_policy_plan is not None:
        policy = state.ros_policy_plan
        result['policy_routing'] = {key: policy[key] for key in ('items', 'blockers', 'warnings')}
        result['policy_routing']['sources'] = desired['ros_policy']['sources']
        result['policy_routing']['enabled'] = bool(desired['ros_policy']['selected'])
        for item in policy['items']:
            summary[item['status']] = summary.get(item['status'], 0) + 1
        if policy['blockers']:
            summary['unknown'] = summary.get('unknown', 0) + len(policy['blockers'])
        if policy['ops'] and not policy['items']:
            summary['pending'] = summary.get('pending', 0) + 1
        result['in_sync'] = in_sync and not policy['ops'] and not policy['blockers']
    return result
