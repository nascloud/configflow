"""Preview/apply freshness ignores traffic counters, never routing changes."""
import copy

import pytest

from backend.test_agent_upgrade_auth import authenticated_app  # noqa: F401
from backend.test_ros_policy import GATEWAY, MANGLE, ROUTER, ROUTES, TARGET, MemoryROS


@pytest.fixture
def policy_api(authenticated_app, monkeypatch):
    from backend.routes import routers

    client, _, agents, admin, _ = authenticated_app
    driver = MemoryROS()
    driver.rows[MANGLE] = [
        {'.id': '*M1', 'chain': 'forward', 'action': 'change-mss',
         'new-mss': 'clamp-to-pmtu', 'protocol': 'tcp', 'out-interface': 'pppoe-out1',
         'disabled': 'false', 'bytes': '100', 'packets': '2'},
        {'.id': '*M2', 'chain': 'forward', 'action': 'log', 'disabled': 'true',
         'bytes': '200', 'packets': '4'},
    ]
    monkeypatch.setitem(routers.DRIVERS, 'routeros', lambda *a, **kw: driver)
    monkeypatch.setattr(routers, '_agent', lambda _: {'host': GATEWAY})

    def compute(config, gateway, selected):
        entries = [{'cidr': '198.18.0.0/16', 'sources': ['fake-ip']}]
        if 'telegram' in selected:
            entries.append({'cidr': TARGET, 'sources': ['Telegram']})
        return {'fake_ip_range': '198.18.0.0/16', 'proxy_gateway': gateway,
                'proxy_cidrs': entries, 'warnings': []}

    monkeypatch.setattr(routers, 'compute_desired', compute)
    monkeypatch.setattr('backend.routers.desired.compute_desired', compute)
    monkeypatch.setattr('backend.common.oxidns_device_policy.read_policies', lambda *_: {
        'editable': True, 'policies': [{'ip': '192.168.0.101', 'mode': 'smart'}]})
    created = client.post('/api/routers', headers=admin, json={
        **ROUTER, 'name': 'ROS', 'username': 'admin', 'password': 'pw',
        'proxy_gateway_agent_id': agents[0]['id'],
    })
    assert created.status_code == 200, created.get_json()
    path = '/api/routers/' + created.get_json()['router']['id']

    def preview():
        response = client.post(path + '/plan', headers=admin)
        assert response.status_code == 200, response.get_json()
        return response.get_json()['plan']

    def apply(plan):
        return client.post(path + '/apply', headers=admin, json={'fingerprint': plan['fingerprint']})

    return driver, preview, apply


@pytest.mark.parametrize('already_published', [False, True])
@pytest.mark.parametrize('counters', [('999999', '7777'), ('0', '0')])
def test_traffic_or_counter_reset_allows_previewed_publish(policy_api, already_published, counters):
    driver, preview, apply = policy_api
    if already_published:
        response = apply(preview())
        assert response.status_code == 200 and response.get_json()['result']['ok']
    before = preview()
    assert not before['blockers'] and before['empty'] is already_published
    writes = copy.deepcopy(driver.writes)
    for path in (MANGLE, '/ip/firewall/nat'):
        for row in driver.rows[path]:
            row.update(bytes=counters[0], packets=counters[1])
    after = preview()
    assert after['fingerprint'] == before['fingerprint']
    assert driver.writes == writes
    response = apply(before)
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['result']['ok']
    if already_published:
        assert driver.writes == writes


@pytest.mark.parametrize('change', ['matcher', 'action', 'unknown_field', 'order', 'gateway', 'active'])
def test_actual_configuration_or_egress_change_still_rejects_before_write(policy_api, change):
    driver, preview, apply = policy_api
    before = preview()
    assert not before['blockers']
    if change == 'matcher':
        driver.rows[MANGLE][0]['src-address'] = '192.168.0.105'
    elif change == 'action':
        driver.rows[MANGLE][0]['action'] = 'accept'
    elif change == 'unknown_field':
        driver.rows[MANGLE][0]['future-config-field'] = 'must-be-checked'
    elif change == 'order':
        driver.rows[MANGLE].reverse()
    elif change == 'gateway':
        driver.rows[ROUTES][0].update(gateway='other-lan', **{'immediate-gw': 'other-lan'})
    else:
        driver.rows[ROUTES][0]['active'] = 'false'
    response = apply(before)
    assert response.status_code == 409, response.get_json()
    assert not driver.writes
