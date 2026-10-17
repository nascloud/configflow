"""Release plan: turn a drift report into ordered, reviewable operations.

Stages run in this order (see docs/ROUTER_INTEGRATION_DESIGN.md §6.2):

1. ``return_routing`` - the mihomo Agent marks replies so they go back through
   the router. Harmless on its own, so it goes first.
2. ``routes``         - router static routes for proxy CIDRs to the gateway.
3. ``nat_bypass``     - exempt proxy CIDRs from source NAT so mihomo sees client IPs.

Manual objects that already do the right thing are adopted (re-commented with the
managed prefix), never recreated. Conflicting routes are skipped and reported.
"""
import hashlib
import ipaddress
import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from backend.routers.base import RouterState, is_managed, managed_comment
from backend.routers.report import _networks

RETURN_MARK = '0x400000'   # avoids mihomo auto-redirect marks 0x2023/0x2024
RETURN_TABLE = 104
RETURN_PRIORITY = 104
MANAGED_RETURN_TABLE = 'configflow_return'

STAGE_TITLES = {
    'return_routing': 'mihomo 回流规则',
    'routes': '主路由代理路由',
    'nat_bypass': '主路由 NAT 免转换',
}


def router_ip(endpoint: str) -> Optional[str]:
    host = urlparse(endpoint or '').hostname or ''
    try:
        return str(ipaddress.IPv4Address(host))
    except ValueError:
        return None


def _collapse(values: List[str]) -> List[str]:
    return [str(net) for net in ipaddress.collapse_addresses(_networks(values))]


def _mark_value(text: str) -> Optional[int]:
    try:
        return int(str(text).split('/')[0], 0)
    except ValueError:
        return None


def _lan_except(gateway: str) -> List[str]:
    """Source ranges for a new NAT bypass rule: the gateway's /24 without the gateway itself.

    The gateway's own traffic (mihomo DIRECT connections) must keep being NATed.
    """
    address = ipaddress.IPv4Address(gateway)
    network = ipaddress.IPv4Network(f'{gateway}/24', strict=False)
    first, last = network.network_address + 1, network.broadcast_address - 1
    ranges = []
    if address > first:
        ranges.append(f'{first}-{address - 1}')
    if address < last:
        ranges.append(f'{address + 1}-{last}')
    return ranges


def _route_ops(report: Dict[str, Any], state: RouterState, gateway: str, warnings: List[str]):
    ops, skipped = [], set()
    for item in report['routes']['items']:
        cidr = item['cidr']
        if item['status'] == 'missing':
            comment = managed_comment(','.join(item['sources']))
            ops.append({'kind': 'route_add', 'cidr': cidr, 'gateway': gateway, 'comment': comment,
                        'summary': f'新增路由 {cidr} → {gateway}（备注 {comment}）'})
        elif item['status'] == 'adoptable':
            route = next(route for route in state.routes
                         if route.dst == cidr and route.enabled and route.gateway == gateway)
            comment = managed_comment(route.comment)
            ops.append({'kind': 'route_comment', 'id': route.id, 'cidr': cidr, 'comment': comment,
                        'summary': f'接管路由 {cidr}（备注「{route.comment or "无"}」→「{comment}」）'})
        elif item['status'] == 'conflict':
            skipped.add(cidr)
            warnings.append(f'{cidr}：{item["detail"]}。本次跳过该网段（路由、NAT、回流都不处理），请先手动处理')
    for extra in report['routes']['extra']:
        ops.append({'kind': 'route_delete', 'id': extra['route_id'], 'cidr': extra['cidr'],
                    'summary': f'删除不再需要的受管路由 {extra["cidr"]} → {extra["gateway"]}'})
    return ops, skipped


def _nat_ops(report: Dict[str, Any], state: RouterState, gateway: str, skipped: set):
    items = [item for item in report['nat_bypass']['items'] if item['cidr'] not in skipped]
    rules = {rule.id: rule for rule in state.nat_bypass if rule.enabled}
    missing = [item['cidr'] for item in items if item['status'] == 'missing']
    adopt_ids: List[str] = []
    for item in items:
        if item['status'] == 'adoptable':
            adopt_ids.extend(rule_id for rule_id in item['rule_ids']
                             if rule_id not in adopt_ids and not is_managed(rules[rule_id].comment))
    managed = [rule for rule in rules.values() if is_managed(rule.comment)]
    target = managed[0] if managed else (rules[adopt_ids[0]] if adopt_ids else None)

    ops = []
    for rule_id in adopt_ids:
        rule = rules[rule_id]
        add = missing if target is rule else []
        comment = managed_comment(rule.comment)
        extra = f'，并加入 {", ".join(add)}' if add else ''
        ops.append({'kind': 'nat_update', 'id': rule.id, 'add_dst': add, 'comment': comment,
                    'summary': f'接管 NAT 免转换规则「{rule.comment or rule.id}」（备注 → {comment}）{extra}'})
    if target is not None and is_managed(target.comment) and missing:
        ops.append({'kind': 'nat_update', 'id': target.id, 'add_dst': missing, 'comment': target.comment,
                    'summary': f'受管 NAT 免转换规则「{target.comment}」加入 {", ".join(missing)}'})
    if target is None and missing:
        src = _lan_except(gateway)
        ops.append({'kind': 'nat_add', 'src': src, 'dst': missing, 'comment': 'cf-mihomo',
                    'summary': f'新增 NAT 免转换规则：源 {", ".join(src)}，目的 {", ".join(missing)}'})

    # Everything exempt from SNAT after this release must also get return marks.
    exempt = [cidr for rule in rules.values() for cidr in rule.dst]
    exempt += [cidr for op in ops for cidr in op.get('add_dst') or op.get('dst') or []]
    return ops, exempt


