"""Dialer regressions using shared raw resources and profile-local bindings."""
import pytest
import yaml
from backend.test_qa_integrity import make_app


def node(id, name=None, **extra):
    return dict(id=id, name=name or id, enabled=True, type='http', server='example.test', port=80, **extra)


def save_fixture(repo, shared, profile, profile_id='default'):
    """Persist raw catalog data and explicitly select it for this test profile."""
    repo.save_shared(shared)
    profile['resource_refs'] = {
        kind: [item['id'] for item in shared[kind]]
        for kind in ('nodes', 'subscriptions', 'subscription_aggregations')
    }
    repo.save_profile(profile_id, profile)


def add_subscription(repo, subscription):
    repo.update_shared_transaction(lambda shared: shared['subscriptions'].append(subscription))
    repo.update_profile_transaction('default', lambda profile: profile['resource_refs']['subscriptions'].append(subscription['id']))


def select_resources(client, *, nodes=(), subscriptions=(), aggregations=(), profile='default'):
    response = client.put(f'/api/profiles/{profile}/resources', json={
        'nodes': list(nodes), 'subscriptions': list(subscriptions),
        'subscription_aggregations': list(aggregations),
    })
    assert response.status_code == 200, response.get_data(as_text=True)


def bind_dialers(client, mapping, profile='default'):
    response = client.put(f'/api/profiles/{profile}/node-dialers', json=mapping)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response


def seed_nodes(repo, nodes, *, dialers=None, groups=None, aggregations=None, subscriptions=None):
    shared = repo.get_shared()
    shared['nodes'] = nodes
    if subscriptions is not None:
        shared['subscriptions'] = subscriptions
    if aggregations is not None:
        shared['subscription_aggregations'] = aggregations
    profile = repo.get_profile('default')
    profile['node_dialers'] = dialers or {}
    profile['proxy_groups'] = groups or []
    save_fixture(repo, shared, profile)


