"""Second independent-review regressions, exercising real REST and MCP writes."""
from copy import deepcopy
import pytest
from flask import Flask
from backend.common import config
from backend.common.config_repository import ProfileRepository
from backend.routes import register_blueprints

@pytest.fixture
def env(tmp_path):
    repo = ProfileRepository(tmp_path); config.set_repository(repo)
    repo.save_system({'system_config': {'config_token': 're1-fixture-token'}})
    repo.update_shared_transaction(lambda p: p['subscriptions'].append({'id':'s','name':'S','url':'http://127.0.0.1:9/sub'}))
    app = Flask(__name__); register_blueprints(app)
    from backend.mcp_server import mcp_bp
    app.register_blueprint(mcp_bp)
    return repo, app.test_client(), tmp_path

def write(client, transport, action, data):
    if transport == 'api':
        r = client.post('/api/proxy-groups', json=data) if action=='create' else client.put('/api/proxy-groups/g', json=data)
        return r.status_code == 200
    r=client.post('/mcp', headers={'Authorization':'Bearer re1-fixture-token'}, json={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'manage_proxy_group','arguments':{'action':action,'id':'g','data':data}}})
    return not r.json['result']['isError']

@pytest.mark.parametrize('transport',['api','mcp'])
@pytest.mark.parametrize('patch',[
    {'manual_nodes':[], 'include_all':True}, {'manual_nodes':[], 'use':['unsupported']},
    {'proxies_order':[{'type':'subscription','id':'s'}]},
    {'proxies_order':[{'type':'node','id':'REJECT'}]},
])
def test_final_candidate_update_rejected_atomically(env,transport,patch):
    repo,c,path=env
    assert write(c,transport,'create',{'id':'g','name':'G','type':'url-test','manual_nodes':['DIRECT']})
    before=deepcopy(repo.get_compat_config('default')['proxy_groups'])
    assert not write(c,transport,'update',patch)
    assert ProfileRepository(path).get_compat_config('default')['proxy_groups']==before

@pytest.mark.parametrize('transport',['api','mcp'])
def test_order_must_consume_declared_candidates_on_create(env,transport):
    repo,c,path=env
    assert not write(c,transport,'create',{'id':'g','name':'G','type':'url-test','manual_nodes':['DIRECT'],'proxies_order':[{'type':'subscription','id':'s'}]})
    assert ProfileRepository(path).get_compat_config('default')['proxy_groups']==[]

@pytest.mark.parametrize('transport',['api','mcp'])
@pytest.mark.parametrize('new_type',['fallback','url-test'])
def test_type_is_business_field_not_replace_mode(env,transport,new_type):
    repo,c,path=env
    health={'url':'https://example.test/probe','interval':77,'timeout':8250,'lazy':False,'expected-status':'200-299','tolerance':23}
    assert write(c,transport,'create',{'id':'g','name':'G','type':'url-test','manual_nodes':['DIRECT'],**health})
    assert write(c,transport,'update',{'type':new_type})
    actual=ProfileRepository(path).get_compat_config('default')['proxy_groups'][0]
    for key,value in health.items():
        if key=='tolerance' and new_type!='url-test': assert key not in actual
        else: assert actual[key]==value

@pytest.mark.parametrize('transport',['api','mcp'])
def test_explicit_health_delete_protocol(env,transport):
    repo,c,path=env
    assert write(c,transport,'create',{'id':'g','name':'G','type':'url-test','manual_nodes':['DIRECT'],'timeout':8250,'lazy':False})
    assert write(c,transport,'update',{'name':'Renamed','delete_fields':['timeout','lazy']})
    actual=ProfileRepository(path).get_compat_config('default')['proxy_groups'][0]
    assert 'timeout' not in actual and 'lazy' not in actual and 'delete_fields' not in actual
    assert actual['interval']==60

@pytest.mark.parametrize('patch', [
    {'delete_fields':'timeout'}, {'delete_fields':['name']},
    {'delete_fields':['timeout','timeout']}, {'delete_fields':['timeout'],'timeout':8000},
    {'proxies_order':[None]}, {'proxies_order':['DIRECT']},
    {'proxies_order':[{'type':'node','id':[]}]},
    {'proxies_order':[{'type':'node','id':'DIRECT','extra':True}]},
    {'proxies_order':[{'type':'node','id':'DIRECT'},{'type':'node','id':'DIRECT'}]},
    {'manual_nodes':True}, {'manual_nodes':None}, {'proxies_order':True},
    {'follow_group':['g']},
])
def test_malformed_update_returns_controlled_error_without_write(env,patch):
    repo,c,path=env
    assert write(c,'api','create',{'id':'g','name':'G','type':'select','manual_nodes':['DIRECT']})
    before=deepcopy(repo.get_compat_config('default')['proxy_groups'])
    r=c.put('/api/proxy-groups/g',json=patch)
    assert r.status_code==400
    assert ProfileRepository(path).get_compat_config('default')['proxy_groups']==before

def test_follow_legacy_node_source_uses_same_resolved_candidates(env):
    import yaml
    from backend.converters.mihomo import generate_mihomo_config
    repo,c,path=env
    assert write(c,'api','create',{'id':'g','name':'G','type':'select','source':'node','proxies':['DIRECT']})
    r=c.post('/api/proxy-groups',json={'id':'f','name':'F','type':'select','follow_group':'g'})
    assert r.status_code==200
    cfg=ProfileRepository(path).get_compat_config('default')
    emitted=yaml.safe_load(generate_mihomo_config(cfg,preflight_providers=False))
    assert emitted['proxy-groups'][1]['proxies']==['DIRECT']

