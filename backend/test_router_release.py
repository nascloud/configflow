"""Router releases (P1): iKuai writes, release plan, staged execution with rollback, API."""
import copy

from backend.routers.ikuai import IKuaiDriver, decode_value, encode_value
from backend.routers.plan import build_plan
from backend.routers.release import execute
from backend.routers.report import build_report
from backend.routers.routeros import RouterOSDriver
from backend.test_agent_upgrade_auth import authenticated_app  # noqa: F401  (pytest fixture)
from backend.test_routers import FakeResponse

GATEWAY = '10.0.0.4'


class StatefulIKuai:
    """Minimal iKuai 4.x: show/add/edit/del on static_rt and nat_rule, strings stored encoded."""

    def __init__(self, routes, nats, fail=None):
        self.rows = {'static_rt': copy.deepcopy(routes), 'nat_rule': copy.deepcopy(nats), 'route_object': []}
        self.writes = []
        self.fail = fail
        self.next_id = 100
        self.trust_env = True
        self.verify = True

    def close(self):
        pass

    def post(self, url, json=None, **_):
        if url.endswith('/Action/login'):
            return FakeResponse({'Result': 10000, 'code': 0})
        func, action, param = json['func_name'], json['action'], json['param']
        rows = self.rows[func]
        if action == 'show':
            return FakeResponse({'code': 0, 'results': {'total': len(rows), 'data': copy.deepcopy(rows)}})
        self.writes.append((func, action, copy.deepcopy(param)))
        if self.fail == (func, action):
            return FakeResponse({'code': 2, 'message': '参数错误'})
        # Real iKuai 4.x: name is required, 1-15 characters (web UI form rule).
        if action in ('add', 'edit') and not 1 <= len(str(param.get('tagname') or '')) <= 15:
            return FakeResponse({'code': 2, 'message': '请求参数不合法',
                                 'details': [{'field': 'tagname', 'msg': '名称长度为1-15'}]})
        if action == 'add':
            rows.append({'enabled': 'yes', **param, 'id': self.next_id})
            self.next_id += 1
        elif action == 'edit':
            row = next(row for row in rows if row['id'] == param['id'])
            row.update(param)
        elif action == 'del':
            rows[:] = [row for row in rows if row['id'] != param['id']]
        return FakeResponse({'code': 0, 'results': {}})


HOME_ROUTES = [
    {'id': 1, 'dst_addr': '198.18.0.0', 'netmask': '255.255.0.0', 'gateway': GATEWAY, 'enabled': 'yes',
     'comment': 'fake%20ip', 'tagname': 'RT_1', 'ip_type': '4', 'interface': 'auto', 'prio': 1},
    {'id': 2, 'dst_addr': '91.108.4.0', 'netmask': '255.255.252.0', 'gateway': GATEWAY, 'enabled': 'yes',
     'comment': 'tg', 'tagname': 'RT_2', 'ip_type': '4', 'interface': 'auto', 'prio': 1},
    {'id': 3, 'dst_addr': '1.1.1.1', 'netmask': '255.255.255.255', 'gateway': GATEWAY, 'enabled': 'yes',
     'comment': 'dns', 'tagname': 'RT_3', 'ip_type': '4', 'interface': 'auto', 'prio': 1},
]
HOME_NATS = [
    {'id': 1, 'action': 'filter', 'enabled': 'yes', 'tagname': 'NAT_1', 'comment': 'mihomo', 'protocol': 'any',
     'iinterface': 'any', 'ointerface': 'any', 'nat_addr': '', 'nat_port': '', 'src_addr_inv': 0, 'dst_addr_inv': 0,
     'src_addr': {'custom': ['10.0.0.1-10.0.0.3', '10.0.0.5-10.0.0.255'], 'object': {}},
     'dst_addr': {'custom': ['198.18.0.0/16', '91.108.4.0/22', '185.76.151.0/24'], 'object': {}},
     'src_port': {'custom': {}, 'object': {}}, 'dst_port': {'custom': {}, 'object': {}}},
]
DESIRED = {'fake_ip_range': '198.18.0.0/16', 'proxy_gateway': GATEWAY, 'warnings': [],
           'proxy_cidrs': [{'cidr': '91.108.0.0/16', 'sources': ['Telegram']},
                           {'cidr': '149.154.160.0/20', 'sources': ['Telegram']},
                           {'cidr': '198.18.0.0/16', 'sources': ['fake-ip']}]}
