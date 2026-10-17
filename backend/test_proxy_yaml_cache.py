"""YAML cache regressions: unchanged SafeLoader semantics and private results."""
from concurrent.futures import ThreadPoolExecutor
import datetime
import json
import math
import threading
from unittest.mock import Mock

import pytest
import yaml

from backend.converters import mihomo


@pytest.fixture(autouse=True)
def isolated_cache():
    with mihomo._PROXY_YAML_CACHE_LOCK:
        mihomo._PROXY_YAML_CACHE.clear()
    yield
    with mihomo._PROXY_YAML_CACHE_LOCK:
        mihomo._PROXY_YAML_CACHE.clear()


def old_parse(text):
    """Original URI/JSON/SafeLoader behavior, independent of the new cache."""
    stripped = text.strip()
    if '://' in stripped.split('\n')[0] and not ':' == stripped.split('://')[0][-1:]:
        first_line = stripped.split('\n')[0].strip()
        if not first_line.endswith(':') and '://' in first_line:
            scheme = first_line.split('://')[0]
            if ' ' not in scheme and ':' not in scheme:
                return None
    if stripped.startswith('{'):
        try:
            value = json.loads(stripped)
            if isinstance(value, dict) and value.get('type'):
                return value
        except Exception:
            pass
    try:
        value = yaml.safe_load(stripped)
        if isinstance(value, dict) and value.get('type'):
            return value
    except Exception:
        pass
    return None


def typed(value):
    if isinstance(value, float) and math.isnan(value):
        return ('float', 'nan')
    if isinstance(value, dict):
        return ('dict', sorted(((typed(k), typed(v)) for k, v in value.items()), key=repr))
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, [typed(child) for child in value])
    return (type(value).__name__, value)


def semantic_cases():
    normal = [f'name: sample-{i}\ntype: vless\nserver: proxy.example\nport: 443\n'
              'uuid: 00000000-0000-4000-8000-000000000000\ntls: true\nudp: true\n'
              'servername: edge.example\nalpn: [h2]\nws-opts:\n  path: /edge\n'
              '  headers:\n    Host: edge.example' for i in range(8)]
    return normal + ['vless://sample@proxy.example:443?type=ws', 'ss://sample@proxy.example:443'] + [
        json.dumps(yaml.safe_load(text)) for text in normal] + [
        '', 'null', '~', 'name: sample\nserver: proxy.example', 'type: false', 'type: 0',
        'type: []', 'type: {}', '- type: ss', 'type: [', '{type: ss, port: 443}',
        'server: https://proxy.example\ntype: ss', 'type: ss\ndialer-proxy: DIRECT\nport: 443',
        '{"type":"ss","n":123456789012345678901234567890}', '{"type":false}',
        'type: ss\nflag: yes', 'type: ss\nflag: on', 'type: ss\nvalue: 2026-10-17',
        'type: ss\nvalue: 2026-10-17T01:02:03Z', 'type: ss\nvalue: 1e3',
        'type: ss\nvalue: 0777', 'type: ss\nvalue: 0o777', 'type: ss\nvalue: 1:20',
        'type: ss\nvalue: 08', 'type: ss\nport: 80\nport: 443',
        'type: ss\nyes: first\ntrue: second', 'type: ss\nvalue: !!binary aGVsbG8=',
        'type: ss\n---\ntype: trojan',
        'base: &base {server: proxy.example, port: 443}\ntype: ss\n<<: *base',
        'type: ss\nvalue: .nan', '{"type":"ss","value":NaN}',
    ]


@pytest.mark.parametrize('text', semantic_cases())
def test_old_parse_semantics_on_first_and_repeated_calls(text):
    expected = typed(old_parse(text))
    assert typed(mihomo._parse_structured_proxy_string(text)) == expected
    assert typed(mihomo._parse_structured_proxy_string(text)) == expected


def spy_loader(monkeypatch):
    wrapped = Mock(wraps=yaml.safe_load)
    monkeypatch.setattr(mihomo.yaml, 'safe_load', wrapped)
    return wrapped


NESTED = ('type: vless\nname: original\nserver: proxy.example\nport: 443\n'
          'uuid: 00000000-0000-4000-8000-000000000000\nalpn: [h2]\n'
          'ws-opts:\n  headers:\n    Host: edge.example')


