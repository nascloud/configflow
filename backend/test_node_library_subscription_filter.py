"""节点库的订阅展示：来源感知去重与导入节点资格共用后端正则。"""
import copy

import pytest
from flask import Flask

from backend.routes import proxy_groups_bp
from backend.routes import proxy_groups


@pytest.fixture
def preview_client(monkeypatch):
    config = {
        'subscriptions': [
            {'id': 'one', 'name': '订阅一', 'regex_filter': '(?i)^hk'},
            {'id': 'two', 'name': '订阅二'},
            {'id': 'off', 'name': '禁用订阅', 'enabled': False},
        ],
        'nodes': [
            {'id': 'manual', 'name': 'HK shared'},
            {'id': 'imported', 'name': 'HK shared', 'subscription_id': 'one'},
            {'id': 'filtered', 'name': 'JP imported', 'subscription_id': 'one'},
            {'id': 'node-off', 'name': 'hk disabled node', 'subscription_id': 'one', 'enabled': False},
            {'id': 'sub-off', 'name': 'HK off', 'subscription_id': 'off'},
            {'id': 'orphan', 'name': 'HK orphan', 'subscription_id': 'deleted'},
        ],
    }
    caches = {
        'one': {'nodes': [{'name': 'HK shared', 'type': 'ss'}, {'name': 'JP cache', 'type': 'ss'}]},
        'two': {'nodes': [{'name': 'HK shared', 'type': 'trojan'}]},
        'off': {'nodes': [{'name': 'HK off', 'type': 'ss'}]},
    }
    monkeypatch.setattr('backend.common.auth.is_auth_enabled', lambda: False)
    monkeypatch.setattr(proxy_groups, 'get_shared_config', lambda: config)
    monkeypatch.setattr(proxy_groups, 'load_subscription_cache', lambda sub_id: caches.get(sub_id))
    app = Flask(__name__)
    app.register_blueprint(proxy_groups_bp)
    return app.test_client(), config, caches


def preview(client, **options):
    response = client.post('/api/proxy-groups/preview-regex', json={
        'source': 'subscription', 'subscriptions': ['one', 'two', 'off', 'deleted'],
        'regex': '.*', **options,
    })
    assert response.status_code == 200
    return response.get_json()


def test_source_aware_preview_keeps_same_name_across_subscriptions(preview_client):
    client, config, caches = preview_client
    original = copy.deepcopy((config, caches))
    data = preview(client, deduplicate_by_source=True)
    assert [(n['source_id'], n['name']) for n in data['nodes']] == [
        ('one', 'HK shared'), ('two', 'HK shared'),
    ]
    # 节点自身禁用仍可在节点库管理；来源禁用、删除或正则不匹配则不合格。
    assert data['imported_node_ids'] == ['imported', 'node-off']
    assert (config, caches) == original


def test_strategy_preview_preserves_default_name_deduplication(preview_client):
    client, _, _ = preview_client
    data = preview(client)
    assert [n['name'] for n in data['nodes']] == ['HK shared']
    assert 'imported_node_ids' not in data


def test_imported_node_eligibility_uses_current_regex_without_cache(preview_client):
    client, config, caches = preview_client
    caches.clear()
    config['subscriptions'][0]['regex_filter'] = '^JP'
    data = preview(client, deduplicate_by_source=True)
    assert data['imported_node_ids'] == ['filtered']
    assert [n['name'] for n in data['nodes']] == ['JP imported']
    config['subscriptions'][0]['enabled'] = False
    data = preview(client, deduplicate_by_source=True)
    assert data['imported_node_ids'] == []
    assert data['nodes'] == []
