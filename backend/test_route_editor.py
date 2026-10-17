"""Single-route preview/apply contract, authorization, drift and isolation."""
import copy
from types import SimpleNamespace

import pytest

from backend.routers.base import RouterError, RouterRollbackError
from backend.routers import route_editor as editor
from backend.test_agent_upgrade_auth import authenticated_app  # noqa: F401


ROUTER = {'id': 'r1', 'type': 'routeros', 'endpoint': 'http://10.0.0.1',
          'username': 'admin', 'password': 'never-expose-this', 'verify_tls': True}
VALUES = {'cidr': '198.51.100.0/24', 'gateway': '10.0.0.3', 'enabled': True, 'comment': '手工路由'}


def row(**changes):
    value = {'id': '*1', 'cidr': '203.0.113.0/24', 'gateway': '10.0.0.2', 'enabled': False,
             'comment': 'existing', 'table': 'main', 'editable': True, 'reason': '', 'managed': False,
             'snapshot': {'.id': '*1', 'distance': '7', 'scope': '30', 'gateway': '10.0.0.2'}}
    value.update(changes)
    return value


def update_intent(entry):
    return {'action': 'update', 'id': entry['id'], 'revision': editor.public_route(entry)['revision'],
            'values': dict(VALUES)}


def test_preview_normalizes_values_and_preserves_full_snapshot_revision():
    original = row()
    intent = update_intent(original)
    intent['values']['cidr'] = '198.51.100.9/24'
    plan = editor.build_route_plan(ROUTER, [original], intent)
    assert plan['after']['cidr'] == '198.51.100.0/24'
    assert plan['before']['gateway'] == '10.0.0.2' and plan['after']['gateway'] == '10.0.0.3'
    assert 'snapshot' not in plan['before'] and ROUTER['password'] not in str(plan)
    changed = copy.deepcopy(original)
    changed['snapshot']['distance'] = '8'
    with pytest.raises(editor.RouteEditConflict, match='其他操作修改'):
        editor.build_route_plan(ROUTER, [changed], intent)


def test_fingerprint_binds_intent_and_target_not_unrelated_routes_or_secret():
    original = row()
    intent = update_intent(original)
    plan = editor.build_route_plan(ROUTER, [original], intent)
    other = row(id='*9', cidr='10.30.0.0/16')
    assert editor.build_route_plan(ROUTER, [original, other], intent)['fingerprint'] == plan['fingerprint']
    assert editor.build_route_plan({**ROUTER, 'password': 'changed'}, [original], intent)['fingerprint'] == plan['fingerprint']
    assert editor.build_route_plan({**ROUTER, 'endpoint': 'http://10.0.0.9'}, [original], intent)['fingerprint'] != plan['fingerprint']
    intent['values']['enabled'] = False
    assert editor.build_route_plan(ROUTER, [original], intent)['fingerprint'] != plan['fingerprint']


def test_manual_routes_are_not_automatically_tagged_and_managed_warning_is_explicit():
    add = editor.build_route_plan(ROUTER, [], {'action': 'add', 'values': VALUES})
    assert add['after']['comment'] == '手工路由' and not add['after']['managed'] and not add['warnings']
    managed = row(comment='cf-old', managed=True)
    plan = editor.build_route_plan(ROUTER, [managed], update_intent(managed))
    assert plan['warnings'] and not plan['after']['managed']
    tagged = editor.build_route_plan(ROUTER, [], {'action': 'add', 'values': {**VALUES, 'comment': 'cf-test'}})
    assert tagged['warnings'] and tagged['after']['managed']


@pytest.mark.parametrize('action', ['add', 'update'])
def test_duplicate_main_destination_is_rejected_even_if_disabled_or_readonly(action):
    original = row()
    other = row(id='*9', cidr=VALUES['cidr'], editable=False, reason='动态路由')
    payload = update_intent(original) if action == 'update' else {'action': action, 'values': VALUES}
    with pytest.raises(editor.RouteEditConflict, match='已存在'):
        editor.build_route_plan(ROUTER, [original, other], payload)


