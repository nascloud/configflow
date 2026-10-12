"""订阅级节点名正则筛选：缓存保留原始节点，下发只输出匹配的节点。"""
from types import SimpleNamespace

import yaml

from backend.utils.subscription_filter import filter_subscription_items, validate_regex_filter

YAML_TEXT = """proxies:
  - {name: 香港 01, type: ss, server: 1.1.1.1, port: 1, cipher: aes-128-gcm, password: p}
  - {name: 日本 01, type: ss, server: 1.1.1.2, port: 2, cipher: aes-128-gcm, password: p}
  - {name: 剩余流量 100G, type: ss, server: 1.1.1.3, port: 3, cipher: aes-128-gcm, password: p}
"""


def test_filter_keeps_matching_names_and_ignores_blank_or_invalid_pattern():
    items = [{'name': '香港 01'}, {'name': '日本 01'}, {'name': 'HK-02'}]
    assert [i['name'] for i in filter_subscription_items({'regex_filter': '香港|HK'}, items)] == ['香港 01', 'HK-02']
    assert filter_subscription_items({'regex_filter': '  '}, items) == items
    assert filter_subscription_items({}, items) == items
    # 非法正则在运行期不筛选，避免误清空节点（保存时已拦截）
    assert filter_subscription_items({'regex_filter': '('}, items) == items


def test_validate_regex_filter():
    assert validate_regex_filter(None) is None
    assert validate_regex_filter('') is None
    assert validate_regex_filter('^(?!.*剩余).*$') is None
    assert validate_regex_filter('(') is not None
    assert validate_regex_filter(123) is not None


def test_provider_delivers_filtered_proxies_but_caches_raw_nodes():
    from backend.utils.provider_delivery import prepare_subscription_provider

    sub = {'id': 's1', 'name': 'Air', 'url': 'http://airport.test/sub', 'regex_filter': '香港|日本'}
    snapshot = SimpleNamespace(validate=lambda proxies, provider_name=None: None, has_chains=False)
    result = prepare_subscription_provider(sub, {}, snapshot, fetch=lambda _id, _url: (YAML_TEXT, 'sub_store'))

    delivered = [p['name'] for p in yaml.safe_load(result['content'])['proxies']]
    assert delivered == ['香港 01', '日本 01']
    (_sub_id, cached_nodes, _meta), = result['cache_updates']
    assert len(cached_nodes) == 3


def test_subscription_save_rejects_invalid_regex():
    from backend.routes.subscriptions import validate_subscription_fields
    from flask import Flask

    with Flask(__name__).app_context():
        error = validate_subscription_fields({'name': 'a', 'url': 'http://x', 'regex_filter': '('})
        assert error is not None and error[1] == 400
        assert validate_subscription_fields({'name': 'a', 'url': 'http://x', 'regex_filter': '香港'}) is None