def test_nested_results_and_conversion_names_do_not_pollute_cache(monkeypatch):
    load = spy_loader(monkeypatch)
    first = mihomo._parse_structured_proxy_string(NESTED)
    first['ws-opts']['headers']['Host'] = 'changed.invalid'
    first['alpn'].append('changed')
    first['dialer-proxy'] = 'changed'
    later = mihomo._parse_structured_proxy_string(NESTED)
    assert later['ws-opts']['headers']['Host'] == 'edge.example'
    assert later['alpn'] == ['h2'] and 'dialer-proxy' not in later
    one = mihomo.convert_node_to_mihomo({'name': 'one', 'proxy_string': NESTED})
    two = mihomo.convert_node_to_mihomo({'name': 'two', 'proxy_string': NESTED})
    assert one['name'] == 'one' and two['name'] == 'two'
    assert mihomo._parse_structured_proxy_string(NESTED)['name'] == 'original'
    assert load.call_count == 1


def test_full_text_change_invalidates_and_lru_evicts(monkeypatch):
    monkeypatch.setattr(mihomo, '_PROXY_YAML_CACHE_SIZE', 2)
    load = spy_loader(monkeypatch)
    a, b, c = ['type: ss\nserver: ' + name for name in ('a.example', 'b.example', 'c.example')]
    for text in (a, b, ' \n' + a + '\n ', c):
        assert mihomo._parse_structured_proxy_string(text)['server'] == text.strip().split(': ')[-1]
    assert set(mihomo._PROXY_YAML_CACHE) == {a, c}
    assert load.call_count == 3
    assert mihomo._parse_structured_proxy_string(b)['server'] == 'b.example'
    assert load.call_count == 4 and len(mihomo._PROXY_YAML_CACHE) == 2


def test_uri_and_valid_json_do_not_touch_yaml_cache(monkeypatch):
    monkeypatch.setattr(mihomo, '_load_proxy_yaml', Mock(side_effect=AssertionError('YAML path entered')))
    assert mihomo._parse_structured_proxy_string('ss://sample@proxy.example:443') is None
    assert mihomo._parse_structured_proxy_string('{"type":"ss","port":443}') == {'type': 'ss', 'port': 443}
    mihomo._load_proxy_yaml.assert_not_called()
    assert not mihomo._PROXY_YAML_CACHE


@pytest.mark.parametrize('text', ['type: [', 'null', 'name: sample', 'type: false'])
def test_failed_and_non_proxy_results_not_retained(monkeypatch, text):
    load = spy_loader(monkeypatch)
    assert mihomo._parse_structured_proxy_string(text) is None
    assert mihomo._parse_structured_proxy_string(text) is None
    assert load.call_count == 2 and not mihomo._PROXY_YAML_CACHE


def test_loader_failure_does_not_poison_later_retry(monkeypatch):
    real = yaml.safe_load
    load = Mock(side_effect=[yaml.YAMLError('temporary failure'), real('type: ss')])
    monkeypatch.setattr(mihomo.yaml, 'safe_load', load)
    assert mihomo._parse_structured_proxy_string('type: ss') is None
    assert mihomo._parse_structured_proxy_string('type: ss') == {'type': 'ss'}
    assert mihomo._parse_structured_proxy_string('type: ss') == {'type': 'ss'}
    assert load.call_count == 2


@pytest.mark.parametrize('extra,cacheable', [('', True), ('x', False), ('中', False)])
def test_input_byte_budget_boundary(monkeypatch, extra, cacheable):
    text = 'type: ss\nname: abc'
    monkeypatch.setattr(mihomo, '_PROXY_YAML_MAX_INPUT_BYTES', len(text.encode()))
    load = spy_loader(monkeypatch)
    for _ in range(2):
        assert mihomo._parse_structured_proxy_string(text + extra)['name'] == 'abc' + extra
    assert load.call_count == (1 if cacheable else 2)
    assert bool(mihomo._PROXY_YAML_CACHE) is cacheable


