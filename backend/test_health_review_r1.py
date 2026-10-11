"""Adversarial regressions from independent health-check review R1."""
from copy import deepcopy
import pytest
import yaml
from flask import Flask
from backend.common import config
from backend.common.config_repository import ProfileRepository
from backend.routes import register_blueprints
from backend.converters.mihomo import generate_mihomo_config


@pytest.mark.parametrize('transport', ['api', 'mcp'])
@pytest.mark.parametrize('candidate', [
    {'include_all': True}, {'include-all': True}, {'use': ['missing-provider']},
    {'proxies': ['missing']}, {'manual_nodes': ['missing']},
    {'subscriptions': ['missing']}, {'aggregations': ['missing']},
    {'include_groups': ['missing']}, {'follow_group': 'missing'},
])
def test_creation_rejects_nonmanaged_or_missing_candidates_atomically(tmp_path, transport, candidate):
    repo = ProfileRepository(tmp_path)
    config.set_repository(repo)
    app = Flask(__name__)
    register_blueprints(app)
    from backend.mcp_server import mcp_bp
    app.register_blueprint(mcp_bp)
    repo.save_system({'system_config': {'config_token': 'r1-fixture-token'}})
    client = app.test_client()
    before = deepcopy(repo.get_compat_config('default')['proxy_groups'])
    group = {'id': 'r1', 'name': 'R1', 'type': 'url-test', **candidate}
    if transport == 'api':
        response = client.post('/api/proxy-groups', json=group)
        assert response.status_code in (400, 409)
    else:
        response = client.post('/mcp', headers={'Authorization': 'Bearer r1-fixture-token'}, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'manage_proxy_group', 'arguments': {'action': 'create', 'data': group}}})
        assert response.json['result']['isError'] is True
    assert ProfileRepository(tmp_path).get_compat_config('default')['proxy_groups'] == before


@pytest.mark.parametrize('field,kind', [('subscriptions','subscriptions'),('aggregations','subscription_aggregations'),('manual_nodes','nodes')])
def test_create_first_group_from_unreferenced_shared_catalog(tmp_path,field,kind):
    repo=ProfileRepository(tmp_path);config.set_repository(repo)
    repo.update_shared_transaction(lambda p:p[kind].append({'id':'s','name':'Source','url':'https://example.test','proxy_string':'http://127.0.0.1:19876','subscriptions':[],'nodes':[]}))
    app=Flask(__name__);register_blueprints(app)
    response=app.test_client().post('/api/proxy-groups',json={'id':'g','name':'G','type':'url-test',field:['s']})
    assert response.status_code==200
    assert ProfileRepository(tmp_path).get_compat_config('default')['proxy_groups'][0][field]==['s']


@pytest.mark.parametrize('source', ['subscription','aggregation'])
def test_legacy_proxies_are_not_candidates_when_generator_ignores_them(tmp_path, source):
    repo=ProfileRepository(tmp_path);config.set_repository(repo)
    kind='subscriptions' if source=='subscription' else 'subscription_aggregations'
    repo.update_shared_transaction(lambda p:p[kind].append({'id':'s','name':'Source','url':'https://example.test','subscriptions':[],'nodes':[]}))
    app=Flask(__name__);register_blueprints(app)
    response=app.test_client().post('/api/proxy-groups',json={'id':'g','name':'G','type':'url-test','source':source,'proxies':['s']})
    assert response.status_code==400
    assert repo.get_compat_config('default')['proxy_groups']==[]


@pytest.mark.parametrize('field,kind', [('manual_nodes','nodes'), ('subscriptions','subscriptions'), ('aggregations','subscription_aggregations'), ('include_groups','proxy_groups'), ('follow_group','proxy_groups')])
def test_disabled_resource_is_not_a_creation_candidate(tmp_path, field, kind):
    repo = ProfileRepository(tmp_path)
    config.set_repository(repo)
    if kind == 'proxy_groups':
        repo.update_profile_transaction('default', lambda p: p['proxy_groups'].append({'id':'off','name':'Off','type':'select','manual_nodes':['DIRECT'],'enabled':False}))
    else:
        repo.update_shared_transaction(lambda p: p[kind].append({'id':'off','name':'Off','enabled':False,'url':'https://example.test','proxy_string':'http://127.0.0.1:19876','subscriptions':[],'nodes':[]}))
    app=Flask(__name__);register_blueprints(app)
    before=deepcopy(repo.get_compat_config('default')['proxy_groups'])
    response=app.test_client().post('/api/proxy-groups',json={'id':'new','name':'New','type':'url-test',field:'off' if field=='follow_group' else ['off']})
    assert response.status_code == 400
    assert repo.get_compat_config('default')['proxy_groups'] == before