@pytest.mark.parametrize('action', ['update', 'delete'])
def test_readonly_and_missing_route_cannot_be_mutated(action):
    original = row(editable=False, reason='动态路由')
    payload = {'action': action, 'id': original['id'], 'revision': editor.public_route(original)['revision']}
    if action == 'update':
        payload['values'] = VALUES
    with pytest.raises(editor.RouteEditConflict, match='动态路由'):
        editor.build_route_plan(ROUTER, [original], payload)
    with pytest.raises(editor.RouteEditConflict, match='不存在'):
        editor.build_route_plan(ROUTER, [], payload)


@pytest.mark.parametrize('payload', [
    None, [], {'action': 'flush'}, {'action': 'add', 'values': {**VALUES, 'distance': 9}},
    {'action': 'add', 'values': {**VALUES, 'cidr': '2001:db8::/32'}},
    {'action': 'add', 'values': {**VALUES, 'gateway': 'ether1'}},
    {'action': 'add', 'values': {**VALUES, 'gateway': '0.0.0.0'}},
    {'action': 'add', 'values': {**VALUES, 'enabled': 'true'}},
    {'action': 'add', 'values': {**VALUES, 'comment': 'bad\0comment'}},
    {'action': 'delete', 'id': '*1'},
])
def test_malformed_or_unsupported_route_intent_is_rejected(payload):
    with pytest.raises(editor.RouteEditValidationError):
        editor.build_route_plan(ROUTER, [], payload)


def test_noop_preview_does_not_call_mutating_driver(monkeypatch):
    original = row()
    intent = update_intent(original)
    intent['values'] = {key: original[key] for key in VALUES}
    plan = editor.build_route_plan(ROUTER, [original], intent)
    monkeypatch.setattr(editor, '_adapter', lambda *_: pytest.fail('No-op must not write'))
    assert plan['empty'] and not editor.execute_route_plan(None, [original], plan)['changed']


@pytest.fixture
def route_api(authenticated_app, monkeypatch):  # noqa: F811
    client, _, agents, admin, _ = authenticated_app
    response = client.post('/api/routers', headers=admin, json={
        **ROUTER, 'name': 'ROS', 'proxy_gateway_agent_id': '', 'manage_return_routing': False,
    })
    assert response.status_code == 200, response.get_json()
    router_id = response.get_json()['router']['id']
    rows, writes, closed = [row()], [], []

    class Driver:
        vendor = 'routeros'
        def __init__(self, *args, **kwargs):
            pass
        def close(self):
            closed.append(True)

    def mutate(driver, action, expected, values):
        writes.append((action, copy.deepcopy(expected), copy.deepcopy(values)))
        if action == 'delete':
            rows.clear()
            return {'changed': True, 'entry': None}
        entry = row(**(values or {}))
        entry['snapshot'] = {**entry['snapshot'], **values}
        rows[:] = [entry]
        return {'changed': True, 'entry': entry}

    from backend.routes import routers as routes_module
    monkeypatch.setitem(routes_module.DRIVERS, 'routeros', Driver)
    monkeypatch.setattr(editor, '_adapter', lambda _: SimpleNamespace(
        read_routes=lambda _: copy.deepcopy(rows), mutate_route=mutate))
    monkeypatch.setattr(routes_module, '_inspect', lambda *_: pytest.fail('Manual route edits must not inspect Agents/NAT/DNS'))
    return client, admin, agents, f'/api/routers/{router_id}/routes', rows, writes, closed


def test_api_requires_admin_for_list_preview_and_write(route_api):
    client, admin, agents, base, _, writes, _ = route_api
    for headers in ({}, {'Authorization': 'Bearer ' + agents[0]['token']}):
        assert client.get(base, headers=headers).status_code in (401, 403)
        for suffix in ('/plan', '/apply'):
            response = client.post(base + suffix, headers=headers, json={'action': 'add', 'values': VALUES})
            assert response.status_code in (401, 403)
    assert writes == []
    assert client.get(base, headers=admin).status_code == 200


