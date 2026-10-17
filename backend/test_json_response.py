"""Single encoding must preserve global redaction and encoder behavior."""

import copy
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from flask import Flask, jsonify, request
from flask.json.provider import DefaultJSONProvider

from backend.common import config_export
from backend.common.json_response import jsonify_external
from backend.common.profile_context import install_profile_context


def make_app(monkeypatch, payload, *, after=None, provider=None):
    tokens = ['private-secret']
    monkeypatch.setattr(config_export, '_repository_rule_proxy_tokens', lambda: tokens)
    monkeypatch.setattr(config_export, '_repository_agent_tokens', lambda: ['agent-secret'])
    app = Flask(__name__)
    app.testing = True
    if provider is not None:
        app.json = provider(app)
    install_profile_context(app)
    if after is not None:
        app.after_request(lambda response: after(response, tokens))

    @app.get('/ordinary')
    def ordinary():
        return jsonify(payload)

    @app.get('/single')
    def single():
        return jsonify_external(payload)

    return app


def test_large_response_encodes_once_and_keeps_redaction(monkeypatch):
    payload = {'items': [{'name': '例子.test', 'secret_url': 'https://host/private%252Dsecret',
                          'agent-secret': 'agent-secret', 'rule_proxy_token': 'hidden'}
                         for _ in range(2000)]}
    original = copy.deepcopy(payload)
    app = make_app(monkeypatch, payload)
    calls = {'dumps': 0, 'loads': 0}
    for name in calls:
        method = getattr(json, name)

        def counted(*args, _name=name, _method=method, **kwargs):
            # Observe encoding without replacing the provider's own methods,
            # which intentionally selects the conservative custom-codec path.
            value = args[0]
            if (isinstance(value, dict) and 'items' in value
                    or isinstance(value, (str, bytes)) and len(value) > 10000):
                calls[_name] += 1
            return _method(*args, **kwargs)

        monkeypatch.setattr(json, name, counted)
    normal = app.test_client().get('/ordinary')
    assert calls == {'dumps': 2, 'loads': 1}
    calls.update(dumps=0, loads=0)
    single = app.test_client().get('/single')
    assert calls == {'dumps': 1, 'loads': 0}
    assert single.data == normal.data
    assert single.content_type == normal.content_type
    assert single.content_length == normal.content_length
    assert single.json['items'][0] == {
        'name': '例子.test', 'secret_url': '[REDACTED]', '[REDACTED]': '[REDACTED]',
    }
    assert payload == original


@pytest.mark.parametrize('payload', [
    {'integer_keys': {1: 'private-secret'}, 'safe': {'mixed': (1, 'agent-secret')}},
    {'date': datetime(2026, 10, 17, tzinfo=timezone.utc), 'decimal': Decimal('1.2')},
    {'values': [None, True, False, 0, -3, 1.5, float('inf'), float('nan')]},
    None, 'private-secret', [], {},
])
def test_non_native_encoder_values_and_scalars_keep_wire_behavior(monkeypatch, payload):
    app = make_app(monkeypatch, payload)
    assert app.test_client().get('/single').data == app.test_client().get('/ordinary').data


def test_deep_json_keeps_global_depth_redaction(monkeypatch):
    payload = {'value': 'private-secret'}
    for _ in range(150):
        payload = [payload]
    app = make_app(monkeypatch, payload)
    single = app.test_client().get('/single')
    assert single.data == app.test_client().get('/ordinary').data
    assert b'[REDACTED]' in single.data


@pytest.mark.parametrize('sort_keys', [True, False])
def test_redacted_key_collisions_preserve_encoder_order(monkeypatch, sort_keys):
    payload = {'private-secret-z': 1, 'private-secret-a': 2, '[REDACTED]': 3,
               'nested': [{'agent-secret-z': 4, 'agent-secret-a': 5}]}
    app = make_app(monkeypatch, payload)
    app.json.sort_keys = sort_keys
    assert app.test_client().get('/single').data == app.test_client().get('/ordinary').data


@pytest.mark.parametrize('payload', [
    {'value': 'prefix-\ud83d\ude00'},
    {'prefix-\ud83d\ude00': 'value'},
    {'nested': [{'value': 'prefix-\ud83d\ude00'}]},
    {'value': '\ud800'},
])
def test_surrogate_strings_keep_normalization_before_redaction(monkeypatch, payload):
    app = make_app(monkeypatch, payload)
    monkeypatch.setattr(config_export, '_repository_rule_proxy_tokens', lambda: ['prefix-😀'])
    single = app.test_client().get('/single')
    assert single.data == app.test_client().get('/ordinary').data
    assert b'prefix-' not in single.data


def test_custom_json_provider_keeps_serialization_hook(monkeypatch):
    class Provider(DefaultJSONProvider):
        def dumps(self, value, **kwargs):
            return super().dumps(value, **kwargs).replace('ordinary-value', 'agent-secret')

    app = make_app(monkeypatch, {'value': 'ordinary-value'}, provider=Provider)
    assert app.test_client().get('/single').data == app.test_client().get('/ordinary').data
    assert app.test_client().get('/single').json == {'value': '[REDACTED]'}


def test_instance_json_codec_override_keeps_serialization_hook(monkeypatch):
    app = make_app(monkeypatch, {'value': 'ordinary-value'})
    original = app.json.dumps
    monkeypatch.setattr(app.json, 'dumps', lambda value, **kwargs: original(value, **kwargs).replace(
        'ordinary-value', 'agent-secret'))
    assert app.test_client().get('/single').data == app.test_client().get('/ordinary').data
    assert app.test_client().get('/single').json == {'value': '[REDACTED]'}


def test_body_changed_after_encoding_is_sanitized_again(monkeypatch):
    def alter(response, _tokens):
        if request.path == '/single':
            response.set_data('{"value": "agent-secret"}')
        return response

    app = make_app(monkeypatch, {'value': 'safe'}, after=alter)
    assert app.test_client().get('/single').json == {'value': '[REDACTED]'}


def test_rotated_credentials_in_same_request_do_not_skip_redaction(monkeypatch):
    def rotate(response, tokens):
        tokens.append('new-private-secret')
        return response

    app = make_app(monkeypatch, {'value': 'new-private-secret'}, after=rotate)
    assert app.test_client().get('/single').json == {'value': '[REDACTED]'}


def test_client_marker_cannot_disable_redaction(monkeypatch):
    payload = {'_configflow_sanitized_json': True, 'value': 'agent-secret'}
    app = make_app(monkeypatch, payload)
    response = app.test_client().get('/ordinary', headers={'X-ConfigFlow-Sanitized': 'true'})
    assert response.json['value'] == '[REDACTED]'