@pytest.mark.parametrize('follow', [False, True])
def test_load_balance_final_yaml_does_not_emit_tolerance(tmp_path, follow):
    repo = ProfileRepository(tmp_path)
    def seed(p):
        p['proxy_groups'] = [{'id': 'lb', 'name': 'LB', 'type': 'load-balance', 'manual_nodes': ['DIRECT'],
                              'tolerance': 123, 'url': 'https://example.test/204', 'interval': 60}]
        if follow:
            p['proxy_groups'].append({'id': 'f', 'name': 'Follow', 'type': 'url-test', 'follow_group': 'lb'})
        p['rule_configs'] = []
    repo.update_profile_transaction('default', seed)
    emitted = yaml.safe_load(generate_mihomo_config(repo.get_compat_config('default'), preflight_providers=False))
    groups = emitted['proxy-groups']
    assert groups[-1]['type'] == 'load-balance'
    assert 'tolerance' not in groups[-1]


def test_url_test_legacy_and_explicit_tolerance_are_preserved(tmp_path):
    repo = ProfileRepository(tmp_path)
    def seed(p):
        p['proxy_groups'] = [{'id': 'old', 'name': 'Old', 'type': 'url-test', 'manual_nodes': ['DIRECT']},
                              {'id': 'new', 'name': 'New', 'type': 'url-test', 'manual_nodes': ['DIRECT'], 'tolerance': 50}]
        p['rule_configs'] = []
    repo.update_profile_transaction('default', seed)
    emitted = yaml.safe_load(generate_mihomo_config(repo.get_compat_config('default'), preflight_providers=False))
    assert [g['tolerance'] for g in emitted['proxy-groups']] == [100, 50]


def test_persisted_legacy_empty_candidate_is_not_globally_rejected(tmp_path):
    repo=ProfileRepository(tmp_path)
    def seed(p):
        p['proxy_groups']=[{'id':'legacy','name':'Legacy','type':'select'}]
        p['rule_configs']=[]
    repo.update_profile_transaction('default',seed)
    cfg=ProfileRepository(tmp_path).get_compat_config('default')
    emitted=yaml.safe_load(generate_mihomo_config(cfg,preflight_providers=False))
    assert emitted['proxy-groups']==[{'name':'Legacy','type':'select'}]


@pytest.mark.parametrize('transport',['api','mcp'])
def test_explicit_direct_survives_create_reopen_and_generation(tmp_path,transport):
    repo=ProfileRepository(tmp_path);config.set_repository(repo)
    repo.save_system({'system_config':{'config_token':'r1-fixture-token'}})
    app=Flask(__name__);register_blueprints(app)
    from backend.mcp_server import mcp_bp
    app.register_blueprint(mcp_bp)
    client=app.test_client()
    group={'id':'direct','name':'Direct choice','type':'url-test','manual_nodes':['DIRECT']}
    if transport=='api':assert client.post('/api/proxy-groups',json=group).status_code==200
    else:
        result=client.post('/mcp',headers={'Authorization':'Bearer r1-fixture-token'},json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'manage_proxy_group','arguments':{'action':'create','data':group}}})
        assert result.json['result']['isError'] is False
    cfg=ProfileRepository(tmp_path).get_compat_config('default')
    assert cfg['proxy_groups'][0]['manual_nodes']==['DIRECT']
    emitted=yaml.safe_load(generate_mihomo_config(cfg,preflight_providers=False))
    assert emitted['proxy-groups'][0]['proxies']==['DIRECT']
    assert emitted['proxy-groups'][0]['tolerance']==50
