"""Execute a release plan stage by stage, verify each stage, roll back in reverse on failure."""
import ipaddress
from typing import Any, Dict, List, Tuple

import requests

from backend.routers.base import RouterError, RouterState, is_managed
from backend.routers.report import _networks


class ReleaseError(Exception):
    pass


class AgentReturnRouting:
    """Return-routing calls on the proxy gateway's Agent."""

    def __init__(self, manager, agent: Dict[str, Any]):
        self.manager = manager
        self.agent = agent

    def apply(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        return self.manager.return_routing_apply(self.agent, spec)

    def remove(self, restore_legacy: bool) -> Dict[str, Any]:
        return self.manager.return_routing_remove(self.agent, restore_legacy)

    def status(self) -> Dict[str, Any]:
        return self.manager.return_routing_status(self.agent)


def _apply(op: Dict[str, Any], driver, agent: AgentReturnRouting) -> Dict[str, Any]:
    kind = op['kind']
    if kind == 'return_routing_apply':
        agent.apply(op['spec'])
        if op.get('previous'):
            return {'kind': 'return_routing_apply', 'spec': op['previous'], 'previous': None}
        return {'kind': 'return_routing_remove', 'restore_legacy': True}
    if kind == 'return_routing_remove':
        agent.remove(op['restore_legacy'])
        return {}
    return driver.apply(op)


def _covered(cidr: str, state: RouterState) -> bool:
    target = ipaddress.IPv4Network(cidr)
    return any(rule.enabled and is_managed(rule.comment) and any(target.subnet_of(net) for net in _networks(rule.dst))
               for rule in state.nat_bypass)


def _verify(stage: str, checks: Dict[str, Any], driver, agent: AgentReturnRouting) -> None:
    if stage == 'return_routing':
        status = agent.status()
        managed = status.get('managed') or {}
        expected = checks['return_routing']
        if not managed.get('table_present'):
            raise ReleaseError('Agent 上没有找到 configflow_return 表')
        if set((managed.get('spec') or {}).get('proxy_dst') or []) != set(expected['proxy_dst']):
            raise ReleaseError('Agent 上的回流网段与计划不一致')
        present = {f'{table.get("family")} {table.get("name")}' for table in status.get('return_tables') or []}
        leftover = sorted(present & set(expected['adopted']))
        if leftover:
            raise ReleaseError(f'手工回流规则 {", ".join(leftover)} 仍然存在')
        if not any(str(rule.get('priority')) == str((managed.get('spec') or {}).get('priority'))
                   for rule in status.get('fwmark_rules') or []):
            raise ReleaseError('没有找到回流策略路由（ip rule）')
        return
    state = driver.read_state()
    if stage == 'routes':
        for check in checks['routes']:
            if not any(route.dst == check['cidr'] and route.enabled and route.gateway == check['gateway']
                       and is_managed(route.comment) for route in state.routes):
                raise ReleaseError(f'回读校验失败：{check["cidr"]} 没有受管路由指向 {check["gateway"]}')
        remaining = {route.id for route in state.routes} & set(checks['removed_routes'])
        if remaining:
            raise ReleaseError('回读校验失败：应删除的受管路由仍然存在')
    elif stage == 'nat_bypass':
        uncovered = [cidr for cidr in checks['nat_bypass'] if not _covered(cidr, state)]
        if uncovered:
            raise ReleaseError(f'回读校验失败：{", ".join(uncovered)} 没有被受管 NAT 免转换规则覆盖')


def _rollback(done: List[Tuple[str, Dict[str, Any], Dict[str, Any]]], driver, agent) -> List[Dict[str, Any]]:
    results = []
    for stage, op, undo in reversed(done):
        if not undo:
            continue
        try:
            _apply(undo, driver, agent)
            results.append({'stage': stage, 'summary': f'已撤销：{op["summary"]}', 'status': 'ok'})
        except (RouterError, RuntimeError, requests.RequestException) as error:
            results.append({'stage': stage, 'summary': f'撤销失败：{op["summary"]}（{error}）', 'status': 'failed'})
    return results


def execute(plan: Dict[str, Any], driver, agent: AgentReturnRouting) -> Dict[str, Any]:
    log: List[Dict[str, Any]] = []
    done: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    record = log.append

    current = ''
    try:
        for stage in plan['stages']:
            current = stage['key']
            if not stage['ops']:
                continue
            if stage['manual']:
                record({'stage': current, 'summary': '需要在路由器上手动导入脚本', 'status': 'manual'})
                continue
            for op in stage['ops']:
                undo = _apply(op, driver, agent)
                done.append((current, op, undo))
                record({'stage': current, 'summary': op['summary'], 'status': 'ok'})
            _verify(current, plan['checks'], driver, agent)
            record({'stage': current, 'summary': '回读校验通过', 'status': 'verified'})
    except (ReleaseError, RouterError, RuntimeError, requests.RequestException) as error:
        record({'stage': current, 'summary': str(error) or error.__class__.__name__, 'status': 'failed'})
        return {'ok': False, 'failed_stage': current, 'log': log, 'rollback': _rollback(done, driver, agent)}
    return {'ok': True, 'log': log, 'rollback': []}