def _return_op(return_routing: Dict[str, Any], spec: Dict[str, Any], warnings: List[str]):
    mark = _mark_value(RETURN_MARK)
    legacy, others = [], []
    for table in return_routing.get('return_tables') or []:
        if table.get('name') == MANAGED_RETURN_TABLE:
            continue
        name = f'{table.get("family")} {table.get("name")}'
        if any(_mark_value(value) == mark for value in table.get('marks') or []):
            legacy.append(name)
        else:
            others.append(name)
    if others:
        warnings.append(f'mihomo 主机上还有其他连接标记表（{", ".join(others)}），不会被改动，请确认不冲突')
    for rule in return_routing.get('fwmark_rules') or []:
        if str(rule.get('priority')) == str(RETURN_PRIORITY) and _mark_value(rule.get('mark')) != mark:
            warnings.append(f'策略路由优先级 {RETURN_PRIORITY} 已被 fwmark {rule.get("mark")} 占用，发布时会被替换')

    managed = return_routing.get('managed') or {}
    current = managed.get('spec') or None
    same = (current and managed.get('table_present')
            and set(current.get('proxy_dst') or []) == set(spec['proxy_dst'])
            and all(current.get(key) == spec[key] for key in ('router_ip', 'mark', 'table', 'priority')))
    if same and not legacy:
        return []
    added = sorted(set(spec['proxy_dst']) - set((current or {}).get('proxy_dst') or []))
    parts = [f'应用回流规则（表 inet {MANAGED_RETURN_TABLE}，mark {RETURN_MARK}，路由表 {RETURN_TABLE} → {spec["router_ip"]}）',
             f'标记 {len(spec["proxy_dst"])} 个网段']
    if current and added:
        parts.append(f'新增 {", ".join(added)}')
    if legacy:
        parts.append(f'接管并移除手工规则 {", ".join(legacy)}（同时停用其开机脚本，回滚时恢复）')
    return [{'kind': 'return_routing_apply', 'spec': {**spec, 'adopt_legacy': legacy}, 'previous': current,
             'summary': '；'.join(parts)}]


def fingerprint(stages: List[Dict[str, Any]]) -> str:
    payload = json.dumps(stages, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def build_plan(router: Dict[str, Any], desired: Dict[str, Any], state: RouterState,
               report: Dict[str, Any], return_routing: Dict[str, Any], driver=None) -> Dict[str, Any]:
    warnings = list(desired.get('warnings') or [])
    blockers = []
    gateway = desired.get('proxy_gateway') or ''
    if not gateway:
        blockers.append('未关联 mihomo Agent，无法确定代理路由的下一跳')
    upstream = router_ip(router.get('endpoint'))
    if not upstream:
        blockers.append('路由器地址不是 IPv4，无法确定回流规则的下一跳')
    if return_routing.get('status') != 'ok':
        blockers.append(f'无法读取 mihomo Agent 的回流规则状态：{return_routing.get("message") or "未知错误"}')
    if blockers:
        return {'stages': [], 'blockers': blockers, 'warnings': warnings, 'script': '', 'fingerprint': '',
                'empty': True}

    route_ops, skipped = _route_ops(report, state, gateway, warnings)
    nat_ops, exempt = _nat_ops(report, state, gateway, skipped)
    proxy_cidrs = [entry['cidr'] for entry in desired['proxy_cidrs'] if entry['cidr'] not in skipped]
    spec = {'router_ip': upstream, 'proxy_dst': _collapse(proxy_cidrs + exempt), 'mark': RETURN_MARK,
            'table': RETURN_TABLE, 'priority': RETURN_PRIORITY}
    return_ops = _return_op(return_routing, spec, warnings)

    manual = driver is not None and not driver.supports_write
    stages = [
        {'key': 'return_routing', 'title': STAGE_TITLES['return_routing'], 'manual': False, 'ops': return_ops},
        {'key': 'routes', 'title': STAGE_TITLES['routes'], 'manual': manual, 'ops': route_ops},
        {'key': 'nat_bypass', 'title': STAGE_TITLES['nat_bypass'], 'manual': manual, 'ops': nat_ops},
    ]
    checks = {
        'routes': [{'cidr': cidr, 'gateway': gateway} for cidr in proxy_cidrs],
        'removed_routes': [op['id'] for op in route_ops if op['kind'] == 'route_delete'],
        'nat_bypass': proxy_cidrs,
        'return_routing': {'proxy_dst': spec['proxy_dst'],
                           'adopted': return_ops[0]['spec']['adopt_legacy'] if return_ops else []},
    }
    script = driver.render_script(route_ops + nat_ops) if manual else ''
    return {'stages': stages, 'checks': checks, 'blockers': [], 'warnings': warnings, 'script': script,
            'fingerprint': fingerprint(stages), 'empty': not any(stage['ops'] for stage in stages)}
