"""Dialer stable-reference regressions using real profile storage and routes."""
import pytest
import yaml
from backend.test_qa_integrity import make_app


def node(id, name=None, **extra):
    return dict(id=id, name=name or id, enabled=True, type='http', server='example.test', port=80, **extra)


def test_stable_reference_emits_unused_relay_and_tracks_rename(tmp_path):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    assert client.post('/api/nodes', json=node('relay')).status_code == 200
    assert client.post('/api/nodes', json=node('exit', dialer_ref={'type': 'node', 'id': 'relay'})).status_code == 200
    client.post('/api/proxy-groups', json={'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['exit']})
    assert client.put('/api/nodes/relay', json=node('relay', 'Renamed')).status_code == 200
    response = client.post('/api/generate/mihomo/preview', json={})
    assert response.status_code == 200, response.get_data(as_text=True)
    content = response.get_json()['content']
    proxies = {p['name']: p for p in yaml.safe_load(content)['proxies']}
    assert proxies['exit']['dialer-proxy'] == 'Renamed'
    assert 'Renamed' in proxies


@pytest.mark.parametrize('failure', [None, 'profile', 'system'])
def test_node_delete_cleans_related_resources_in_one_transaction(tmp_path, monkeypatch, failure):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('unused'))
    repo.update_profile_transaction('default', lambda p: p['subscription_aggregations'].append({'id': 'agg', 'nodes': ['unused'], 'subscriptions': []}))
    client.post('/api/proxy-groups', json={'id': 'g', 'name': 'Group', 'type': 'select', 'manual_nodes': ['unused', 'DIRECT'], 'aggregations': ['agg'], 'proxies_order': [{'type': 'node', 'id': 'unused'}, {'type': 'node', 'id': 'DIRECT'}]})
    before = repo.get_profile('default')
    system_before = repo.system_file.read_bytes()
    if failure:
        original = repo._write_json
        def injected(path, data):
            if path == (repo.system_file if failure == 'system' else repo._profile_path('default')):
                raise OSError('injected write failure')
            original(path, data)
        monkeypatch.setattr(repo, '_write_json', injected)
    response = client.delete('/api/nodes/unused')
    if failure:
        assert response.status_code == 500
        assert repo.get_profile('default') == before
        assert repo.system_file.read_bytes() == system_before
    else:
        assert response.status_code == 200
        profile = repo.get_profile('default')
        assert profile['nodes'] == []
        assert profile['subscription_aggregations'][0]['enabled'] is False
        assert profile['proxy_groups'][0]['manual_nodes'] == ['DIRECT']
        assert profile['proxy_groups'][0]['aggregations'] == []
        assert profile['proxy_groups'][0]['proxies_order'] == [{'type': 'node', 'id': 'DIRECT'}]


@pytest.mark.parametrize('resource', ['nodes', 'proxy-groups'])
@pytest.mark.parametrize('conflict', ['name', 'builtin', 'id'])
def test_api_rejects_ambiguous_dialer_identity_without_write(tmp_path, resource, conflict):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('relay'))
    client.post('/api/nodes', json=node('exit', dialer_ref={'type': 'node', 'id': 'relay'}))
    client.post('/api/proxy-groups', json={'id': 'g', 'name': 'Group', 'type': 'select', 'manual_nodes': ['DIRECT']})
    before = repo.get_profile('default')
    data = node('new') if resource == 'nodes' else {'id': 'new', 'name': 'New', 'type': 'select', 'manual_nodes': ['DIRECT']}
    if conflict == 'name':
        data['name'] = 'relay'
    elif conflict == 'builtin':
        data['name'] = 'DIRECT'
    else:
        data['id'] = 'relay' if resource == 'nodes' else 'g'
    response = client.post('/api/' + resource, json=data)
    assert response.status_code == 400
    assert repo.get_profile('default') == before


@pytest.mark.parametrize('resource,key', [('nodes', 'nodes'), ('proxy-groups', 'groups')])
def test_legacy_reorder_cannot_bypass_dialer_validation(tmp_path, resource, key):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('relay'))
    client.post('/api/nodes', json=node('exit', dialer_ref={'type': 'node', 'id': 'relay'}))
    group = {'id': 'g', 'name': 'Group', 'type': 'select', 'manual_nodes': ['relay']}
    client.post('/api/proxy-groups', json=group)
    if resource == 'proxy-groups':
        client.put('/api/nodes/exit', json=node('exit', dialer_ref={'type': 'group', 'id': 'g'}))
    before = repo.get_profile('default')
    replacement = [node('exit', dialer_ref={'type': 'node', 'id': 'relay'})] if resource == 'nodes' else [{**group, 'manual_nodes': ['exit']}]
    response = client.post('/api/' + resource + '/reorder', json={key: replacement})
    assert response.status_code == 400
    assert repo.get_profile('default') == before


