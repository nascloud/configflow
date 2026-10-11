"""Latest independent-review regressions; synthetic resources only."""
from copy import deepcopy
import pytest
from backend.test_health_review_re1 import env


def write(client, transport, action, data=None, ident='a'):
    if transport == 'api':
        response = (client.delete(f'/api/proxy-groups/{ident}') if action == 'delete' else
                    client.post('/api/proxy-groups', json=data) if action == 'create' else
                    client.put(f'/api/proxy-groups/{ident}', json=data))
        assert response.status_code != 500
        return response.status_code == 200
    response = client.post('/mcp', headers={'Authorization': 'Bearer re1-fixture-token'}, json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
            'name': 'manage_proxy_group', 'arguments': {'action': action, 'id': ident, 'data': data or {}}}})
    return not response.json['result']['isError']


@pytest.mark.parametrize('transport', ['api', 'mcp'])
def test_source_transition_cannot_break_existing_follower(env, transport):
    from backend.common.config_repository import ProfileRepository
    repo, client, path = env
    for group in [
        {'id': 'a', 'name': 'A', 'type': 'select', 'manual_nodes': ['DIRECT']},
        {'id': 'b', 'name': 'B', 'type': 'select', 'manual_nodes': ['REJECT']},
        {'id': 'f', 'name': 'F', 'type': 'select', 'follow_group': 'a'},
    ]:
        assert write(client, transport, 'create', group)
    before = deepcopy(repo.get_compat_config('default')['proxy_groups'])
    assert not write(client, transport, 'update', {'follow_group': 'b', 'manual_nodes': []})
    assert ProfileRepository(path).get_compat_config('default')['proxy_groups'] == before


@pytest.mark.parametrize('transport', ['api', 'mcp'])
@pytest.mark.parametrize('action', ['create', 'update'])
@pytest.mark.parametrize('field', ['regex', 'aggregation_regex'])
@pytest.mark.parametrize('value', [None, '', '  '])
def test_nullable_filters_remain_valid(env, transport, action, field, value):
    repo, client, path = env
    group = {'id': 'a', 'name': 'A', 'type': 'select', 'subscriptions': ['s']}
    if action == 'update':
        assert write(client, transport, 'create', group)
    assert write(client, transport, action, {**group, field: value})
    from backend.converters.mihomo import generate_mihomo_config
    import yaml
    emitted = yaml.safe_load(generate_mihomo_config(repo.get_compat_config('default'), preflight_providers=False))['proxy-groups'][0]
    assert emitted['use'] == ['S']
    assert 'filter' not in emitted


@pytest.mark.parametrize('transport', ['api', 'mcp'])
@pytest.mark.parametrize('action', ['create', 'update'])
@pytest.mark.parametrize('patch', [{'regex': 3}, {'aggregation_regex': False}, {'regex': []}, {'aggregation_regex': {}}])
def test_nonstring_filter_writes_are_controlled_and_atomic(env, transport, action, patch):
    from backend.common.config_repository import ProfileRepository
    repo, client, path = env
    group = {'id': 'a', 'name': 'A', 'type': 'select', 'subscriptions': ['s']}
    if action == 'update':
        assert write(client, transport, 'create', group)
    before = deepcopy(repo.get_compat_config('default')['proxy_groups'])
    assert not write(client, transport, action, {**group, **patch})
    assert ProfileRepository(path).get_compat_config('default')['proxy_groups'] == before


@pytest.mark.parametrize('follow', [False, True])
@pytest.mark.parametrize('value', [None, '', False, 0])
def test_legacy_falsy_filter_generation(env, follow, value):
    import yaml
    from backend.converters.mihomo import generate_mihomo_config
    repo, client, path = env
    groups = [{'id': 'a', 'name': 'A', 'type': 'select', 'subscriptions': ['s'], 'regex': value, 'aggregation_regex': value}]
    if follow:
        groups.append({'id': 'f', 'name': 'F', 'type': 'select', 'follow_group': 'a'})
    repo.update_profile_transaction('default', lambda p: p.update(proxy_groups=groups))
    emitted = yaml.safe_load(generate_mihomo_config(repo.get_compat_config('default'), preflight_providers=False))['proxy-groups'][-1]
    assert emitted['use'] == ['S']
    assert 'filter' not in emitted


@pytest.mark.parametrize('transport', ['api', 'mcp'])
@pytest.mark.parametrize('operation,patch', [
    ('update', {'type': 'chain', 'chain': []}),
    ('update', {'enabled': False}),
    ('update', {'manual_nodes': []}),
    ('update', {'proxies_order': [{'type': 'subscription', 'id': 's'}]}),
    ('delete', {}),
])
def test_other_target_changes_cannot_break_followers(env, transport, operation, patch):
    from backend.common.config_repository import ProfileRepository
    repo, client, path = env
    assert write(client, transport, 'create', {'id': 'a', 'name': 'A', 'type': 'select', 'manual_nodes': ['DIRECT']})
    for ident in ('f', 'f2'):
        assert write(client, transport, 'create', {'id': ident, 'name': ident, 'type': 'select', 'follow_group': 'a'})
    before = deepcopy(repo.get_compat_config('default')['proxy_groups'])
    assert not write(client, transport, operation, patch)
    assert ProfileRepository(path).get_compat_config('default')['proxy_groups'] == before


@pytest.mark.parametrize('transport', ['api', 'mcp'])
def test_valid_target_source_update_reaches_all_followers(env, transport):
    import yaml
    from backend.converters.mihomo import generate_mihomo_config
    repo, client, path = env
    assert write(client, transport, 'create', {'id': 'a', 'name': 'A', 'type': 'select', 'manual_nodes': ['DIRECT']})
    for ident in ('f', 'f2'):
        assert write(client, transport, 'create', {'id': ident, 'name': ident, 'type': 'select', 'follow_group': 'a'})
    assert write(client, transport, 'update', {'name': 'Renamed', 'manual_nodes': [], 'subscriptions': ['s'], 'regex': None})
    cfg = repo.get_compat_config('default')
    emitted = yaml.safe_load(generate_mihomo_config(cfg, preflight_providers=False))['proxy-groups']
    assert len(emitted) == 3
    assert all(item.get('use') == ['S'] and 'filter' not in item for item in emitted)


@pytest.mark.parametrize('transport', ['api', 'mcp'])
def test_unrelated_edit_of_legacy_falsy_filter_does_not_add_strict_contract(env, transport):
    repo, client, path = env
    repo.update_profile_transaction('default', lambda p: p['proxy_groups'].append({
        'id': 'a', 'name': 'A', 'type': 'select', 'subscriptions': ['s'], 'regex': False, 'aggregation_regex': 0}))
    assert write(client, transport, 'update', {'name': 'Renamed'})
    actual = repo.get_compat_config('default')['proxy_groups'][0]
    assert actual['regex'] is False and actual['aggregation_regex'] == 0


def test_explicit_legacy_nonstring_filter_assignment_is_still_invalid(env):
    repo, client, path = env
    repo.update_profile_transaction('default', lambda p: p['proxy_groups'].append({
        'id': 'a', 'name': 'A', 'type': 'select', 'subscriptions': ['s'], 'regex': False}))
    before = deepcopy(repo.get_compat_config('default')['proxy_groups'])
    assert not write(client, 'api', 'update', {'regex': False})
    assert repo.get_compat_config('default')['proxy_groups'] == before
