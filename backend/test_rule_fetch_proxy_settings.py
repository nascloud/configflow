"""Persistence, validation and credential handling for the rule download proxy."""

import json
import logging

import pytest
from flask import Flask

from backend.common import config as config_module
from backend.common.config_export import sanitize_config_for_output
from backend.common.config_repository import ProfileRepository
from backend.routes import register_blueprints


PROXY = 'http://proxy-user:proxy-secret@192.168.0.3:7890'
ENDPOINT = '/api/settings/rule-fetch-proxy'


@pytest.fixture
def settings_client(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path)
    config_module.set_repository(repository)
    monkeypatch.setattr('backend.common.auth.is_auth_enabled', lambda: False)
    app = Flask(__name__)
    register_blueprints(app)
    return app.test_client(), repository


def test_proxy_defaults_to_empty_for_new_and_existing_settings(settings_client):
    client, repository = settings_client
    assert repository.get_system()['system_config']['rule_fetch_proxy'] == ''
    assert config_module.get_default_config()['system_config']['rule_fetch_proxy'] == ''
    def remove_new_setting(system):
        system['system_config'].pop('rule_fetch_proxy')
    repository.update_system_transaction(remove_new_setting)
    assert client.get(ENDPOINT).get_json() == {'rule_fetch_proxy': ''}


def test_save_proxy_is_global_persistent_and_can_be_cleared(settings_client, tmp_path, caplog):
    client, repository = settings_client
    repository.create_profile({'id': 'another', 'name': 'Another'})
    with caplog.at_level(logging.INFO):
        response = client.post(ENDPOINT, json={'rule_fetch_proxy': f' {PROXY} '})
    assert response.status_code == 200
    assert response.get_json()['rule_fetch_proxy'] == PROXY
    assert 'proxy-secret' not in caplog.text
    assert 'proxy-user' not in caplog.text
    assert ProfileRepository(tmp_path).get_system()['system_config']['rule_fetch_proxy'] == PROXY
    assert client.get(ENDPOINT, headers={'X-ConfigFlow-Profile': 'another'}).get_json() == {
        'rule_fetch_proxy': PROXY
    }

    response = client.post(ENDPOINT, json={'rule_fetch_proxy': '  '})
    assert response.status_code == 200
    assert client.get(ENDPOINT).get_json() == {'rule_fetch_proxy': ''}
    assert ProfileRepository(tmp_path).get_system()['system_config']['rule_fetch_proxy'] == ''


@pytest.mark.parametrize('value', [
    None, False, 123, [], {}, '192.168.0.3:7890',
    'socks5://proxy-user:proxy-secret@proxy.test:1080',
    'http://proxy-user:proxy-secret@proxy.test:invalid',
    'http://proxy-user:proxy-secret@proxy.test:70000',
    'http://proxy-user:proxy-secret@proxy.test:7890/rules',
    'http://proxy-user:proxy-secret@proxy.test:7890?token=secret',
    'http://proxy-user:proxy-secret@proxy.test:7890#fragment',
])
def test_invalid_proxy_does_not_overwrite_previous_or_echo_credentials(settings_client, value, caplog):
    client, repository = settings_client
    repository.update_system_transaction(lambda system: system['system_config'].update(rule_fetch_proxy=PROXY))
    before = repository.get_system()
    with caplog.at_level(logging.DEBUG):
        response = client.post(ENDPOINT, json={'rule_fetch_proxy': value})
    assert response.status_code == 400
    assert repository.get_system() == before
    assert 'proxy-secret' not in response.get_data(as_text=True) + caplog.text
    assert 'proxy-user' not in response.get_data(as_text=True) + caplog.text


@pytest.mark.parametrize('body', [{}, [], {'unrelated': PROXY}])
def test_missing_proxy_field_cannot_accidentally_clear_setting(settings_client, body):
    client, repository = settings_client
    repository.update_system_transaction(lambda system: system['system_config'].update(rule_fetch_proxy=PROXY))
    assert client.post(ENDPOINT, json=body).status_code == 400
    assert repository.get_system()['system_config']['rule_fetch_proxy'] == PROXY


def test_proxy_setting_requires_authentication(settings_client, monkeypatch):
    client, _ = settings_client
    monkeypatch.setattr('backend.common.auth.is_auth_enabled', lambda: True)
    assert client.get(ENDPOINT).status_code == 401
    assert client.post(ENDPOINT, json={'rule_fetch_proxy': PROXY}).status_code == 401


def test_proxy_save_failure_does_not_echo_credentials(settings_client, monkeypatch, caplog):
    client, _ = settings_client

    def fail_save(_config):
        raise OSError(f'Cannot save {PROXY}')

    monkeypatch.setattr('backend.routes.settings.save_system_config', fail_save)
    with caplog.at_level(logging.ERROR):
        response = client.post(ENDPOINT, json={'rule_fetch_proxy': PROXY})
    assert response.status_code == 500
    assert 'proxy-secret' not in response.get_data(as_text=True) + caplog.text
    assert 'proxy-user' not in response.get_data(as_text=True) + caplog.text


def test_full_backup_restores_proxy_but_shared_exports_hide_it(settings_client, tmp_path):
    _, repository = settings_client
    repository.update_system_transaction(lambda system: system['system_config'].update(rule_fetch_proxy=PROXY))
    backup = repository.export_all()
    assert backup['system']['system_config']['rule_fetch_proxy'] == PROXY
    restored = ProfileRepository(tmp_path / 'restored')
    restored.import_all(backup)
    assert restored.get_system()['system_config']['rule_fetch_proxy'] == PROXY

    shared = repository.export_all(desensitize=True)
    public = sanitize_config_for_output(repository.get_compat_config('default'))
    for output in (shared, public):
        serialized = json.dumps(output)
        assert 'rule_fetch_proxy' not in serialized
        assert 'proxy-secret' not in serialized
        assert 'proxy-user' not in serialized
    assert repository.get_system()['system_config']['rule_fetch_proxy'] == PROXY
