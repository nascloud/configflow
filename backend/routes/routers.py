"""Upstream routers (read-only P0): CRUD, capability probe and drift report."""
from datetime import datetime

import requests
from flask import jsonify, request

from backend.common.auth import require_auth
from backend.common.config import get_config, save_config
from backend.routers.base import RouterError, as_dict
from backend.routers.desired import compute_desired
from backend.routers.ikuai import IKuaiDriver
from backend.routers.report import build_report
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


@routers_bp.route('/<router_id>/report', methods=['GET'])
@require_auth
def router_report(router_id):
    config = get_config()
    router = find_router(config, router_id)
    if router is None:
        return _not_found()
    gateway_agent = _agent(router.get('proxy_gateway_agent_id'))
    desired = compute_desired(config, gateway_agent.get('host') if gateway_agent else None,
                              router.get('ip_rule_sets') or [])

    driver = _driver(router)
    try:
        state = driver.read_state()
    except RouterError as error:
        return jsonify({'success': False, 'message': str(error)}), 502
    finally:
        driver.close()
    report = build_report(desired, state)
    report['return_routing'] = _return_routing(gateway_agent)
    return jsonify({'success': True, 'report': report, 'generated_at': datetime.now().isoformat(timespec='seconds')})


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