def test_api_lists_safe_fields_previews_without_writes_and_requires_same_fingerprint(route_api):
    client, admin, _, base, rows, writes, closed = route_api
    response = client.get(base, headers=admin)
    [item] = response.get_json()['routes']
    assert 'snapshot' not in item and len(item['revision']) == 64
    intent = update_intent(rows[0])
    preview = client.post(base + '/plan', headers=admin, json=intent)
    assert preview.status_code == 200 and writes == []
    plan = preview.get_json()['plan']
    assert client.post(base + '/apply', headers=admin, json=intent).status_code == 409
    changed_intent = {**intent, 'values': {**VALUES, 'gateway': '10.0.0.99'}, 'fingerprint': plan['fingerprint']}
    assert client.post(base + '/apply', headers=admin, json=changed_intent).status_code == 409
    assert writes == []
    result = client.post(base + '/apply', headers=admin, json={**intent, 'fingerprint': plan['fingerprint']})
    assert result.status_code == 200 and result.get_json()['result']['changed']
    assert len(writes) == 1 and writes[0][0] == 'update'
    assert 'snapshot' not in result.get_json()['result']['entry']
    assert len(closed) == 5


def test_api_detects_external_advanced_setting_change_before_apply(route_api):
    client, admin, _, base, rows, writes, _ = route_api
    intent = update_intent(rows[0])
    plan = client.post(base + '/plan', headers=admin, json=intent).get_json()['plan']
    rows[0]['snapshot']['scope'] = '11'
    result = client.post(base + '/apply', headers=admin, json={**intent, 'fingerprint': plan['fingerprint']})
    assert result.status_code == 409 and writes == []
    assert '修改' in result.get_json()['message']


def test_api_delete_has_explicit_preview_and_is_independent_of_publish(route_api):
    client, admin, _, base, rows, writes, _ = route_api
    intent = {'action': 'delete', 'id': rows[0]['id'], 'revision': editor.public_route(rows[0])['revision']}
    preview = client.post(base + '/plan', headers=admin, json=intent).get_json()['plan']
    assert preview['before']['cidr'] == rows[0]['cidr'] and preview['after'] is None
    response = client.post(base + '/apply', headers=admin, json={**intent, 'fingerprint': preview['fingerprint']})
    assert response.status_code == 200 and response.get_json()['result'] == {'changed': True, 'entry': None}
    assert len(writes) == 1 and not rows


def test_api_reports_unconfirmed_rollback_honestly(route_api, monkeypatch):
    client, admin, _, base, _, writes, _ = route_api
    adapter = editor._adapter(None)
    def fail(*_):
        raise RouterRollbackError('检测到外部修改，未覆盖现场；回滚未完成')
    adapter.mutate_route = fail
    monkeypatch.setattr(editor, '_adapter', lambda _: adapter)
    intent = {'action': 'add', 'values': VALUES}
    plan = client.post(base + '/plan', headers=admin, json=intent).get_json()['plan']
    result = client.post(base + '/apply', headers=admin, json={**intent, 'fingerprint': plan['fingerprint']})
    assert result.status_code == 502 and '回滚未完成' in result.get_json()['message']
    assert writes == []


def test_manual_route_api_respects_profile_and_missing_router(route_api):
    client, admin, _, base, _, writes, _ = route_api
    created = client.post('/api/profiles', headers=admin, json={'name': '另一配置空间'})
    assert created.status_code == 201, created.get_json()
    profile = created.get_json()
    other = {**admin, 'X-ConfigFlow-Profile': profile['id']}
    assert client.get(base, headers=other).status_code == 404
    assert client.post(base + '/plan', headers=other, json={'action': 'add', 'values': VALUES}).status_code == 404
    assert client.post(base + '/apply', headers=other, json={'action': 'add', 'values': VALUES}).status_code == 404
    assert not writes


def test_invalid_api_payload_is_rejected_before_device_read(route_api, monkeypatch):
    client, admin, _, base, _, writes, _ = route_api
    from backend.routes import routers as routes_module
    monkeypatch.setattr(routes_module, 'read_routes', lambda _: pytest.fail('invalid intent must not contact device'))
    assert client.post(base + '/plan', headers=admin, json={'action': 'flush'}).status_code == 400
    assert client.post(base + '/apply', headers=admin, json=None).status_code == 400
    assert not writes
