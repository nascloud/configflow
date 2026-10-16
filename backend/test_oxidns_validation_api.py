"""Draft persistence is lossless; every publication uses authoritative rules."""
from copy import deepcopy
import json

import pytest
import yaml

from backend.common.oxidns_validation import (validate_settings, validate_native_config,
                                             OxidnsValidationError, schemas, format_error)
from backend.common.oxidns_native import native_document
from backend.routes.agents import _prepare_transactional_content
from backend.test_oxidns_integration import oxidns_app


def settings(document):
    return {'config_mode': 'native', 'native_config': yaml.safe_dump(document, sort_keys=False),
            'additional_files': []}


@pytest.mark.parametrize('document,path', [
    ({'runtime': {'worker_threads': 0}, 'plugins': []}, ['runtime', 'worker_threads']),
    ({'plugins': [{'tag': 'cache', 'type': 'cache', 'args': {'size': -1}}]}, ['plugins', 0, 'args', 'size']),
    ({'plugins': [{'tag': 'http', 'type': 'http_server', 'args': {}}]}, ['plugins', 0, 'args', 'listen']),
    ({'plugins': [{'tag': 'future', 'type': 'forward', 'args': {}}]}, ['plugins', 0, 'args', 'upstreams']),
])
def test_draft_saved_exactly_but_same_rules_block_publishing(oxidns_app, monkeypatch, document, path):
    app, repository = oxidns_app
    client = app.test_client()
    draft = settings(document)
    draft['native_config'] = '# preserve comments and incomplete values\n' + draft['native_config']
    before = deepcopy(repository.get_profile('office')['mosdns'])
    checked = client.post('/api/profiles/office/oxidns/validate', json={'settings': draft})
    assert checked.status_code == 200
    assert checked.headers['Cache-Control'] == 'no-store'
    assert not checked.json['valid']
    assert any(item['path'] == path and item['severity'] == 'error' for item in checked.json['issues'])
    saved = client.post('/api/profiles/office/oxidns/settings', json=draft)
    assert saved.status_code == 200, saved.json
    assert saved.json['draft'] is True
    persisted = repository.get_profile('office')['oxidns']
    assert persisted['native_config'] == draft['native_config']
    assert repository.get_profile('office')['mosdns'] == before
    monkeypatch.setattr('backend.agents.deployment_bundle.build_deployment_bundle',
                        lambda *_a, **_k: pytest.fail('invalid draft uploaded or materialized'))
    with pytest.raises(OxidnsValidationError) as error:
        _prepare_transactional_content({'id': 'fixture', 'service_type': 'oxidns'}, {'oxidns': persisted})
    assert any(item['path'] == path for item in error.value.issues)


def test_legal_empty_null_and_future_values_remain_unchanged(oxidns_app):
    app, repository = oxidns_app
    client = app.test_client()
    document = {'runtime': {'worker_threads': None}, 'api': {'http': None}, 'future': {'enabled': False},
                'plugins': [{'tag': 'reverse', 'type': 'reverse_lookup', 'args': {}},
                            {'tag': 'cache', 'type': 'cache', 'args': None},
                            {'tag': 'extension', 'type': 'future_plugin', 'args': {'zero': 0, 'null': None, 'empty': []}}]}
    draft = settings(document)
    original = deepcopy(draft)
    result = validate_settings(draft)
    assert result['valid'], result
    assert any(item['code'] == 'unknown-plugin' for item in result['issues'])
    assert draft == original
    assert client.post('/api/profiles/office/oxidns/settings', json=draft).status_code == 200
    assert yaml.safe_load(repository.get_profile('office')['oxidns']['native_config']) == document


def test_included_plugin_issues_point_to_original_file_and_local_index():
    draft = settings({'include': ['parts/first.yaml'], 'plugins': [{'tag': 'main', 'type': 'reverse_lookup', 'args': {}}]})
    draft['additional_files'] = [{'path': 'parts/first.yaml', 'content': 'plugins:\n- tag: cache\n  type: cache\n  args: {size: -1}\n'}]
    original = deepcopy(draft)
    result = validate_settings(draft)
    assert not result['valid']
    assert any(i['source'] == 'parts/first.yaml' and i['path'] == ['plugins', 0, 'args', 'size'] for i in result['issues'])
    assert draft == original


def test_missing_include_is_savable_draft_but_not_publishable(oxidns_app):
    app, _ = oxidns_app
    client = app.test_client()
    draft = settings({'include': ['missing.yaml'], 'plugins': []})
    assert client.post('/api/oxidns/settings', json=draft).status_code == 200
    result = client.post('/api/oxidns/validate', json={'settings': draft})
    assert result.status_code == 200 and not result.json['valid']


@pytest.mark.parametrize('content', ['plugins: [', 'plugins: {}', '[]'])
def test_include_structure_diagnostic_locates_attachment(content):
    draft = settings({'include': ['parts/broken.yaml']})
    draft['additional_files'] = [{'path': 'parts/broken.yaml', 'content': content}]
    result = validate_settings(draft)
    assert not result['valid']
    assert result['issues'][0]['source'] == 'parts/broken.yaml'


@pytest.mark.parametrize('text', ['[not, an, object]', 'broken: [', 'plugins: []\nplugins: []', 'runtime: &loop {value: *loop}'])
def test_unsafe_or_non_object_yaml_does_not_persist(oxidns_app, text):
    app, repository = oxidns_app
    before = deepcopy(repository.get_profile('office'))
    response = app.test_client().post('/api/profiles/office/oxidns/settings', json={'config_mode': 'native', 'native_config': text})
    assert response.status_code == 400
    assert repository.get_profile('office') == before


def test_diagnostics_never_echo_secret_values():
    secret = 'super-secret-password-123'
    draft = settings({'api': {'http': {'auth': {'type': 'invalid', 'username': secret, 'password': secret}}},
                      'plugins': [{'tag': 'http', 'type': 'http_request', 'args': {'url': secret, 'method': secret + ' invalid'}}]})
    result = validate_settings(draft)
    assert not result['valid']
    assert secret not in json.dumps(result)


def test_warning_limit_cannot_hide_errors():
    document = {**{f'future_{i}': None for i in range(250)},
                'plugins': [{'tag': 'cache', 'type': 'cache', 'args': {'size': -1}}]}
    result = validate_settings(settings(document))
    assert not result['valid']
    assert any(i['path'] == ['plugins', 0, 'args', 'size'] for i in result['issues'])
    assert len(result['issues']) <= 200


def test_catalog_covers_every_known_plugin_and_formats():
    global_schema, plugins = schemas()
    assert len(plugins) == 65
    formats = set()
    def visit(value):
        if isinstance(value, dict):
            if 'format' in value:
                formats.add(value['format'])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(global_schema)
    visit(plugins)
    for name in formats:
        # Unknown formats raise: adding a rule cannot silently disable checks.
        format_error(name, '')


def test_rule_endpoint_is_authenticated(oxidns_app, monkeypatch):
    app, _ = oxidns_app
    monkeypatch.setattr('backend.common.auth.ADMIN_USERNAME', 'admin')
    monkeypatch.setattr('backend.common.auth.ADMIN_PASSWORD', 'test-password')
    response = app.test_client().post('/api/oxidns/validate', json={'settings': settings({'plugins': []})})
    assert response.status_code in (401, 403)