def test_surge_policy_path_never_silently_drops_raw_dialer():
    from backend.converters.surge import convert_proxies_to_surge_text
    from backend.utils.strategy_references import StrategyReferenceError
    with pytest.raises(StrategyReferenceError, match='Surge'):
        convert_proxies_to_surge_text([{'name': 'Exit', 'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'Relay'}])


def test_subscription_surge_consumer_reports_unsupported_dialer_as_400(tmp_path):
    from backend.utils.subscription_cache import save_subscription_nodes
    app, repo = make_app(tmp_path)
    repo.update_profile_transaction('default', lambda p: p['subscriptions'].append({'id': 's', 'name': 'Cached', 'enabled': True, 'url': ''}))
    save_subscription_nodes('s', [node('cached', params={'dialer-proxy': 'Relay'})], profile_id='default')
    response = app.test_client().get('/api/subscriptions/s/proxies?format=surge')
    assert response.status_code == 400, response.get_data(as_text=True)
    assert 'Surge' in response.get_json()['message']


def test_mcp_manage_node_documents_stable_reference_contract():
    import json
    from backend.mcp_server import tools
    spec = next(t for t in tools.list_tools() if t['name'] == 'manage_node')
    schema = json.dumps(spec, ensure_ascii=False)
    assert 'dialer_ref' in schema
    assert '静态' in schema
    assert 'null' in schema


def test_builtin_looking_resource_id_does_not_bypass_cycle_validation(tmp_path):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('DIRECT', 'Relay'))
    client.post('/api/nodes', json=node('exit', dialer_ref={'type': 'node', 'id': 'DIRECT'}))
    before = repo.get_profile('default')
    response = client.put('/api/nodes/DIRECT', json=node('DIRECT', 'Relay', dialer_ref={'type': 'node', 'id': 'exit'}))
    assert response.status_code == 400
    assert repo.get_profile('default') == before


@pytest.mark.parametrize('storage', ['structured', 'uri'])
def test_multihop_overlay_includes_every_dependency(tmp_path, monkeypatch, storage):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('first'))
    client.post('/api/nodes', json=node('second', dialer_ref={'type': 'node', 'id': 'first'}))
    payload = node('exit', dialer_ref={'type': 'node', 'id': 'second'})
    if storage == 'uri':
        payload['proxy_string'] = 'http://fixture.invalid:80'
        monkeypatch.setattr('backend.utils.sub_store_client.convert_proxy_string', lambda s: {'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'legacy-missing'})
    client.post('/api/nodes', json=payload)
    client.post('/api/proxy-groups', json={'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['exit']})
    response = client.post('/api/generate/mihomo/preview', json={})
    assert response.status_code == 200
    proxies = {p['name']: p for p in yaml.safe_load(response.get_json()['content'])['proxies']}
    assert set(proxies) == {'first', 'second', 'exit'}
    assert proxies['exit']['dialer-proxy'] == 'second'
    assert proxies['second']['dialer-proxy'] == 'first'


def test_scope_isolation_and_stable_group_rename(tmp_path):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('relay'))
    group = {'id': 'g', 'name': 'RelayGroup', 'type': 'select', 'manual_nodes': ['relay']}
    client.post('/api/proxy-groups', json=group)
    client.post('/api/nodes', json=node('exit', dialer_ref={'type': 'group', 'id': 'g'}))
    repo.create_profile({'id': 'other', 'name': 'Other'})
    before = repo.get_profile('other')
    response = client.post('/api/nodes', headers={'X-ConfigFlow-Profile': 'other'}, json=node('foreign', dialer_ref={'type': 'node', 'id': 'relay'}))
    assert response.status_code == 400
    assert repo.get_profile('other') == before
    client.put('/api/proxy-groups/g', json={**group, 'name': 'RenamedGroup'})
    client.post('/api/proxy-groups', json={'id': 'e', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['exit']})
    response = client.post('/api/generate/mihomo/preview', json={})
    proxies = {p['name']: p for p in yaml.safe_load(response.get_json()['content'])['proxies']}
    assert proxies['exit']['dialer-proxy'] == 'RenamedGroup'
    assert repo.get_profile('other') == before


@pytest.mark.parametrize('failure', ['profile', 'system'])
def test_node_put_failure_rolls_back_dialer_and_preserves_id(tmp_path, monkeypatch, failure):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('relay'))
    client.post('/api/nodes', json=node('exit'))
    before = repo.get_profile('default')
    original = repo._write_json
    def injected(path, data):
        if path == (repo.system_file if failure == 'system' else repo._profile_path('default')):
            raise OSError('injected write failure')
        original(path, data)
    monkeypatch.setattr(repo, '_write_json', injected)
    response = client.put('/api/nodes/exit', json=node('exit', dialer_ref={'type': 'node', 'id': 'relay'}))
    assert response.status_code == 500
    assert repo.get_profile('default') == before


def test_same_profile_concurrent_cycle_race_commits_only_one_edge(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('a'))
    client.post('/api/nodes', json=node('b'))
    def update(pair):
        source, target = pair
        with app.test_client() as client:
            return client.put('/api/nodes/' + source, json=node(source, dialer_ref={'type': 'node', 'id': target})).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(update, [('a', 'b'), ('b', 'a')]))
    assert sorted(codes) == [200, 400]
    assert sum(n.get('dialer_ref') is not None for n in repo.get_profile('default')['nodes']) == 1


@pytest.mark.parametrize('operation', ['delete', 'disable'])
def test_disabled_source_still_protects_its_stable_reference(tmp_path, operation):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('relay'))
    client.post('/api/nodes', json=node('exit', dialer_ref={'type': 'node', 'id': 'relay'}))
    client.put('/api/nodes/exit', json={**node('exit', dialer_ref={'type': 'node', 'id': 'relay'}), 'enabled': False})
    before = repo.get_profile('default')
    response = client.delete('/api/nodes/relay') if operation == 'delete' else client.put('/api/nodes/relay', json={**node('relay'), 'enabled': False})
    assert response.status_code == 409
    assert repo.get_profile('default') == before


def test_disabling_source_dialer_is_allowed_and_preserves_metadata(tmp_path):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('relay'))
    payload = node('exit', dialer_ref={'type': 'node', 'id': 'relay'})
    client.post('/api/nodes', json=payload)
    response = client.put('/api/nodes/exit', json={**payload, 'enabled': False})
    assert response.status_code == 200
    assert repo.get_profile('default')['nodes'][1]['dialer_ref'] == payload['dialer_ref']


