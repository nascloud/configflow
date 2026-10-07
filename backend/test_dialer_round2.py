"""Fail-closed regressions from the second independent dialer review."""
from unittest.mock import Mock
import pytest
import yaml
from backend.test_qa_integrity import make_app
from backend.test_dialer_proxy import node


@pytest.mark.parametrize('path', ['/api/generate/mihomo/preview', '/api/generate/mihomo', '/api/config/mihomo', '/api/agents/a/push-config'])
def test_converted_main_chain_preflights_subscription_only_provider(tmp_path, monkeypatch, path):
    app, repo = make_app(tmp_path)
    def seed(p):
        p['nodes'] = [node('relay'), node('exit', proxy_string='http://fixture.invalid:80')]
        p['subscriptions'] = [{'id': 's', 'name': 'S', 'enabled': True, 'url': 'https://fixture.invalid/feed'}]
        p['subscription_aggregations'] = [{'id': 'agg', 'name': 'Agg', 'nodes': [], 'subscriptions': ['s']}]
        p['proxy_groups'] = [{'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['relay', 'exit'], 'aggregations': ['agg']}]
    repo.update_profile_transaction('default', seed)
    monkeypatch.setattr('backend.utils.sub_store_client.convert_proxy_string', lambda s: {'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'relay'})
    fetch = Mock(return_value=('proxies: [{name: Bad, type: http, server: example.test, port: 80, dialer-proxy: gone}]', 'rendered_yaml'))
    monkeypatch.setattr('backend.routes.aggregations.get_subscription_proxies_yaml', fetch)
    cache_write = Mock()
    monkeypatch.setattr('backend.routes.aggregations.save_subscription_nodes', cache_write)
    manager = Mock()
    manager.get_agent_by_id.return_value = {'id': 'a', 'service_type': 'mihomo', 'profile_id': 'default'}
    monkeypatch.setattr('backend.routes.agents.get_agent_manager', lambda: manager)
    outbound = Mock(side_effect=AssertionError('invalid graph must precede outbound agent requests'))
    monkeypatch.setattr('requests.get', outbound)
    output = repo.generated_dir('default') / 'config.yaml'
    output.write_text('known good')
    before = repo._profile_path('default').read_bytes()
    client = app.test_client()
    response = client.get(path) if path.startswith('/api/config') else client.post(path, json={'restart': False})
    assert response.status_code == 400, response.get_data(as_text=True)
    assert fetch.call_count == 1
    cache_write.assert_not_called()
    outbound.assert_not_called()
    manager.push_config_to_agent.assert_not_called()
    assert output.read_text() == 'known good'
    assert repo._profile_path('default').read_bytes() == before


@pytest.mark.parametrize('provider_chain', [True, False])
@pytest.mark.parametrize('path', ['/api/aggregations/agg/provider', '/api/generate/mihomo'])
def test_duplicate_emitted_provider_dialers_rejected_before_commit(tmp_path, monkeypatch, path, provider_chain):
    app, repo = make_app(tmp_path)
    def seed(p):
        p['nodes'] = [node('exit', params={'dialer-proxy': 'DIRECT'})]
        p['subscriptions'] = [{'id': 's', 'name': 'S', 'enabled': True, 'url': 'https://fixture.invalid/feed'}]
        p['subscription_aggregations'] = [{'id': 'agg', 'name': 'Agg', 'nodes': [], 'subscriptions': ['s']}]
        p['proxy_groups'] = [{'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['exit'], 'aggregations': ['agg']}]
    repo.update_profile_transaction('default', seed)
    text = 'proxies: [{name: Dup, type: http, server: example.test, port: 80, dialer-proxy: DIRECT}, {name: Dup, type: http, server: other.test, port: 81, dialer-proxy: DIRECT}]'
    if not provider_chain:
        text = text.replace(', dialer-proxy: DIRECT', '')
    monkeypatch.setattr('backend.routes.aggregations.get_subscription_proxies_yaml', Mock(return_value=(text, 'rendered_yaml')))
    cache_write = Mock()
    monkeypatch.setattr('backend.routes.aggregations.save_subscription_nodes', cache_write)
    provider = repo.profile_dir('default') / 'providers' / 'agg.yaml'
    provider.parent.mkdir(exist_ok=True)
    provider.write_text('known good provider')
    output = repo.generated_dir('default') / 'config.yaml'
    output.write_text('known good')
    response = app.test_client().get(path) if '/provider' in path else app.test_client().post(path, json={})
    assert response.status_code == 400
    cache_write.assert_not_called()
    assert provider.read_text() == 'known good provider'
    assert output.read_text() == 'known good'


@pytest.mark.parametrize('source', ['rendered_yaml', 'sub_store', 'direct_url_fallback'])
def test_surge_remote_dialer_rejected_before_cache_write(tmp_path, monkeypatch, source):
    from backend.utils.subscription_cache import save_subscription_nodes, load_subscription_cache
    app, repo = make_app(tmp_path)
    repo.update_profile_transaction('default', lambda p: p['subscriptions'].append({'id': 's', 'name': 'S', 'enabled': True, 'url': 'https://fixture.invalid/feed'}))
    save_subscription_nodes('s', [node('old')], profile_id='default')
    before = load_subscription_cache('s', profile_id='default')
    text = 'proxies: [{name: Exit, type: http, server: example.test, port: 80, dialer-proxy: DIRECT}]'
    monkeypatch.setattr('backend.routes.subscriptions.get_subscription_proxies_yaml', Mock(return_value=(text, source)))
    response = app.test_client().get('/api/subscriptions/s/proxies?format=surge')
    assert response.status_code == 400
    assert load_subscription_cache('s', profile_id='default') == before


@pytest.mark.parametrize('reverse_order', [False, True])
def test_provider_discovered_chain_preflights_other_used_providers(tmp_path, monkeypatch, reverse_order):
    app, repo = make_app(tmp_path)
    def seed(p):
        p['nodes'] = [node('relay'), node('exit', proxy_string='http://fixture.invalid:80')]
        p['subscriptions'] = [{'id': 's', 'name': 'S', 'enabled': True, 'url': 'https://fixture.invalid/feed'}]
        p['subscription_aggregations'] = [{'id': 'first', 'name': 'First', 'nodes': ['exit'], 'subscriptions': []}, {'id': 'second', 'name': 'Second', 'nodes': [], 'subscriptions': ['s']}]
        p['proxy_groups'] = [{'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['relay'], 'aggregations': ['first', 'second']}]
        if reverse_order:
            p['subscription_aggregations'].reverse()
    repo.update_profile_transaction('default', seed)
    monkeypatch.setattr('backend.utils.sub_store_client.convert_proxy_string', lambda s: {'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'relay'})
    fetch = Mock(return_value=('proxies: [{name: Bad, type: http, server: example.test, port: 80, dialer-proxy: gone}]', 'rendered_yaml'))
    monkeypatch.setattr('backend.routes.aggregations.get_subscription_proxies_yaml', fetch)
    response = app.test_client().post('/api/generate/mihomo/preview', json={})
    assert response.status_code == 400
    assert fetch.call_count == 1


def test_direct_subscription_provider_is_preflighted_for_converted_main_chain(tmp_path, monkeypatch):
    app, repo = make_app(tmp_path)
    def seed(p):
        p['nodes'] = [node('relay'), node('exit', proxy_string='http://fixture.invalid:80')]
        p['subscriptions'] = [{'id': 's', 'name': 'S', 'enabled': True, 'url': 'https://fixture.invalid/feed'}]
        p['proxy_groups'] = [{'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['relay', 'exit'], 'subscriptions': ['s']}]
    repo.update_profile_transaction('default', seed)
    monkeypatch.setattr('backend.utils.sub_store_client.convert_proxy_string', lambda s: {'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'relay'})
    monkeypatch.setattr('backend.routes.subscriptions.get_subscription_proxies_yaml', Mock(return_value=('proxies: [{name: Bad, type: http, server: example.test, port: 80, dialer-proxy: gone}]', 'rendered_yaml')))
    response = app.test_client().post('/api/generate/mihomo/preview', json={})
    assert response.status_code == 400


@pytest.mark.parametrize('path', ['/api/generate/mihomo/preview', '/api/generate/mihomo', '/api/config/mihomo', '/api/aggregations/agg/provider'])
def test_good_converted_main_and_raw_provider_graph_still_exports(tmp_path, monkeypatch, path):
    app, repo = make_app(tmp_path)
    def seed(p):
        p['nodes'] = [node('relay'), node('exit', proxy_string='http://fixture.invalid:80')]
        p['subscriptions'] = [{'id': 's', 'name': 'S', 'enabled': True, 'url': 'https://fixture.invalid/feed'}]
        p['subscription_aggregations'] = [{'id': 'agg', 'name': 'Agg', 'nodes': [], 'subscriptions': ['s']}]
        p['proxy_groups'] = [{'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['relay', 'exit'], 'aggregations': ['agg']}]
    repo.update_profile_transaction('default', seed)
    monkeypatch.setattr('backend.utils.sub_store_client.convert_proxy_string', lambda s: {'type': 'http', 'server': 'example.test', 'port': 80, 'dialer-proxy': 'relay'})
    monkeypatch.setattr('backend.routes.aggregations.get_subscription_proxies_yaml', Mock(return_value=('proxies: [{name: Good, type: http, server: example.test, port: 80, dialer-proxy: relay}]', 'rendered_yaml')))
    client = app.test_client()
    response = client.get(path) if path.startswith('/api/config') or '/provider' in path else client.post(path, json={})
    assert response.status_code == 200, response.get_data(as_text=True)
    content = response.get_json()['content'] if 'preview' in path else (repo.generated_dir('default') / 'config.yaml').read_text() if path == '/api/generate/mihomo' else response.get_data(as_text=True)
    proxies = yaml.safe_load(content)['proxies']
    assert next(p for p in proxies if p['name'] == ('Good' if '/provider' in path else 'exit'))['dialer-proxy'] == 'relay'


def test_subscription_name_priority_over_manual_node_is_preserved(tmp_path, monkeypatch):
    app, repo = make_app(tmp_path)
    def seed(p):
        p['nodes'] = [node('relay'), node('exit', dialer_ref={'type': 'node', 'id': 'relay'})]
        p['subscriptions'] = [{'id': 's', 'name': 'S', 'enabled': True, 'url': 'https://fixture.invalid/feed'}]
        p['subscription_aggregations'] = [{'id': 'agg', 'name': 'Agg', 'nodes': ['exit'], 'subscriptions': ['s']}]
        p['proxy_groups'] = [{'id': 'g', 'name': 'Entry', 'type': 'select', 'aggregations': ['agg']}]
    repo.update_profile_transaction('default', seed)
    monkeypatch.setattr('backend.routes.aggregations.get_subscription_proxies_yaml', Mock(return_value=('proxies: [{name: exit, type: http, server: other.test, port: 81}]', 'rendered_yaml')))
    response = app.test_client().get('/api/aggregations/agg/provider')
    assert response.status_code == 200
    assert yaml.safe_load(response.data)['proxies'] == [{'name': 'exit', 'type': 'http', 'server': 'other.test', 'port': 81}]


def test_disabled_managed_source_keeps_manual_only_closure_contract():
    from backend.utils.dialer_references import validate_dialers, DialerReferenceError
    profile = {'nodes': [{**node('source', dialer_ref={'type': 'node', 'id': 'relay'}), 'enabled': False}, node('relay', subscription_id='s')]}
    with pytest.raises(DialerReferenceError, match='手动'):
        validate_dialers(profile)


def deep_nodes(cycle=False):
    return [node(str(i), **({'dialer_ref': {'type': 'node', 'id': str((i + 1) % 1100)}}
                           if i < 1099 or cycle else {})) for i in range(1100)]


@pytest.mark.parametrize('cycle', [False, True])
def test_deep_graph_validation_and_closure_are_stack_safe(cycle):
    from backend.utils.dialer_references import validate_dialers, dependency_node_ids, validate_emitted, DialerReferenceError
    profile = {'nodes': deep_nodes(cycle)}
    if cycle:
        with pytest.raises(DialerReferenceError, match='循环'):
            validate_dialers(profile)
    else:
        validate_dialers(profile)
        assert len(dependency_node_ids(profile, ['0'])) == 1100
    emitted = {'proxies': [{'name': n['name'], **({'dialer-proxy': n['dialer_ref']['id']} if n.get('dialer_ref') else {})} for n in profile['nodes']]}
    if cycle:
        with pytest.raises(DialerReferenceError, match='循环'):
            validate_emitted(emitted)
    else:
        validate_emitted(emitted)


@pytest.mark.parametrize('cycle', [False, True])
@pytest.mark.parametrize('path', ['/api/generate/mihomo/preview', '/api/nodes'])
def test_deep_graph_api_never_returns_500(tmp_path, monkeypatch, cycle, path):
    from backend.converters import mihomo
    app, repo = make_app(tmp_path)
    repo.update_profile_transaction('default', lambda p: p.update(nodes=deep_nodes(cycle), proxy_groups=[{'id': 'g', 'name': 'Entry', 'type': 'select', 'manual_nodes': ['0']}]))
    before = repo._profile_path('default').read_bytes()
    convert = Mock(wraps=mihomo.convert_node_to_mihomo)
    monkeypatch.setattr(mihomo, 'convert_node_to_mihomo', convert)
    response = app.test_client().post(path, json={} if 'preview' in path else node('new'))
    assert response.status_code == (400 if cycle else 200), response.get_data(as_text=True)
    if cycle or 'preview' in path:
        assert repo._profile_path('default').read_bytes() == before
    if not cycle and 'preview' in path:
        assert len(yaml.safe_load(response.get_json()['content'])['proxies']) == 1100
        assert convert.call_count == 1100
