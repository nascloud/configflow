"""Upstream routers: CRUD, capability probe, drift report and staged releases."""
from datetime import datetime

import requests
from flask import jsonify, request

from backend.common.auth import require_auth
from backend.common.config import get_config, save_config
from backend.routers.base import RouterConflictError, RouterError, as_dict
from backend.routers.desired import compute_desired
from backend.routers.dhcp_dns import dns_agent_settings
from backend.routers.ikuai import IKuaiDriver
from backend.routers.plan import build_plan
from backend.routers.release import AgentReturnRouting, execute
from backend.routers.report import build_report
from backend.routers.route_editor import (
    RouteEditValidationError, build_route_plan, execute_route_plan,
    normalize_request, public_route, read_routes, router_operation_lock,
)
from backend.routers.routeros import RouterOSDriver
from backend.routers.store import (
    RouterValidationError, find_router, normalize, public_view, routers_of,
)
from backend.routes import routers_bp

DRIVERS = {'ikuai': IKuaiDriver, 'routeros': RouterOSDriver}


def _driver(router):
    return DRIVERS[router['type']](router['endpoint'], router['username'], router.get('password') or '',
                                   verify_tls=router.get('verify_tls') is not False)


def _not_found():
    return jsonify({'success': False, 'message': '路由器不存在'}), 404


def _save_items(config, items):
    config['routers'] = {**(config.get('routers') if isinstance(config.get('routers'), dict) else {}), 'items': items}
    save_config(config)


def _agent(agent_id):
    if not agent_id:
        return None
    from backend.common.agent_manager import get_agent_manager
    return get_agent_manager().get_agent_by_id(agent_id)


@routers_bp.route('', methods=['GET', 'POST'])
@require_auth
def list_or_create_routers():
    config = get_config()
    if request.method == 'GET':
        return jsonify({'success': True, 'routers': [public_view(item) for item in routers_of(config)]})
    try:
        router = normalize(request.get_json(silent=True))
    except RouterValidationError as error:
        return jsonify({'success': False, 'message': str(error)}), 400
    _save_items(config, routers_of(config) + [router])
    return jsonify({'success': True, 'router': public_view(router)})


@routers_bp.route('/<router_id>', methods=['PUT', 'DELETE'])
@require_auth
def update_or_delete_router(router_id):
    config = get_config()
    existing = find_router(config, router_id)
    if existing is None:
        return _not_found()
    others = [item for item in routers_of(config) if item.get('id') != router_id]
    if request.method == 'DELETE':
        _save_items(config, others)
        return jsonify({'success': True})
    try:
        router = normalize(request.get_json(silent=True), existing)
    except RouterValidationError as error:
        return jsonify({'success': False, 'message': str(error)}), 400
    if any(router.get(key) != existing.get(key) for key in ('type', 'endpoint', 'username', 'password')):
        router.pop('last_probe', None)   # stale once the connection changes
    index = next(i for i, item in enumerate(routers_of(config)) if item.get('id') == router_id)
    items = routers_of(config)
    items[index] = router
    _save_items(config, items)
    return jsonify({'success': True, 'router': public_view(router)})


@routers_bp.route('/<router_id>/probe', methods=['POST'])
@require_auth
def probe_router(router_id):
    config = get_config()
    router = find_router(config, router_id)
    if router is None:
        return _not_found()
    driver = _driver(router)
    probe = {'at': datetime.now().isoformat(timespec='seconds')}
    try:
        info = driver.probe()
        probe.update(ok=True, **as_dict(info))
    except RouterError as error:
        probe.update(ok=False, message=str(error))
    finally:
        driver.close()
    items = routers_of(config)
    for item in items:
        if item.get('id') == router_id:
            item['last_probe'] = probe
    _save_items(config, items)
    return jsonify({'success': probe['ok'], 'probe': probe, 'message': probe.get('message', '')}), 200 if probe['ok'] else 502


def _inspect(config, router, driver):
    """Desired state, router state, drift report and Agent return-routing state."""
    gateway_agent = _agent(router.get('proxy_gateway_agent_id'))
    desired = dict(compute_desired(config, gateway_agent.get('host') if gateway_agent else None,
                                  router.get('ip_rule_sets') or []))
    dns_id = router.get('dns_agent_id') or ''
    desired.update(dns_agent_settings(dns_id, _agent(dns_id) if dns_id else None))
    state = driver.read_state()
    if dns_id and not desired['dns_error']:
        try:
            state.dhcp_dns = driver.read_dhcp_dns()
        except RouterError as error:
            desired['dns_error'] = f'无法读取 DHCP DNS 配置：{error}'
    report = build_report(desired, state)
    if router.get('manage_return_routing') is True:
        report['return_routing'] = _return_routing(gateway_agent)
    else:
        report['return_routing'] = {
            'status': 'disabled',
            'message': '未启用自动配置回流；本次不读取或修改主机回流配置，已有规则保留。',
        }
    return gateway_agent, desired, state, report


def _now():
    return datetime.now().isoformat(timespec='seconds')


@routers_bp.route('/<router_id>/report', methods=['GET'])
@require_auth
def router_report(router_id):
    config = get_config()
    router = find_router(config, router_id)
    if router is None:
        return _not_found()
    driver = _driver(router)
    try:
        _, _, _, report = _inspect(config, router, driver)
    except RouterError as error:
        return jsonify({'success': False, 'message': str(error)}), 502
    finally:
        driver.close()
    return jsonify({'success': True, 'report': report, 'generated_at': _now()})