@pytest.mark.parametrize('kind', ['aggregation_relay', 'legacy_static'])
def test_dependency_closure_emits_relays_not_in_direct_selection(tmp_path, kind):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('relay'))
    client.post('/api/nodes', json=node('exit', dialer_ref={'type': 'node', 'id': 'relay'}))
    if kind == 'aggregation_relay':
        repo.update_profile_transaction('default', lambda p: p['subscription_aggregations'].append({'id': 'agg', 'name': 'Agg', 'nodes': ['relay', 'exit'], 'subscriptions': []}))
        group = {'id': 'g', 'name': 'Entry', 'type': 'select', 'aggregations': ['agg']}
    else:
        group = {'id': 'g', 'name': 'Entry', 'type': 'select', 'source': 'node', 'proxies': ['exit']}
    client.post('/api/proxy-groups', json=group)
    response = client.post('/api/generate/mihomo/preview', json={})
    assert response.status_code == 200, response.get_data(as_text=True)
    proxies = yaml.safe_load(response.get_json()['content'])['proxies']
    assert 'relay' in {p['name'] for p in proxies}


@pytest.mark.parametrize('format', ['mihomo', 'surge'])
def test_provider_rejects_untranslatable_dialer_before_write(tmp_path, monkeypatch, format):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('relay'))
    payload = node('exit', dialer_ref={'type': 'node', 'id': 'relay'}) if format == 'surge' else node('exit', proxy_string='http://fixture.invalid:80')
    client.post('/api/nodes', json=payload)
    agg = {'id': 'agg', 'name': 'Agg', 'nodes': ['exit'], 'subscriptions': []}
    repo.update_profile_transaction('default', lambda p: p['subscription_aggregations'].append(agg))
    client.post('/api/proxy-groups', json={'id': 'g', 'name': 'Entry', 'type': 'select', 'aggregations': ['agg']})
    monkeypatch.setattr('backend.utils.sub_store_client.convert_proxy_string', lambda s: {'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'gone'})
    output = repo.profile_dir('default') / 'providers' / 'agg.yaml'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('known good')
    response = client.get('/api/aggregations/agg/provider' + ('?format=surge' if format == 'surge' else ''))
    assert response.status_code == 400, response.get_data(as_text=True)
    assert output.read_text() == 'known good'


def test_aggregation_manual_dialer_overlay_and_main_dependency_closure(tmp_path):
    from pathlib import Path
    from backend.routes.aggregations import generate_aggregation_provider
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('relay'))
    client.post('/api/nodes', json=node('exit', dialer_ref={'type': 'node', 'id': 'relay'}))
    agg = {'id': 'agg', 'name': 'Agg', 'enabled': True, 'nodes': ['exit'], 'subscriptions': []}
    repo.update_profile_transaction('default', lambda p: p['subscription_aggregations'].append(agg))
    client.post('/api/proxy-groups', json={'id': 'g', 'name': 'Entry', 'type': 'select', 'aggregations': ['agg']})
    with app.test_request_context('/api/subscription-aggregations/agg/proxies'):
        result = generate_aggregation_provider(agg)
    proxies = yaml.safe_load(Path(result['file_path']).read_text())['proxies']
    assert proxies[0]['dialer-proxy'] == 'relay'
    response = client.post('/api/generate/mihomo/preview', json={})
    assert response.status_code == 200, response.get_data(as_text=True)
    config = yaml.safe_load(response.get_json()['content'])
    assert 'relay' in {p['name'] for p in config['proxies']}
    assert 'Agg' in config['proxy-providers']


@pytest.mark.parametrize('format', ['mihomo', 'surge'])
@pytest.mark.parametrize('failure', ['missing', 'cycle', 'reserved', 'duplicate', 'unsupported_surge', 'uri'])
def test_generation_rejects_invalid_topology_without_artifact_write(tmp_path, monkeypatch, format, failure):
    app, repo = make_app(tmp_path)
    nodes = [node('relay'), node('exit', dialer_ref={'type': 'node', 'id': 'relay'})]
    if failure == 'missing':
        nodes[1]['dialer_ref']['id'] = 'gone'
    elif failure == 'cycle':
        nodes[0]['dialer_ref'] = {'type': 'node', 'id': 'exit'}
    elif failure == 'reserved':
        nodes[0]['name'] = 'DIRECT'
    elif failure == 'duplicate':
        nodes[0]['name'] = 'exit'
    elif failure == 'uri':
        nodes[1].pop('dialer_ref')
        nodes[1]['proxy_string'] = 'http://fixture.invalid:80'
        monkeypatch.setattr('backend.utils.sub_store_client.convert_proxy_string', lambda s: {'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'gone'})
    elif format == 'mihomo':
        pytest.skip('Valid stable topology is supported by Mihomo')
    def seed(profile):
        profile['nodes'] = nodes
        profile['proxy_groups'] = [{'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['exit']}]
    repo.update_profile_transaction('default', seed)
    output = repo.generated_dir('default') / ('config.yaml' if format == 'mihomo' else 'config.conf')
    output.write_text('known good')
    response = app.test_client().post('/api/generate/' + format, json={})
    assert response.status_code == 400, response.get_data(as_text=True)
    assert output.read_text() == 'known good'


@pytest.mark.parametrize('storage', ['json', 'yaml', 'params'])
@pytest.mark.parametrize('operation', ['preserve', 'override', 'missing', 'cycle', 'rename', 'delete', 'disable'])
def test_raw_dialer_validation_and_precedence(tmp_path, storage, operation):
    import json
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('relay'))
    client.post('/api/nodes', json=node('other'))
    raw = {'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'missing' if operation == 'missing' else 'relay'}
    payload = node('exit')
    if storage == 'params':
        payload['params'] = {'dialer-proxy': raw['dialer-proxy']}
    else:
        payload['proxy_string'] = json.dumps(raw) if storage == 'json' else yaml.safe_dump(raw)
    if operation == 'override':
        payload['dialer_ref'] = {'type': 'node', 'id': 'other'}
    before = repo.get_profile('default')
    response = client.post('/api/nodes', json=payload)
    if operation == 'missing':
        assert response.status_code == 400
        assert repo.get_profile('default') == before
        return
    assert response.status_code == 200
    if operation in ('cycle', 'rename', 'delete', 'disable'):
        before = repo.get_profile('default')
        data = node('relay')
        if operation == 'cycle':
            data['dialer_ref'] = {'type': 'node', 'id': 'exit'}
        elif operation == 'rename':
            data['name'] = 'Renamed'
        elif operation == 'disable':
            data['enabled'] = False
        response = client.delete('/api/nodes/relay') if operation == 'delete' else client.put('/api/nodes/relay', json=data)
        assert response.status_code == (409 if operation in ('delete', 'disable') else 400)
        assert repo.get_profile('default') == before
    else:
        client.post('/api/proxy-groups', json={'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['exit']})
        response = client.post('/api/generate/mihomo/preview', json={})
        assert response.status_code == 200
        proxies = {p['name']: p for p in yaml.safe_load(response.get_json()['content'])['proxies']}
        target = 'other' if operation == 'override' else 'relay'
        assert proxies['exit']['dialer-proxy'] == target
        assert target in proxies


@pytest.mark.parametrize('ref', [False, '', [], {}, {'type': 'bad', 'id': 'relay'}, {'type': 'node', 'id': 3}, {'type': 'node', 'id': 'missing'}, {'type': 'node', 'id': 'exit'}, {'type': 'group', 'id': 'dynamic'}])
def test_invalid_stable_reference_rejected_without_write(tmp_path, ref):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('relay'))
    client.post('/api/proxy-groups', json={'id': 'dynamic', 'name': 'Dynamic', 'subscriptions': ['sub']})
    before = repo.get_profile('default')
    response = client.post('/api/nodes', json=node('exit', dialer_ref=ref))
    assert response.status_code == 400, response.get_data(as_text=True)
    assert repo.get_profile('default') == before


@pytest.mark.parametrize('operation', ['delete', 'disable', 'cycle', 'id'])
def test_node_mutations_protect_references_atomically(tmp_path, operation):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('relay'))
    client.post('/api/nodes', json=node('exit', dialer_ref={'type': 'node', 'id': 'relay'}))
    before = repo.get_profile('default')
    if operation == 'delete':
        response = client.delete('/api/nodes/relay')
    else:
        data = node('relay')
        if operation == 'disable':
            data['enabled'] = False
        elif operation == 'cycle':
            data['dialer_ref'] = {'type': 'node', 'id': 'exit'}
        else:
            data['id'] = 'spoof'
        response = client.put('/api/nodes/relay', json=data)
    assert response.status_code == (409 if operation in ('delete', 'disable') else 400)
    assert repo.get_profile('default') == before


@pytest.mark.parametrize('operation', ['node_group_cycle', 'membership_cycle', 'delete', 'disable', 'dynamic_nested'])
def test_static_group_dialer_validates_all_paths(tmp_path, operation):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    client.post('/api/nodes', json=node('relay'))
    client.post('/api/nodes', json=node('exit'))
    group = {'id': 'g', 'name': 'RelayPool', 'type': 'select', 'manual_nodes': ['relay', 'DIRECT']}
    if operation == 'node_group_cycle':
        group['manual_nodes'].append('exit')
    client.post('/api/proxy-groups', json=group)
    response = client.put('/api/nodes/exit', json=node('exit', dialer_ref={'type': 'group', 'id': 'g'}))
    if operation == 'node_group_cycle':
        assert response.status_code == 400
        return
    assert response.status_code == 200
    before = repo.get_profile('default')
    if operation == 'delete':
        response = client.delete('/api/proxy-groups/g')
    elif operation == 'disable':
        response = client.put('/api/proxy-groups/g', json={**group, 'enabled': False})
    elif operation == 'membership_cycle':
        response = client.put('/api/proxy-groups/g', json={**group, 'manual_nodes': ['exit', 'relay']})
    else:
        client.post('/api/proxy-groups', json={'id': 'd', 'name': 'Dynamic', 'subscriptions': ['sub']})
        before = repo.get_profile('default')
        response = client.put('/api/proxy-groups/g', json={**group, 'include_groups': ['d']})
    assert response.status_code == (409 if operation in ('delete', 'disable') else 400)
    assert repo.get_profile('default') == before