LEGACY_STATUS = {'status': 'ok', 'managed': None,
                 'return_tables': [{'family': 'inet', 'name': 'mihomo_return', 'marks': ['0x00400000']}],
                 'fwmark_rules': [{'priority': '104', 'mark': '0x400000/0x400000', 'table': '104'}]}
ROUTER = {'id': 'r1', 'type': 'ikuai', 'endpoint': 'http://10.0.0.1'}


class FakeAgent:
    def __init__(self, status, fail_apply=False):
        self.state = copy.deepcopy(status)
        self.calls = []
        self.fail_apply = fail_apply

    def apply(self, spec):
        self.calls.append(('apply', spec))
        if self.fail_apply:
            raise RuntimeError('缺少 nft（nftables），无法管理回流规则')
        saved = {key: value for key, value in spec.items() if key != 'adopt_legacy'}
        self.state['return_tables'] = [table for table in self.state['return_tables']
                                       if f'{table["family"]} {table["name"]}' not in spec.get('adopt_legacy', [])]
        self.state['managed'] = {'spec': saved, 'table_present': True}
        return {'success': True}

    def remove(self, restore_legacy):
        self.calls.append(('remove', restore_legacy))
        self.state = copy.deepcopy(LEGACY_STATUS)
        return {'success': True}

    def status(self):
        return copy.deepcopy(self.state)


def _driver(session):
    driver = IKuaiDriver('http://10.0.0.1', 'admin', 'pw')
    driver.session = session
    return driver


def _plan(driver, status=LEGACY_STATUS, desired=DESIRED):
    state = driver.read_state()
    return build_plan(ROUTER, desired, state, build_report(desired, state), status, driver)


def test_encode_matches_ikuai_ui():
    encoded = encode_value({'comment': ' cf:Unraid SS\nnext ', 'gateway': '10.0.0.4', 'prio': 1,
                            'dst_addr': {'custom': ['a b'], 'object': []}, 'x': '100% "a";$b'})
    assert encoded == {'comment': 'cf:Unraid%20SS%0Anext', 'gateway': '10.0.0.4', 'prio': 1,
                       'dst_addr': {'custom': ['a%20b'], 'object': []}, 'x': '100%25%20%22a%22%3B%24b'}
    assert decode_value(encoded)['x'] == '100% "a";$b'
    assert decode_value({'comment': 'Unraid%20SS%0Anext'})['comment'] == 'Unraid SS\nnext'


def test_plan_for_home_setup_adopts_and_fills_gaps():
    plan = _plan(_driver(StatefulIKuai(HOME_ROUTES, HOME_NATS)))
    assert plan['blockers'] == [] and not plan['empty']
    stages = {stage['key']: stage['ops'] for stage in plan['stages']}

    [agent_op] = stages['return_routing']
    # NAT_1 also exempts 185.76.151.0/24, so it must keep getting return marks.
    assert agent_op['spec'] == {'router_ip': '10.0.0.1', 'mark': '0x400000', 'table': 104, 'priority': 104,
                                'proxy_dst': ['91.108.0.0/16', '149.154.160.0/20', '185.76.151.0/24', '198.18.0.0/16'],
                                'adopt_legacy': ['inet mihomo_return']}
    assert agent_op['previous'] is None

    routes = [(op['kind'], op.get('cidr'), op.get('comment')) for op in stages['routes']]
    assert routes == [('route_add', '91.108.0.0/16', 'cf-Telegram'),
                      ('route_add', '149.154.160.0/20', 'cf-Telegram'),
                      ('route_comment', '198.18.0.0/16', 'cf-fake ip')]
    [nat] = stages['nat_bypass']
    assert (nat['kind'], nat['id'], nat['comment'], nat['add_dst']) == \
        ('nat_update', '1', 'cf-mihomo', ['91.108.0.0/16', '149.154.160.0/20'])
    assert len(plan['fingerprint']) == 16


