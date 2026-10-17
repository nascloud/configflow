from copy import deepcopy

import pytest
import yaml

from backend.common.oxidns_device_policy import (DIRECT_TAG, HOOK, POLICY_TAG,
    apply_policies, normalize_policies, read_policies)
from backend.common.oxidns_editor import prepare_editor
from backend.common.oxidns_settings import OxidnsSettingsError
from backend.common.oxidns_common import apply_common_settings, read_common_settings
from backend.converters.oxidns import generate_oxidns_config
from backend.common.agent_manager import get_agent_manager
from backend.test_oxidns_integration import oxidns_app

ROWS = [{'ip': '192.168.50.50', 'name': '电脑', 'mode': 'smart'},
        {'ip': '192.168.50.51', 'name': '电视', 'mode': 'direct'}]


def plugin(document, tag):
    return next(p for p in document['plugins'] if p['tag'] == tag)


def test_policy_roundtrip_preserves_native_content_and_removes_only_owned_rules():
    config = {'oxidns': {'query_recorder': {'enabled': True}, 'custom_hosts': 'example.test 1.2.3.4'}}
    settings = prepare_editor(config['oxidns'], config)
    before = yaml.safe_load(settings['native_config'])
    before['custom_extension'] = {'keep': 'untouched'}
    settings['native_config'] = yaml.safe_dump(before)
    settings['additional_files'] = [{'path': 'custom.txt', 'content': 'keep'}]
    updated = apply_policies(settings, config, ROWS)
    document = yaml.safe_load(updated['native_config'])
    main = plugin(document, 'sequence_main')['args']
    assert main.index(HOOK) > main.index({'exec': '$query_recorder'})
    assert main.index(HOOK) > main.index({'exec': '$custom_hosts'})
    assert main.index(HOOK) < len(main) - 1
    assert plugin(document, POLICY_TAG)['args'][0]['matches'] == ['client_ip 192.168.50.51']
    assert plugin(document, DIRECT_TAG)['args'][0] == {'exec': '$forward_local'}
    assert read_policies(updated, config)['editable']
    assert updated['additional_files'] == settings['additional_files']
    cleared = apply_policies(updated, config, [])
    assert yaml.safe_load(cleared['native_config']) == before
    assert cleared['device_policies'] == []
    assert read_policies(cleared, config)['editable']


@pytest.mark.parametrize('rows', [None, {}, [{'ip': '192.168.0.1/24', 'mode': 'direct'}],
    [{'ip': '1.2.3.4\nexec reject', 'mode': 'direct'}], [{'ip': '::', 'mode': 'direct'}],
    [{'ip': '1.2.3.4', 'mode': 'proxy'}], [{'ip': '1.2.3.4', 'mode': 'direct', 'name': 1}],
    [{'ip': '2001:db8::1', 'mode': 'smart'}, {'ip': '2001:0db8::1', 'mode': 'direct'}],
    [{'ip': '192.168.0.1', 'mode': 'smart'}, {'ip': '::ffff:192.168.0.1', 'mode': 'direct'}]])
def test_reject_invalid_and_duplicate_devices(rows):
    with pytest.raises(OxidnsSettingsError):
        normalize_policies(rows)


def test_template_and_other_editors_keep_policies():
    config = {'oxidns': {'device_policies': ROWS}}
    document = yaml.safe_load(generate_oxidns_config(config))
    assert HOOK in plugin(document, 'sequence_main')['args']
    settings = prepare_editor(config['oxidns'], config)
    state = read_common_settings(settings, config)
    assert state['capabilities']['custom_matches']['editable']
    assert state['values']['custom_matches'] == []
    result = apply_common_settings(settings, {'default_forward': 'forward_local', 'query_recorder.enabled': True}, config)
    assert read_policies(result['settings'], config)['editable']
    changed = yaml.safe_load(result['settings']['native_config'])
    main = plugin(changed, 'sequence_main')['args']
    assert main.index({'exec': '$query_recorder'}) < main.index(HOOK)