def test_multibyte_input_below_character_limit_still_bypasses_byte_limit(monkeypatch):
    text = 'type: ss\nname: ' + '中' * 10
    monkeypatch.setattr(mihomo, '_PROXY_YAML_MAX_INPUT_BYTES', len(text) + 1)
    load = spy_loader(monkeypatch)
    for _ in range(2):
        assert mihomo._parse_structured_proxy_string(text)['name'] == '中' * 10
    assert load.call_count == 2 and not mihomo._PROXY_YAML_CACHE


@pytest.mark.parametrize('limit,value,text', [
    ('_PROXY_YAML_MAX_RESULT_BYTES', 100, 'type: ss\ntext: ' + 'x' * 200),
    ('_PROXY_YAML_MAX_OBJECTS', 5, 'type: ss\nvalues: [a, b, c, d, e, f]'),
    ('_PROXY_YAML_MAX_DEPTH', 2, 'type: ss\nvalues: [[[item]]]'),
])
def test_result_budgets_bypass_without_changing_results(monkeypatch, limit, value, text):
    expected = old_parse(text)
    monkeypatch.setattr(mihomo, limit, value)
    load = spy_loader(monkeypatch)
    assert mihomo._parse_structured_proxy_string(text) == expected
    assert mihomo._parse_structured_proxy_string(text) == expected
    assert load.call_count == 2 and not mihomo._PROXY_YAML_CACHE


@pytest.mark.parametrize('text', [
    'type: ss\nvalue: 2026-10-17', 'type: ss\nvalue: !!binary aGVsbG8=',
    'type: ss\nvalue: !!set {a: null, b: null}', 'type: ss\nyes: first\ntrue: second',
])
def test_special_yaml_types_bypass_with_original_types(monkeypatch, text):
    expected = typed(old_parse(text))
    load = spy_loader(monkeypatch)
    assert typed(mihomo._parse_structured_proxy_string(text)) == expected
    assert typed(mihomo._parse_structured_proxy_string(text)) == expected
    assert load.call_count == 2 and not mihomo._PROXY_YAML_CACHE


def test_aliases_and_cycles_keep_identity_and_bypass_cache(monkeypatch):
    load = spy_loader(monkeypatch)
    alias = 'type: ss\na: &shared [item]\nb: *shared'
    cycle = '&root {type: ss, self: *root}'
    for _ in range(2):
        result = mihomo._parse_structured_proxy_string(alias)
        assert result['a'] is result['b']
        recursive = mihomo._parse_structured_proxy_string(cycle)
        assert recursive['self'] is recursive
    assert load.call_count == 4 and not mihomo._PROXY_YAML_CACHE


def test_alias_diamond_does_not_expand_during_cache_screening(monkeypatch):
    text = 'type: ss\na0: &a0 [item]\n' + ''.join(
        f'a{i}: &a{i} [*a{i-1}, *a{i-1}]\n' for i in range(1, 30))
    load = spy_loader(monkeypatch)
    for _ in range(2):
        result = mihomo._parse_structured_proxy_string(text)
        assert result['a29'][0] is result['a29'][1] is result['a28']
    assert load.call_count == 2 and not mihomo._PROXY_YAML_CACHE


def test_concurrent_cold_calls_parse_once_and_return_private_results(monkeypatch):
    load = spy_loader(monkeypatch)
    start = threading.Barrier(8)
    def work(number):
        start.wait(timeout=3)
        result = mihomo._parse_structured_proxy_string(NESTED)
        result['ws-opts']['headers']['Host'] = str(number)
        return result
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(work, range(8)))
    assert [result['ws-opts']['headers']['Host'] for result in results] == list(map(str, range(8)))
    assert load.call_count == 1
    assert mihomo._parse_structured_proxy_string(NESTED)['ws-opts']['headers']['Host'] == 'edge.example'


def test_deepcopy_does_not_hold_cache_lock(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real = mihomo.deepcopy
    def delayed(value):
        entered.set()
        assert release.wait(timeout=3)
        return real(value)
    monkeypatch.setattr(mihomo, 'deepcopy', delayed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(mihomo._parse_structured_proxy_string, NESTED)
        try:
            assert entered.wait(timeout=3)
            acquired = mihomo._PROXY_YAML_CACHE_LOCK.acquire(blocking=False)
            assert acquired
            if acquired:
                mihomo._PROXY_YAML_CACHE_LOCK.release()
        finally:
            release.set()
        assert future.result(timeout=3)['type'] == 'vless'