def test_plan_skips_conflicts_and_blocks_without_agent():
    routes = HOME_ROUTES + [{'id': 9, 'dst_addr': '149.154.160.0', 'netmask': '255.255.240.0', 'gateway': '10.0.0.9',
                             'enabled': 'yes', 'comment': 'other'}]
    plan = _plan(_driver(StatefulIKuai(routes, HOME_NATS)))
    cidrs = [op.get('cidr') for stage in plan['stages'] for op in stage['ops']]
    assert '149.154.160.0/20' not in cidrs
    assert '149.154.160.0/20' not in plan['stages'][0]['ops'][0]['spec']['proxy_dst']
    assert any('149.154.160.0/20' in warning and '跳过' in warning for warning in plan['warnings'])

    blocked = _plan(_driver(StatefulIKuai(HOME_ROUTES, HOME_NATS)),
                    status={'status': 'offline', 'message': 'Agent 离线'})
    assert blocked['empty'] and 'Agent 离线' in blocked['blockers'][0]


def test_execute_applies_in_order_and_verifies():
    session = StatefulIKuai(HOME_ROUTES, HOME_NATS)
    driver = _driver(session)
    agent = FakeAgent(LEGACY_STATUS)
    plan = _plan(driver)
    result = execute(plan, driver, agent)
    assert result['ok'], result
    assert [entry['status'] for entry in result['log'] if entry['status'] == 'verified'] == ['verified'] * 3
    assert agent.calls[0][0] == 'apply'

    add = next(param for func, action, param in session.writes if (func, action) == ('static_rt', 'add'))
    assert add == {'enabled': 'yes', 'tagname': add['tagname'], 'ip_type': '4', 'interface': 'auto',
                   'dst_addr': '91.108.0.0', 'gateway': GATEWAY, 'netmask': '255.255.0.0', 'prio': 1,
                   'comment': 'cf-Telegram'}
    assert add['tagname'].startswith('cf_rt_') and len(add['tagname']) <= 15
    edit = next(param for func, action, param in session.writes if (func, action) == ('static_rt', 'edit'))
    assert edit['id'] == 1 and edit['comment'] == 'cf-fake%20ip'
    assert edit['tagname'] == 'RT_1' and edit['enabled'] == 'yes'      # unchanged fields are sent back
    nat = next(param for func, action, param in session.writes if func == 'nat_rule')
    assert nat['dst_addr'] == {'custom': ['198.18.0.0/16', '91.108.4.0/22', '185.76.151.0/24',
                                          '91.108.0.0/16', '149.154.160.0/20'], 'object': []}
    assert nat['src_port'] == {'custom': [], 'object': []} and nat['comment'] == 'cf-mihomo'
    assert nat['src_addr']['custom'] == ['10.0.0.1-10.0.0.3', '10.0.0.5-10.0.0.255']

    # A second preview is empty: everything is managed and in place.
    again = _plan(driver, status={**agent.status(), 'status': 'ok'})
    assert again['empty'], again['stages']


def test_execute_failure_rolls_back_in_reverse():
    session = StatefulIKuai(HOME_ROUTES, HOME_NATS, fail=('nat_rule', 'edit'))
    driver = _driver(session)
    agent = FakeAgent(LEGACY_STATUS)
    result = execute(_plan(driver), driver, agent)
    assert not result['ok'] and result['failed_stage'] == 'nat_bypass'
    assert all(entry['status'] == 'ok' for entry in result['rollback'])
    assert [entry['stage'] for entry in result['rollback']] == ['routes'] * 3 + ['return_routing']
    assert agent.calls[-1] == ('remove', True)          # restore the adopted manual rules
    routes = {(row['dst_addr'], row['comment']) for row in session.rows['static_rt']}
    assert routes == {(row['dst_addr'], row['comment']) for row in HOME_ROUTES}