def test_manual_edits_are_detected_without_overwriting():
    settings = apply_policies({}, {}, ROWS)
    document = yaml.safe_load(settings['native_config'])
    plugin(document, DIRECT_TAG)['args'][0] = {'exec': '$forward_remote'}
    settings['native_config'] = yaml.safe_dump(document)
    before = deepcopy(settings)
    assert not read_policies(settings, {})['editable']
    with pytest.raises(OxidnsSettingsError):
        apply_policies(settings, {}, [])
    assert settings == before


def test_api_binding_shared_scope_and_stale_write(oxidns_app):
    app, repository = oxidns_app
    manager = get_agent_manager()
    aid = manager.register_agent({'name': 'DNS', 'host': '127.0.0.1', 'port': 18000,
                                  'service_type': 'oxidns', 'profile_id': 'office'})['id']
    peer = manager.register_agent({'name': 'DNS2', 'host': '127.0.0.2', 'port': 18000,
                                   'service_type': 'oxidns', 'profile_id': 'office'})['id']
    client = app.test_client()
    url = f'/api/agents/{aid}/oxidns/device-policies?profile=default'
    state = client.get(url).get_json()
    assert state['profile_id'] == 'office'
    assert {a['id'] for a in state['agents']} == {aid, peer}
    default = deepcopy(repository.get_profile('default'))
    body = {'profile_id': 'office', 'revision': state['revision'], 'policies': ROWS}
    saved = client.put(url, json=body)
    assert saved.status_code == 200, saved.get_json()
    assert saved.headers['Cache-Control'] == 'no-store'
    assert saved.get_json()['policies'] == ROWS
    assert repository.get_profile('default') == default
    assert 'token' not in saved.get_data(as_text=True)
    assert client.put(url, json=body).status_code == 409
    body['revision'] = saved.get_json()['revision']
    manager.update_agent(aid, {'profile_id': 'default'})
    assert client.put(url, json=body).status_code == 409


def test_publish_rejects_stale_device_policy_snapshot_before_contacting_agent(oxidns_app, monkeypatch):
    app, repository = oxidns_app
    manager = get_agent_manager()
    aid = manager.register_agent({'name': 'DNS', 'host': '127.0.0.1', 'port': 18000,
                                  'service_type': 'oxidns', 'profile_id': 'office'})['id']
    client = app.test_client()
    state = client.get(f'/api/agents/{aid}/oxidns/device-policies').get_json()
    monkeypatch.setattr(manager, 'begin_deployment', lambda *args: pytest.fail('Stale draft must not begin publication'))
    body = {'expected_profile_id': 'default', 'expected_oxidns_revision': state['revision']}
    assert client.post(f'/api/agents/{aid}/push-config', json=body).status_code == 409
    body['expected_profile_id'] = 'office'
    repository.update_profile_transaction('office', lambda config: config['oxidns'].update(worker_threads=8))
    assert client.post(f'/api/agents/{aid}/push-config', json=body).status_code == 409


def test_api_requires_admin_and_only_accepts_oxidns(oxidns_app, monkeypatch):
    from backend.common import auth
    app, _ = oxidns_app
    monkeypatch.setattr(auth, 'ADMIN_USERNAME', 'admin')
    monkeypatch.setattr(auth, 'ADMIN_PASSWORD', 'password')
    agent = get_agent_manager().register_agent({'name': 'proxy', 'host': '127.0.0.1',
                                               'port': 18000, 'service_type': 'mihomo'})
    url = f'/api/agents/{agent["id"]}/oxidns/device-policies'
    client = app.test_client()
    for method in (client.get, client.put):
        assert method(url).status_code == 401
        assert method(url, headers={'Authorization': 'Bearer ' + agent['token']}).status_code == 401
    assert client.get(url, headers={'Authorization': 'Bearer ' + auth.generate_token('admin')}).status_code == 400
