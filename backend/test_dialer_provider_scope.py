"""Phase-one raw provider dialers cannot enter dynamic group membership."""
from unittest.mock import Mock
import pytest
import yaml
from backend.test_qa_integrity import make_app
from backend.test_dialer_delivery_snapshot import seed_profile, agent_manager
from backend.utils.subscription_cache import load_subscription_cache
from backend.utils.dialer_references import DialerReferenceError, validate_emitted


@pytest.mark.parametrize('direct', [True, False])
@pytest.mark.parametrize('consumer', ['preview', 'generate', 'download', 'provider', 'push'])
def test_provider_dynamic_group_rejected_before_side_effects(tmp_path, monkeypatch, direct, consumer):
    app, repo = make_app(tmp_path)
    seed_profile(repo, direct=direct)
    content = 'proxies: [{name: Remote, type: http, server: fixture.test, port: 80, dialer-proxy: Entry}]'
    monkeypatch.setattr(f'backend.routes.{"subscriptions" if direct else "aggregations"}.get_subscription_proxies_yaml', Mock(return_value=(content, 'rendered_yaml')))
    manager = agent_manager(monkeypatch)
    network = Mock(side_effect=AssertionError('No network'))
    monkeypatch.setattr('requests.get', network)
    before = load_subscription_cache('s', profile_id='default')
    profile = repo._profile_path('default').read_bytes()
    artifacts = [repo.write_profile_text('default', name, 'last-good') for name in ('config.yaml', 'providers/agg.yaml')]
    path = {'preview': '/api/generate/mihomo/preview', 'generate': '/api/generate/mihomo', 'download': '/api/config/mihomo', 'provider': '/api/subscriptions/s/proxies' if direct else '/api/aggregations/agg/provider', 'push': '/api/agents/a/push-config'}[consumer]
    client = app.test_client()
    response = client.get(path) if consumer in ('download', 'provider') else client.post(path, json={'restart': False})
    assert response.status_code == 400, response.get_data(as_text=True)
    manager.push_config_to_agent.assert_not_called()
    network.assert_not_called()
    assert load_subscription_cache('s', profile_id='default') == before
    assert repo._profile_path('default').read_bytes() == profile
    assert all(p.read_text() == 'last-good' for p in artifacts)


@pytest.mark.parametrize('field,value', [('use', ['Feed']), ('subscriptions', ['s']), ('aggregations', ['agg']), ('follow_group', 'other'), ('include-all', True), ('include_all', True)])
@pytest.mark.parametrize('nested', [False, True])
def test_raw_dialer_rejects_transitive_dynamic_membership(field, value, nested):
    groups = [{'name': 'Dynamic', 'proxies': ['DIRECT'], field: value}]
    if nested:
        groups.append({'name': 'Static', 'proxies': ['Dynamic']})
    main = {'proxies': [{'name': 'Remote', 'dialer-proxy': 'Static' if nested else 'Dynamic'}], 'proxy-groups': groups}
    with pytest.raises(DialerReferenceError, match='静态'):
        validate_emitted(main)


@pytest.mark.parametrize('target', ['DIRECT', 'Relay'])
def test_static_provider_dialer_and_builtin_allowed(target):
    validate_emitted({'proxies': [{'name': 'Remote', 'dialer-proxy': target}], 'proxy-groups': [{'name': 'Relay', 'proxies': ['DIRECT']}, {'name': 'UnrelatedDynamic', 'use': ['Feed']}]})


def test_no_chain_dynamic_group_legacy_allowed():
    validate_emitted({'proxies': [{'name': 'Remote'}], 'proxy-groups': [{'name': 'Entry', 'use': ['Feed']}]})