def test_execute_stops_before_router_when_agent_fails():
    session = StatefulIKuai(HOME_ROUTES, HOME_NATS)
    driver = _driver(session)
    result = execute(_plan(driver), driver, FakeAgent(LEGACY_STATUS, fail_apply=True))
    assert not result['ok'] and result['failed_stage'] == 'return_routing'
    assert session.writes == [] and result['rollback'] == []


class FakeROS(RouterOSDriver):
    def __init__(self):
        pass

    def read_state(self):
        from backend.routers.base import RouterState
        return RouterState()

    def close(self):
        pass


def test_routeros_router_stages_are_manual_script():
    driver = FakeROS()
    state = driver.read_state()
    plan = build_plan({**ROUTER, 'type': 'routeros', 'endpoint': 'https://10.0.0.1'}, DESIRED, state,
                      build_report(DESIRED, state), LEGACY_STATUS, driver)
    manual = {stage['key']: stage['manual'] for stage in plan['stages']}
    assert manual == {'return_routing': False, 'routes': True, 'nat_bypass': True}
    assert '/ip route add dst-address=91.108.0.0/16 gateway=10.0.0.4 comment="cf-Telegram"' in plan['script']
    assert 'src-address-list=cf-nat-src dst-address=198.18.0.0/16' in plan['script']
    assert '/ip firewall address-list add list=cf-nat-src address=10.0.0.1-10.0.0.3' in plan['script']

    agent = FakeAgent(LEGACY_STATUS)
    result = execute(plan, driver, agent)
    assert result['ok'] and [entry['status'] for entry in result['log']] == ['ok', 'verified', 'manual', 'manual']


def test_router_plan_and_apply_api(authenticated_app, monkeypatch):  # noqa: F811
    client, _, agents, admin, _ = authenticated_app
    router_id = client.post('/api/routers', headers=admin, json={
        'name': '主路由', 'type': 'ikuai', 'endpoint': 'http://10.0.0.1', 'username': 'admin',
        'password': 'pw', 'proxy_gateway_agent_id': agents[0]['id']}).get_json()['router']['id']

    from backend.routes import routers as routes_module
    session = StatefulIKuai(HOME_ROUTES, HOME_NATS)
    agent = FakeAgent(LEGACY_STATUS)
    monkeypatch.setitem(routes_module.DRIVERS, 'ikuai', lambda *args, **kwargs: _driver(session))
    monkeypatch.setattr(routes_module, 'compute_desired', lambda *args, **kwargs: DESIRED)
    monkeypatch.setattr(routes_module, '_return_routing', lambda record: {**agent.status(), 'status': 'ok'})
    monkeypatch.setattr(routes_module, 'AgentReturnRouting', lambda manager, record: agent)

    preview = client.post(f'/api/routers/{router_id}/plan', headers=admin)
    assert preview.status_code == 200, preview.get_json()
    plan = preview.get_json()['plan']
    assert session.writes == [] and agent.calls == []       # preview never writes

    stale = client.post(f'/api/routers/{router_id}/apply', headers=admin, json={'fingerprint': 'deadbeef'})
    assert stale.status_code == 409 and session.writes == []

    applied = client.post(f'/api/routers/{router_id}/apply', headers=admin, json={'fingerprint': plan['fingerprint']})
    assert applied.status_code == 200, applied.get_json()
    assert applied.get_json()['result']['ok'] is True
    router = client.get('/api/routers', headers=admin).get_json()['routers'][0]
    assert router['last_release']['ok'] is True and router['last_release']['fingerprint'] == plan['fingerprint']
    assert client.post('/api/routers/missing/apply', headers=admin, json={}).status_code == 404


def test_ikuai_error_details_surface_in_message():
    session = StatefulIKuai(HOME_ROUTES, HOME_NATS)
    driver = _driver(session)
    import pytest
    from backend.routers.base import RouterError
    with pytest.raises(RouterError, match='tagname: 名称长度为1-15'):
        driver._write('static_rt', 'add', {'tagname': '', 'dst_addr': '192.0.2.0'})