@pytest.mark.parametrize('transport',['api','mcp'])
def test_explicit_deletion_wins_over_type_transition_defaults(env,transport):
    repo,c,path=env
    assert write(c,transport,'create',{'id':'g','name':'G','type':'select','manual_nodes':['DIRECT']})
    assert write(c,transport,'update',{'type':'url-test','delete_fields':['timeout','lazy']})
    actual=ProfileRepository(path).get_compat_config('default')['proxy_groups'][0]
    assert 'timeout' not in actual and 'lazy' not in actual
    assert actual['interval']==60

@pytest.mark.parametrize('transport',['api','mcp'])
def test_delete_fields_is_update_only(env,transport):
    repo,c,path=env
    assert not write(c,transport,'create',{'id':'g','name':'G','type':'url-test','manual_nodes':['DIRECT'],'delete_fields':['timeout']})
    assert ProfileRepository(path).get_compat_config('default')['proxy_groups']==[]

@pytest.mark.parametrize('transport',['api','mcp'])
@pytest.mark.parametrize('ordered',[False,True])
@pytest.mark.parametrize('follow',[False,True])
@pytest.mark.parametrize('kind',['manual','node','subscription','aggregation','group','legacy-node','legacy-strategy'])
def test_all_consumed_generator_branches_native_parse(env,transport,ordered,follow,kind):
    import yaml, subprocess, os, shutil
    binary = os.environ.get('MIHOMO_BINARY') or shutil.which('mihomo')
    if not binary:
        pytest.skip('native matrix requires MIHOMO_BINARY or installed mihomo')
    from backend.converters.mihomo import generate_mihomo_config
    repo,c,path=env
    repo.update_shared_transaction(lambda p: p['nodes'].append({'id':'n','name':'Node','proxy_string':'{"type":"http","server":"127.0.0.1","port":19876}'}))
    repo.update_shared_transaction(lambda p: p['subscription_aggregations'].append({'id':'a','name':'Agg','subscriptions':['s'],'nodes':[]}))
    repo.update_profile_transaction('default',lambda p:p.update(rule_configs=[{'id':'match','itemType':'rule','rule_type':'MATCH','policy':'DIRECT'}],proxy_groups=[{'id':'base','name':'Base','type':'select','manual_nodes':['DIRECT']}]))
    repo.update_profile_transaction('default',lambda p:p['mihomo'].update(custom_config='mixed-port: 17899\nmode: rule\nlog-level: warning\n'))
    sources={'manual':{'manual_nodes':['DIRECT']},'node':{'manual_nodes':['n']},'subscription':{'subscriptions':['s']},'aggregation':{'aggregations':['a']},'group':{'include_groups':['base']},'legacy-node':{'source':'node','proxies':['n']},'legacy-strategy':{'source':'strategy','proxies':['base']}}
    group={'id':'g','name':'G','type':'url-test',**sources[kind]}
    if ordered:
        order={'manual':[{'type':'node','id':'DIRECT'}],'node':[{'type':'node','id':'n'}],'subscription':[], 'aggregation':[{'type':'aggregation','id':'a'}], 'group':[{'type':'strategy','id':'base'}], 'legacy-node':[{'type':'node','id':'n'}], 'legacy-strategy':[{'type':'strategy','id':'base'}]}
        group['proxies_order']=order[kind]
    assert write(c,transport,'create',group)
    if follow:
        assert c.post('/api/proxy-groups',json={'id':'f','name':'F','type':'select','follow_group':'g'}).status_code==200
    cfg=ProfileRepository(path).get_compat_config('default')
    text=generate_mihomo_config(cfg,base_url='http://127.0.0.1:5187',preflight_providers=False)
    emitted=yaml.safe_load(text)['proxy-groups'][-1]
    expected={'manual':('proxies',['DIRECT']),'node':('proxies',['Node']),'subscription':('use',['S']),'aggregation':('use',['Agg']),'group':('proxies',['Base']),'legacy-node':('proxies',['Node']),'legacy-strategy':('proxies',['Base'])}
    key,value=expected[kind];assert emitted[key]==value
    f=path/'generated.yaml';f.write_text(text)
    provider_dir=path/'native'/'providers';provider_dir.mkdir(parents=True)
    for provider in ('S','Agg'):
        (provider_dir/f'{provider}.yaml').write_text('proxies:\n  - name: Fixture\n    type: http\n    server: 127.0.0.1\n    port: 19876\n')
    native=subprocess.run([binary,'-t','-d',str(path/'native'),'-f',str(f)],capture_output=True,text=True,timeout=15)
    assert native.returncode==0,native.stdout+native.stderr


def test_legacy_empty_unrelated_update_preserves_but_valid_to_empty_rejects(env):
    repo,c,path=env
    repo.update_profile_transaction('default',lambda p:p['proxy_groups'].append({'id':'g','name':'G','type':'select'}))
    assert write(c,'api','update',{'name':'Renamed'})
    assert ProfileRepository(path).get_compat_config('default')['proxy_groups'][0]=={'id':'g','name':'Renamed','type':'select'}
    assert write(c,'api','update',{'manual_nodes':['DIRECT']})
    before=deepcopy(repo.get_compat_config('default')['proxy_groups'])
    assert not write(c,'api','update',{'manual_nodes':[]})
    assert ProfileRepository(path).get_compat_config('default')['proxy_groups']==before