def test_stable_reference_emits_unused_relay_and_tracks_rename(tmp_path):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    seed_nodes(repo, [node('relay'), node('exit')])
    bind_dialers(client, {'exit': {'type': 'node', 'id': 'relay'}})
    assert client.post('/api/proxy-groups', json={'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['exit']}).status_code == 200
    assert client.put('/api/nodes/relay', json=node('relay', 'Renamed')).status_code == 200
    response = client.post('/api/generate/mihomo/preview', json={})
    assert response.status_code == 200, response.get_data(as_text=True)
    proxies = {p['name']: p for p in yaml.safe_load(response.get_json()['content'])['proxies']}
    assert proxies['exit']['dialer-proxy'] == 'Renamed'
    assert 'Renamed' in proxies
    assert all('dialer_ref' not in n for n in repo.get_shared()['nodes'])
    assert repo.get_compat_config('default')['nodes'][1]['dialer_ref'] == {'type': 'node', 'id': 'relay'}


def test_referenced_shared_node_delete_never_cleans_profile_or_aggregation(tmp_path, monkeypatch):
    from unittest.mock import Mock
    app, repo = make_app(tmp_path)
    seed_nodes(repo, [node('used')], aggregations=[{'id': 'agg', 'name': 'Agg', 'nodes': ['used'], 'subscriptions': []}],
               groups=[{'id': 'g', 'name': 'Group', 'type': 'select', 'manual_nodes': ['used', 'DIRECT'], 'aggregations': ['agg'], 'proxies_order': [{'type': 'node', 'id': 'used'}, {'type': 'node', 'id': 'DIRECT'}]}])
    before = repo.path.read_bytes()
    write = Mock(side_effect=AssertionError('Referenced deletion must not write'))
    monkeypatch.setattr(repo, '_write_json', write)
    response = app.test_client().delete('/api/nodes/used')
    assert response.status_code == 409
    assert repo.path.read_bytes() == before
    write.assert_not_called()
    assert repo.get_shared()['subscription_aggregations'][0]['nodes'] == ['used']
    assert repo.get_profile('default')['proxy_groups'][0]['manual_nodes'] == ['used', 'DIRECT']


@pytest.mark.parametrize('resource', ['nodes', 'proxy-groups'])
@pytest.mark.parametrize('conflict', ['name', 'builtin', 'id'])
def test_api_rejects_ambiguous_dialer_identity_without_write(tmp_path, resource, conflict):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    seed_nodes(repo, [node('relay'), node('exit')], dialers={'exit': {'type': 'node', 'id': 'relay'}},
               groups=[{'id': 'g', 'name': 'Group', 'type': 'select', 'manual_nodes': ['DIRECT']}])
    before = repo.path.read_bytes()
    data = node('exit') if resource == 'nodes' else {'id': 'g', 'name': 'Group', 'type': 'select', 'manual_nodes': ['DIRECT']}
    if conflict == 'name':
        data['name'] = 'relay'
    elif conflict == 'builtin':
        data['name'] = 'DIRECT'
    else:
        data['id'] = 'relay' if resource == 'nodes' else 'g'
    response = (client.post('/api/' + resource, json=data) if conflict == 'id' else
                client.put('/api/' + resource + ('/exit' if resource == 'nodes' else '/g'), json=data))
    assert response.status_code == (409 if resource == 'nodes' and conflict != 'id' else 400)
    assert repo.path.read_bytes() == before


@pytest.mark.parametrize('resource,key', [('nodes', 'nodes'), ('proxy-groups', 'groups')])
def test_legacy_reorder_cannot_bypass_dialer_validation(tmp_path, resource, key):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    group = {'id': 'g', 'name': 'Group', 'type': 'select', 'manual_nodes': ['relay']}
    seed_nodes(repo, [node('relay'), node('exit')], groups=[group])
    bind_dialers(client, {'exit': {'type': 'node', 'id': 'relay'} if resource == 'nodes' else {'type': 'group', 'id': 'g'}})
    before = repo.get_compat_config('default')
    replacement = [node('exit', params={'dialer-proxy': 'missing'})] if resource == 'nodes' else [{**group, 'manual_nodes': ['exit']}]
    response = client.post('/api/' + resource + '/reorder', json={key: replacement})
    assert response.status_code == 200
    after = repo.get_compat_config('default')
    assert {n['id']: n for n in after['nodes']} == {n['id']: n for n in before['nodes']}
    assert after['proxy_groups'] == before['proxy_groups']
    assert after['node_dialers'] == before['node_dialers']


def test_surge_policy_path_never_silently_drops_raw_dialer():
    from backend.converters.surge import convert_proxies_to_surge_text
    from backend.utils.strategy_references import StrategyReferenceError
    with pytest.raises(StrategyReferenceError, match='Surge'):
        convert_proxies_to_surge_text([{'name': 'Exit', 'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'Relay'}])


def test_subscription_surge_consumer_reports_unsupported_dialer_as_400(tmp_path):
    from backend.utils.subscription_cache import save_subscription_nodes
    app, repo = make_app(tmp_path)
    seed_nodes(repo, [], subscriptions=[{'id': 's', 'name': 'Cached', 'enabled': True, 'url': ''}])
    save_subscription_nodes('s', [node('cached', params={'dialer-proxy': 'Relay'})])
    response = app.test_client().get('/api/subscriptions/s/proxies?format=surge')
    assert response.status_code == 400, response.get_data(as_text=True)
    assert 'Surge' in response.get_json()['message']


@pytest.mark.parametrize('method', ['post', 'put'])
def test_shared_node_api_rejects_profile_binding_without_mutation(tmp_path, method):
    app, repo = make_app(tmp_path)
    seed_nodes(repo, [node('relay'), node('exit')])
    before = repo.path.read_bytes()
    response = getattr(app.test_client(), method)('/api/nodes' + ('/exit' if method == 'put' else ''),
        json=node('exit', dialer_ref={'type': 'node', 'id': 'relay'}))
    assert response.status_code == 400
    assert repo.path.read_bytes() == before


def test_builtin_looking_resource_id_does_not_bypass_cycle_validation(tmp_path):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    seed_nodes(repo, [node('DIRECT', 'Relay'), node('exit')])
    bind_dialers(client, {'exit': {'type': 'node', 'id': 'DIRECT'}})
    before = repo.path.read_bytes()
    response = client.put('/api/profiles/default/node-dialers', json={
        'exit': {'type': 'node', 'id': 'DIRECT'}, 'DIRECT': {'type': 'node', 'id': 'exit'}})
    assert response.status_code == 400
    assert repo.path.read_bytes() == before


@pytest.mark.parametrize('storage', ['structured', 'uri'])
def test_multihop_overlay_includes_every_dependency(tmp_path, monkeypatch, storage):
    app, repo = make_app(tmp_path)
    payload = node('exit')
    if storage == 'uri':
        payload['proxy_string'] = 'http://fixture.invalid:80'
        monkeypatch.setattr('backend.utils.sub_store_client.convert_proxy_string', lambda s: {'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'legacy-missing'})
    seed_nodes(repo, [node('first'), node('second'), payload])
    client = app.test_client()
    bind_dialers(client, {'second': {'type': 'node', 'id': 'first'}, 'exit': {'type': 'node', 'id': 'second'}})
    assert client.post('/api/proxy-groups', json={'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['exit']}).status_code == 200
    response = client.post('/api/generate/mihomo/preview', json={})
    assert response.status_code == 200
    proxies = {p['name']: p for p in yaml.safe_load(response.get_json()['content'])['proxies']}
    assert set(proxies) == {'first', 'second', 'exit'}
    assert proxies['exit']['dialer-proxy'] == 'second'
    assert proxies['second']['dialer-proxy'] == 'first'


def test_scope_isolation_and_stable_group_rename(tmp_path):
    app, repo = make_app(tmp_path)
    client = app.test_client()
    group = {'id': 'g', 'name': 'RelayGroup', 'type': 'select', 'manual_nodes': ['relay']}
    seed_nodes(repo, [node('relay'), node('exit')], groups=[group])
    bind_dialers(client, {'exit': {'type': 'group', 'id': 'g'}})
    repo.create_profile({'id': 'other', 'name': 'Other'})
    select_resources(client, nodes=['exit'], profile='other')
    before = repo.path.read_bytes()
    response = client.put('/api/profiles/other/node-dialers', json={'exit': {'type': 'node', 'id': 'relay'}})
    assert response.status_code == 400
    assert repo.path.read_bytes() == before
    other = repo.get_profile('other')
    assert client.put('/api/proxy-groups/g', json={**group, 'name': 'RenamedGroup'}).status_code == 200
    assert client.post('/api/proxy-groups', json={'id': 'e', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['exit']}).status_code == 200
    response = client.post('/api/generate/mihomo/preview', json={})
    proxies = {p['name']: p for p in yaml.safe_load(response.get_json()['content'])['proxies']}
    assert proxies['exit']['dialer-proxy'] == 'RenamedGroup'
    assert repo.get_profile('other') == other
    assert repo.get_compat_config('other')['nodes'][0].get('dialer_ref') is None


def test_node_dialers_write_failure_rolls_back_atomic_document(tmp_path, monkeypatch):
    app, repo = make_app(tmp_path)
    seed_nodes(repo, [node('relay'), node('exit')])
    before = repo.path.read_bytes()
    original = repo._write_json
    def injected(path, data):
        if path == repo.path:
            raise OSError('injected write failure')
        original(path, data)
    monkeypatch.setattr(repo, '_write_json', injected)
    response = app.test_client().put('/api/profiles/default/node-dialers', json={'exit': {'type': 'node', 'id': 'relay'}})
    assert response.status_code == 500
    assert repo.path.read_bytes() == before


def test_concurrent_whole_map_replacement_never_merges_into_cycle(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    app, repo = make_app(tmp_path)
    seed_nodes(repo, [node('a'), node('b')])
    def update(pair):
        source, target = pair
        with app.test_client() as client:
            return client.put('/api/profiles/default/node-dialers', json={source: {'type': 'node', 'id': target}}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(update, [('a', 'b'), ('b', 'a')]))
    assert codes == [200, 200]
    assert len(repo.get_profile('default')['node_dialers']) == 1
    before = repo.path.read_bytes()
    response = app.test_client().put('/api/profiles/default/node-dialers', json={
        'a': {'type': 'node', 'id': 'b'}, 'b': {'type': 'node', 'id': 'a'}})
    assert response.status_code == 400
    assert repo.path.read_bytes() == before


@pytest.mark.parametrize('operation', ['delete', 'disable'])
def test_disabled_source_still_protects_its_stable_reference(tmp_path, operation):
    app, repo = make_app(tmp_path)
    seed_nodes(repo, [node('relay'), {**node('exit'), 'enabled': False}], dialers={'exit': {'type': 'node', 'id': 'relay'}})
    before = repo.path.read_bytes()
    client = app.test_client()
    response = client.delete('/api/nodes/relay') if operation == 'delete' else client.put('/api/nodes/relay', json={**node('relay'), 'enabled': False})
    assert response.status_code == 409
    assert repo.path.read_bytes() == before


def test_disabling_selected_shared_source_preserves_binding_and_metadata(tmp_path):
    app, repo = make_app(tmp_path)
    seed_nodes(repo, [node('relay'), node('exit')], dialers={'exit': {'type': 'node', 'id': 'relay'}})
    before = repo.path.read_bytes()
    response = app.test_client().put('/api/nodes/exit', json={**node('exit'), 'enabled': False})
    assert response.status_code == 409
    assert repo.path.read_bytes() == before
    assert repo.get_profile('default')['node_dialers']['exit'] == {'type': 'node', 'id': 'relay'}


@pytest.mark.parametrize('kind', ['aggregation_relay', 'legacy_static'])
def test_dependency_closure_emits_relays_not_in_direct_selection(tmp_path, kind):
    app, repo = make_app(tmp_path)
    aggregations = [{'id': 'agg', 'name': 'Agg', 'nodes': ['relay', 'exit'], 'subscriptions': []}] if kind == 'aggregation_relay' else []
    group = {'id': 'g', 'name': 'Entry', 'type': 'select', 'aggregations': ['agg']} if aggregations else {'id': 'g', 'name': 'Entry', 'type': 'select', 'source': 'node', 'proxies': ['exit']}
    seed_nodes(repo, [node('relay'), node('exit')], aggregations=aggregations, groups=[group], dialers={'exit': {'type': 'node', 'id': 'relay'}})
    if aggregations:
        select_resources(app.test_client(), aggregations=['agg'])
    response = app.test_client().post('/api/generate/mihomo/preview', json={})
    assert response.status_code == 200, response.get_data(as_text=True)
    assert 'relay' in {p['name'] for p in yaml.safe_load(response.get_json()['content'])['proxies']}


@pytest.mark.parametrize('format', ['mihomo', 'surge'])
def test_provider_rejects_untranslatable_dialer_before_write(tmp_path, monkeypatch, format):
    app, repo = make_app(tmp_path)
    payload = node('exit') if format == 'surge' else node('exit', proxy_string='http://fixture.invalid:80')
    seed_nodes(repo, [node('relay'), payload], dialers={'exit': {'type': 'node', 'id': 'relay'}} if format == 'surge' else {},
               aggregations=[{'id': 'agg', 'name': 'Agg', 'nodes': ['exit'], 'subscriptions': []}],
               groups=[{'id': 'g', 'name': 'Entry', 'type': 'select', 'aggregations': ['agg']}])
    monkeypatch.setattr('backend.utils.sub_store_client.convert_proxy_string', lambda s: {'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'gone'})
    output = repo.write_profile_text('default', 'providers/agg.yaml', 'known good')
    response = app.test_client().get('/api/aggregations/agg/provider' + ('?format=surge' if format == 'surge' else ''))
    assert response.status_code == 400, response.get_data(as_text=True)
    assert output.read_text() == 'known good'


def test_aggregation_manual_dialer_overlay_and_main_dependency_closure(tmp_path):
    from pathlib import Path
    from backend.routes.aggregations import generate_aggregation_provider
    app, repo = make_app(tmp_path)
    agg = {'id': 'agg', 'name': 'Agg', 'enabled': True, 'nodes': ['exit'], 'subscriptions': []}
    seed_nodes(repo, [node('relay'), node('exit')], aggregations=[agg], dialers={'exit': {'type': 'node', 'id': 'relay'}},
               groups=[{'id': 'g', 'name': 'Entry', 'type': 'select', 'aggregations': ['agg']}])
    with app.test_request_context('/api/profiles/default/aggregations/agg/provider'):
        result = generate_aggregation_provider(agg)
    assert yaml.safe_load(Path(result['file_path']).read_text())['proxies'][0]['dialer-proxy'] == 'relay'
    response = app.test_client().post('/api/generate/mihomo/preview', json={})
    assert response.status_code == 200, response.get_data(as_text=True)
    config = yaml.safe_load(response.get_json()['content'])
    assert 'relay' in {p['name'] for p in config['proxies']}
    assert 'Agg' in config['proxy-providers']


@pytest.mark.parametrize('format', ['mihomo', 'surge'])
@pytest.mark.parametrize('failure', ['missing', 'cycle', 'reserved', 'duplicate', 'unsupported_surge', 'uri'])
def test_invalid_topology_rejected_without_artifact_write(tmp_path, monkeypatch, format, failure):
    app, repo = make_app(tmp_path)
    nodes = [node('relay'), node('exit')]
    if failure == 'uri':
        nodes[1]['proxy_string'] = 'http://fixture.invalid:80'
    seed_nodes(repo, nodes, groups=[{'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['exit']}])
    client = app.test_client()
    if failure != 'uri':
        bind_dialers(client, {'exit': {'type': 'node', 'id': 'relay'}})
    output = repo.write_generated('default', 'config.yaml' if format == 'mihomo' else 'config.conf', 'known good')
    before = repo.path.read_bytes()
    if failure in ('missing', 'cycle'):
        mapping = {'exit': {'type': 'node', 'id': 'gone' if failure == 'missing' else 'relay'}}
        if failure == 'cycle':
            mapping['relay'] = {'type': 'node', 'id': 'exit'}
        response = client.put('/api/profiles/default/node-dialers', json=mapping)
    elif failure in ('reserved', 'duplicate'):
        response = client.put('/api/nodes/relay', json=node('relay', 'DIRECT' if failure == 'reserved' else 'exit'))
    else:
        if failure == 'uri':
            monkeypatch.setattr('backend.utils.sub_store_client.convert_proxy_string', lambda s: {'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'gone'})
        elif format == 'mihomo':
            pytest.skip('Valid stable topology is supported by Mihomo')
        response = client.post('/api/generate/' + format, json={})
    assert response.status_code == (409 if failure in ('reserved', 'duplicate') else 400), response.get_data(as_text=True)
    assert repo.path.read_bytes() == before
    assert output.read_text() == 'known good'


@pytest.mark.parametrize('storage', ['json', 'yaml', 'params'])
@pytest.mark.parametrize('operation', ['preserve', 'override', 'missing', 'cycle', 'rename', 'delete', 'disable'])
def test_raw_dialer_validation_and_precedence(tmp_path, storage, operation):
    import json
    app, repo = make_app(tmp_path)
    seed_nodes(repo, [node('relay'), node('other'), node('exit')])
    client = app.test_client()
    raw = {'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'missing' if operation == 'missing' else 'relay'}
    payload = node('exit')
    if storage == 'params':
        payload['params'] = {'dialer-proxy': raw['dialer-proxy']}
    else:
        payload['proxy_string'] = json.dumps(raw) if storage == 'json' else yaml.safe_dump(raw)
    if operation == 'override':
        bind_dialers(client, {'exit': {'type': 'node', 'id': 'other'}})
    before = repo.path.read_bytes()
    response = client.put('/api/nodes/exit', json=payload)
    if operation == 'missing':
        assert response.status_code == 409
        assert repo.path.read_bytes() == before
        return
    assert response.status_code == 200
    if operation in ('cycle', 'rename', 'delete', 'disable'):
        before = repo.path.read_bytes()
        if operation == 'cycle':
            response = client.put('/api/profiles/default/node-dialers', json={'relay': {'type': 'node', 'id': 'exit'}})
        elif operation == 'delete':
            response = client.delete('/api/nodes/relay')
        else:
            data = node('relay', 'Renamed') if operation == 'rename' else {**node('relay'), 'enabled': False}
            response = client.put('/api/nodes/relay', json=data)
        assert response.status_code == (400 if operation == 'cycle' else 409)
        assert repo.path.read_bytes() == before
    else:
        assert client.post('/api/proxy-groups', json={'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['exit']}).status_code == 200
        response = client.post('/api/generate/mihomo/preview', json={})
        assert response.status_code == 200
        proxies = {p['name']: p for p in yaml.safe_load(response.get_json()['content'])['proxies']}
        target = 'other' if operation == 'override' else 'relay'
        assert proxies['exit']['dialer-proxy'] == target
        assert target in proxies


@pytest.mark.parametrize('ref', [False, '', [], {}, {'type': 'bad', 'id': 'relay'}, {'type': 'node', 'id': 3}, {'type': 'node', 'id': 'missing'}, {'type': 'node', 'id': 'exit'}, {'type': 'group', 'id': 'dynamic'}])
def test_invalid_stable_reference_rejected_without_write(tmp_path, ref):
    app, repo = make_app(tmp_path)
    seed_nodes(repo, [node('relay'), node('exit')], subscriptions=[{'id': 'sub', 'name': 'Feed', 'url': 'https://fixture.invalid/feed'}],
               groups=[{'id': 'dynamic', 'name': 'Dynamic', 'subscriptions': ['sub']}])
    before = repo.path.read_bytes()
    response = app.test_client().put('/api/profiles/default/node-dialers', json={'exit': ref})
    assert response.status_code == 400, response.get_data(as_text=True)
    assert repo.path.read_bytes() == before


@pytest.mark.parametrize('operation', ['delete', 'disable', 'cycle', 'id'])
def test_node_mutations_protect_references_atomically(tmp_path, operation):
    app, repo = make_app(tmp_path)
    seed_nodes(repo, [node('relay'), node('exit')], dialers={'exit': {'type': 'node', 'id': 'relay'}})
    before = repo.path.read_bytes()
    client = app.test_client()
    if operation == 'delete':
        response = client.delete('/api/nodes/relay')
    elif operation == 'cycle':
        response = client.put('/api/profiles/default/node-dialers', json={'exit': {'type': 'node', 'id': 'relay'}, 'relay': {'type': 'node', 'id': 'exit'}})
    else:
        data = {**node('relay'), 'enabled': False} if operation == 'disable' else node('spoof')
        response = client.put('/api/nodes/relay', json=data)
    assert response.status_code == (409 if operation in ('delete', 'disable') else 400)
    assert repo.path.read_bytes() == before


@pytest.mark.parametrize('operation', ['node_group_cycle', 'membership_cycle', 'delete', 'disable', 'dynamic_nested'])
def test_static_group_dialer_validates_all_paths(tmp_path, operation):
    app, repo = make_app(tmp_path)
    group = {'id': 'g', 'name': 'RelayPool', 'type': 'select', 'manual_nodes': ['relay', 'DIRECT']}
    if operation == 'node_group_cycle':
        group['manual_nodes'].append('exit')
    seed_nodes(repo, [node('relay'), node('exit')], groups=[group], subscriptions=[{'id': 'sub', 'name': 'Feed', 'url': 'https://fixture.invalid/feed'}])
    client = app.test_client()
    before = repo.path.read_bytes()
    response = client.put('/api/profiles/default/node-dialers', json={'exit': {'type': 'group', 'id': 'g'}})
    if operation == 'node_group_cycle':
        assert response.status_code == 400
        assert repo.path.read_bytes() == before
        return
    assert response.status_code == 200
    before = repo.path.read_bytes()
    if operation == 'delete':
        response = client.delete('/api/proxy-groups/g')
    elif operation == 'disable':
        response = client.put('/api/proxy-groups/g', json={**group, 'enabled': False})
    elif operation == 'membership_cycle':
        response = client.put('/api/proxy-groups/g', json={**group, 'manual_nodes': ['exit', 'relay']})
    else:
        assert client.post('/api/proxy-groups', json={'id': 'd', 'name': 'Dynamic', 'subscriptions': ['sub']}).status_code == 200
        before = repo.path.read_bytes()
        response = client.put('/api/proxy-groups/g', json={**group, 'include_groups': ['d']})
    assert response.status_code == (409 if operation in ('delete', 'disable') else 400)
    assert repo.path.read_bytes() == before