@routers_bp.route('/<router_id>/plan', methods=['POST'])
@require_auth
def router_plan(router_id):
    """Preview a release: nothing is written."""
    config = get_config()
    router = find_router(config, router_id)
    if router is None:
        return _not_found()
    driver = _driver(router)
    try:
        _, desired, state, report = _inspect(config, router, driver)
        plan = build_plan(router, desired, state, report, report['return_routing'], driver)
    except RouterError as error:
        return jsonify({'success': False, 'message': str(error)}), 502
    finally:
        driver.close()
    return jsonify({'success': True, 'plan': plan, 'generated_at': _now()})


@routers_bp.route('/<router_id>/apply', methods=['POST'])
@require_auth
def router_apply(router_id):
    """Run the previewed release. The plan is rebuilt and must match the preview's fingerprint."""
    config = get_config()
    router = find_router(config, router_id)
    if router is None:
        return _not_found()
    expected = str((request.get_json(silent=True) or {}).get('fingerprint') or '')
    driver = _driver(router)
    try:
        with router_operation_lock(router):
            gateway_agent, desired, state, report = _inspect(config, router, driver)
            plan = build_plan(router, desired, state, report, report['return_routing'], driver)
            if plan['blockers']:
                return jsonify({'success': False, 'message': '；'.join(plan['blockers']), 'plan': plan}), 409
            if not expected or plan['fingerprint'] != expected:
                return jsonify({'success': False, 'message': '路由器、Agent 或期望状态在预览后发生了变化，请重新预览',
                                'plan': plan}), 409
            if plan['empty']:
                return jsonify({'success': True, 'result': {'ok': True, 'log': [], 'rollback': []}, 'plan': plan})
            agent_client = None
            if router.get('manage_return_routing') is True:
                from backend.common.agent_manager import get_agent_manager
                agent_client = AgentReturnRouting(get_agent_manager(), gateway_agent)
            result = execute(plan, driver, agent_client)
    except RouterError as error:
        return jsonify({'success': False, 'message': str(error)}), 502
    finally:
        driver.close()

    config = get_config()   # reload: the release may have taken a while
    items = routers_of(config)
    for item in items:
        if item.get('id') == router_id:
            item['last_release'] = {'at': _now(), 'ok': result['ok'], 'fingerprint': plan['fingerprint'],
                                    'failed_stage': result.get('failed_stage', '')}
    _save_items(config, items)
    message = ''
    if not result['ok']:
        rollback_failed = any(item['status'] == 'failed' for item in result['rollback'])
        detail = '回滚未完成，请检查发布记录' if rollback_failed else '已按逆序撤销已完成的变更'
        message = f'阶段「{result["failed_stage"]}」失败，{detail}'
    return jsonify({'success': result['ok'], 'result': result, 'plan': plan, 'message': message}), \
        200 if result['ok'] else 502


@routers_bp.route('/<router_id>/routes', methods=['GET'])
@require_auth
def list_device_routes(router_id):
    router = find_router(get_config(), router_id)
    if router is None:
        return _not_found()
    driver = _driver(router)
    try:
        rows = read_routes(driver)
        return jsonify({'success': True, 'routes': [public_route(row) for row in rows]})
    except RouterError as error:
        return jsonify({'success': False, 'message': str(error)}), 502
    finally:
        driver.close()


@routers_bp.route('/<router_id>/routes/plan', methods=['POST'])
@require_auth
def preview_device_route(router_id):
    return _device_route_change(router_id, apply=False)


@routers_bp.route('/<router_id>/routes/apply', methods=['POST'])
@require_auth
def apply_device_route(router_id):
    return _device_route_change(router_id, apply=True)


def _device_route_change(router_id, *, apply):
    router = find_router(get_config(), router_id)
    if router is None:
        return _not_found()
    payload = request.get_json(silent=True)
    try:
        normalize_request(payload)
    except RouteEditValidationError as error:
        return jsonify({'success': False, 'message': str(error)}), 400
    driver = _driver(router)
    try:
        with router_operation_lock(router):
            rows = read_routes(driver)
            plan = build_route_plan(router, rows, payload)
            if not apply:
                return jsonify({'success': True, 'plan': plan})
            fingerprint = payload.get('fingerprint') if isinstance(payload, dict) else None
            if not isinstance(fingerprint, str) or fingerprint != plan['fingerprint']:
                return jsonify({'success': False, 'message': '操作内容或路由状态已变化，请重新预览后确认'}), 409
            result = execute_route_plan(driver, rows, plan)
            return jsonify({'success': True, 'result': result,
                            'message': '路由变更已写入并通过回读校验' if result['changed'] else '路由没有变化'})
    except RouteEditValidationError as error:
        return jsonify({'success': False, 'message': str(error)}), 400
    except RouterConflictError as error:
        return jsonify({'success': False, 'message': str(error)}), 409
    except RouterError as error:
        return jsonify({'success': False, 'message': str(error)}), 502
    finally:
        driver.close()

def _return_routing(agent):
    """Return-routing state reported by the proxy gateway's Agent (read-only)."""
    if agent is None:
        return {'status': 'unlinked', 'message': '未关联 mihomo Agent'}
    if agent.get('status') != 'online':
        return {'status': 'offline', 'message': f'Agent {agent.get("name") or agent.get("id")} 离线'}
    from backend.common.agent_manager import get_agent_manager
    try:
        body = get_agent_manager().return_routing_status(agent)
    except (RuntimeError, requests.RequestException) as error:
        return {'status': 'unavailable', 'message': str(error) or error.__class__.__name__}
    return {'status': 'ok', **{key: value for key, value in body.items() if key != 'success'}}
