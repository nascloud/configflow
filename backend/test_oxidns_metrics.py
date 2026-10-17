"""OxiDNS dashboard numbers must preserve unknowns and the real denominators."""
import pytest

from backend.agents import oxidns_metrics as metrics


def cache(tag, lookups, fresh, stale, misses=0, expired=0, entries=0):
    return '\n'.join([
        f'cache_lookup_total{{plugin_tag="{tag}"}} {lookups}',
        f'cache_hit_total{{plugin_tag="{tag}",kind="fresh"}} {fresh}',
        f'cache_hit_total{{plugin_tag="{tag}",kind="stale"}} {stale}',
        f'cache_miss_total{{plugin_tag="{tag}"}} {misses}',
        f'cache_expired_total{{plugin_tag="{tag}"}} {expired}',
        f'cache_entry_count{{plugin_tag="{tag}"}} {entries}',
    ])


def server(tag, protocol, requests, failed, elapsed, count, inflight=0):
    values = {'request_total': requests, 'failed_total': failed,
              'latency_sum_ms': elapsed, 'latency_count': count, 'inflight': inflight}
    return '\n'.join(
        f'server_{field}{{plugin_tag="{tag}",protocol="{protocol}"}} {value}'
        for field, value in values.items())


def test_multiple_caches_use_lookup_weighted_rate_including_expired():
    result = metrics.summarize_metrics('\n'.join([
        cache('large', 100, 60, 10, misses=20, expired=10, entries=40),
        cache('small', 10, 10, 0, entries=5),
    ]))
    assert result['metrics']['cache_hit_rate'] == pytest.approx(100 * 80 / 110)
    large, small = result['caches']
    assert large == {'tag': 'large', 'lookups': 100, 'hits': 70,
                     'fresh_hits': 60, 'stale_hits': 10, 'misses': 20,
                     'expired': 10, 'entries': 40, 'hit_rate': 70}
    assert small['hit_rate'] == 100
    assert result['metrics']['requests_total'] is None


def test_server_totals_and_weighted_latency_are_per_listener():
    result = metrics.summarize_metrics('\n'.join([
        server('dns', 'udp', 100, 2, 1000, 100, 3),
        server('dns', 'tcp', 10, 1, 1000, 10, 1),
        'query_total{plugin_tag="collector"} 999',
        'forward_query_total{plugin_tag="upstream"} 999',
    ]))
    assert result['metrics'] == {
        'requests_total': 110, 'failed_total': 3, 'inflight': 4,
        'avg_latency_ms': pytest.approx(2000 / 110), 'cache_hit_rate': None,
    }
    assert [(row['protocol'], row['avg_latency_ms']) for row in result['servers']] == [
        ('tcp', 100), ('udp', 10)]


def test_zero_observations_are_known_but_zero_denominators_are_unknown():
    result = metrics.summarize_metrics(cache('c', 0, 0, 0) + '\n' + server('s', 'udp', 0, 0, 0, 0))
    assert result['metrics'] == {'requests_total': 0, 'failed_total': 0,
                                 'inflight': 0, 'avg_latency_ms': None, 'cache_hit_rate': None}
    assert result['caches'][0]['hits'] == 0
    assert result['caches'][0]['hit_rate'] is None


def test_empty_or_unrelated_input_does_not_fabricate_zeroes():
    for text in ('', '# TYPE cache_hit_total counter\n', 'unrelated_total 500'):
        result = metrics.summarize_metrics(text)
        assert all(value is None for value in result['metrics'].values())
        assert result['servers'] == []
        assert result['caches'] == []


def test_partial_cache_and_listener_make_related_global_totals_unknown():
    result = metrics.summarize_metrics('\n'.join([
        cache('complete', 20, 10, 0),
        'cache_hit_total{plugin_tag="partial",kind="fresh"} 4',
        server('complete', 'udp', 10, 0, 20, 10),
        'server_inflight{plugin_tag="partial",protocol="tcp"} 0',
    ]))
    partial = result['caches'][1]
    assert partial['fresh_hits'] == 4
    assert partial['stale_hits'] is None
    assert partial['hits'] is None
    assert partial['lookups'] is None
    assert result['metrics']['cache_hit_rate'] is None
    assert result['metrics']['requests_total'] is None
    assert result['metrics']['avg_latency_ms'] is None
    assert result['metrics']['inflight'] == 0


@pytest.mark.parametrize('bad', ['NaN', '+Inf', '-Inf', '-1', '1e999', 'not_a_number', '1_000'])
def test_invalid_values_are_unknown_not_zero(bad):
    result = metrics.summarize_metrics(cache('c', 10, bad, 0) + '\n' + server('s', 'udp', bad, 0, 1, 1))
    assert result['caches'][0]['fresh_hits'] is None
    assert result['caches'][0]['hits'] is None
    assert result['metrics']['cache_hit_rate'] is None
    assert result['metrics']['requests_total'] is None


def test_contradictory_hit_count_never_exceeds_100_percent():
    result = metrics.summarize_metrics(cache('c', 10, 12, 1))
    assert result['caches'][0]['hits'] == 13
    assert result['caches'][0]['hit_rate'] is None
    assert result['metrics']['cache_hit_rate'] is None


def test_labels_use_prometheus_escaping_and_allow_commas_braces_and_timestamp():
    text = r'cache_entry_count{plugin_tag="a\"b\\c\nd,{x}",ignored="value,with=punctuation"} 7 1792100000000'
    result = metrics.summarize_metrics(text)
    assert result['caches'][0]['tag'] == 'a"b\\c\nd,{x}'
    assert result['caches'][0]['entries'] == 7


def test_prefixed_aliases_duplicates_and_conflicts_do_not_double_count():
    text = server('s', 'udp', 12, 1, 24, 12)
    prefixed = '\n'.join('oxidns_' + line for line in text.splitlines())
    assert metrics.summarize_metrics(text) == metrics.summarize_metrics(prefixed)
    assert metrics.summarize_metrics(text + '\n' + prefixed)['metrics']['requests_total'] == 12
    conflict = '\noxidns_server_request_total{protocol="udp",plugin_tag="s"} 13'
    assert metrics.summarize_metrics(text + conflict)['metrics']['requests_total'] is None


@pytest.mark.parametrize('bad_line', [
    'cache_entry_count{plugin_tag="broken} 1',
    r'cache_entry_count{plugin_tag="bad\q"} 1',
    'cache_entry_count{plugin_tag="a",plugin_tag="b"} 1',
    'cache_entry_count{plugin_tag="a" trailing} 1',
    'cache_entry_count{} 1',
    'cache_entry_count 1',
    'server_request_total{plugin_tag="a"} 1',
    'cache_hit_total{plugin_tag="a",kind="other"} 1',
    'cache_entry_count{plugin_tag="a"} 1 extra invalid',
])
def test_malformed_or_unusable_samples_are_skipped(bad_line):
    result = metrics.summarize_metrics(bad_line)
    assert result['caches'] == [] and result['servers'] == []


def test_scientific_notation_and_large_integer_counter():
    result = metrics.summarize_metrics('\n'.join([
        'server_request_total{plugin_tag="s",protocol="udp"} 18446744073709551615',
        'server_latency_count{plugin_tag="s",protocol="udp"} 2e2',
        'server_latency_sum_ms{plugin_tag="s",protocol="udp"} 3.0e3',
    ]))
    assert result['metrics']['requests_total'] == 18446744073709551615
    assert result['metrics']['avg_latency_ms'] == 15


@pytest.mark.parametrize('value', [None, b'metrics', 42, {}])
def test_nontext_input_is_rejected(value):
    with pytest.raises(ValueError, match='must be text'):
        metrics.summarize_metrics(value)


def test_input_limits_reject_instead_of_returning_truncated_totals(monkeypatch):
    monkeypatch.setattr(metrics, 'MAX_INPUT_BYTES', 20)
    with pytest.raises(ValueError, match='size limit'):
        metrics.summarize_metrics('x' * 21)
    with pytest.raises(ValueError, match='size limit'):
        metrics.summarize_metrics('界' * 7)
    monkeypatch.setattr(metrics, 'MAX_INPUT_BYTES', 1024)
    monkeypatch.setattr(metrics, 'MAX_SAMPLES', 1)
    with pytest.raises(ValueError, match='sample limit'):
        metrics.summarize_metrics('unknown 1\nunknown 2')
    monkeypatch.setattr(metrics, 'MAX_LINE_LENGTH', 8)
    with pytest.raises(ValueError, match='line exceeds'):
        metrics.summarize_metrics('#' * 9)


def test_inconsistent_cache_cannot_be_hidden_by_another_cache_denominator():
    result = metrics.summarize_metrics(cache('bad', 10, 12, 1) + '\n' + cache('good', 100, 20, 0))
    assert result['caches'][1]['hit_rate'] == 20
    assert result['metrics']['cache_hit_rate'] is None


def test_nonfinite_aggregate_is_unknown():
    result = metrics.summarize_metrics('\n'.join([
        server('a', 'udp', '1e308', 0, '1e308', 1),
        server('b', 'tcp', '1e308', 0, '1e308', 1),
    ]))
    assert result['metrics']['requests_total'] is None
    assert result['metrics']['avg_latency_ms'] is None


def upstream(tag, name, requests, failed, elapsed, count):
    values = {'query_total': requests, 'error_total': failed,
              'latency_sum_ms': elapsed, 'latency_count': count}
    return '\n'.join(
        f'forward_upstream_{field}{{plugin_tag="{tag}",upstream="{name}"}} {value}'
        for field, value in values.items())


def test_upstreams_keep_attempts_separate_from_listener_requests():
    text = '\n'.join([
        server('dns', 'udp', 8, 0, 40, 8),
        upstream('remote', 'https://dns.example/dns-query', 10, 2, 120, 6),
        upstream('local', 'udp://192.0.2.1', 5, 0, 25, 5),
        'forward_upstream_timeout_total{plugin_tag="remote",upstream="https://dns.example/dns-query"} 2',
    ])
    result = metrics.summarize_metrics(text)
    assert result['metrics']['requests_total'] == 8
    assert result['upstreams'] == [
        {'tag': 'remote', 'upstream': 'https://dns.example/dns-query', 'requests': 10,
         'failed': 2, 'avg_latency_ms': 20},
        {'tag': 'local', 'upstream': 'udp://192.0.2.1', 'requests': 5,
         'failed': 0, 'avg_latency_ms': 5},
    ]
    assert metrics.summarize_metrics(text + '\n' + text)['upstreams'] == result['upstreams']


def test_reset_uses_raw_weighted_differences_and_keeps_live_gauges():
    import copy
    import json
    baseline = metrics.parse_metrics('\n'.join([
        server('dns', 'udp', 100, 1, 1000, 100, 2),
        cache('cache', 100, 50, 10, 30, 10, 99),
        upstream('forward', 'dns', 50, 5, 5000, 50),
    ]))
    baseline = json.loads(json.dumps(baseline))  # identical after persistence
    current = metrics.parse_metrics('\n'.join([
        server('dns', 'udp', 110, 3, 1300, 110, 4),
        cache('cache', 120, 65, 12, 32, 11, 105),
        upstream('forward', 'dns', 54, 6, 5800, 54),
    ]))
    original = copy.deepcopy(current)
    delta, partial = metrics.apply_metrics_baseline(current, baseline)
    result = metrics.summarize_observations(delta)
    assert not partial
    assert current == original
    assert result['metrics'] == {'requests_total': 10, 'failed_total': 2,
        'avg_latency_ms': 30, 'inflight': 4, 'cache_hit_rate': 85}
    assert result['caches'][0]['entries'] == 105
    assert result['upstreams'][0]['requests'] == 4
    assert result['upstreams'][0]['avg_latency_ms'] == 200
    assert result['upstreams'][0]['failed'] == 1


def test_reset_to_same_sample_has_zero_counters_and_unknown_ratios():
    snapshot = metrics.parse_metrics(server('s', 'udp', 100, 0, 1000, 100, 7) + '\n' + cache('c', 5, 2, 1, entries=20))
    adjusted, partial = metrics.apply_metrics_baseline(snapshot, snapshot)
    result = metrics.summarize_observations(adjusted)
    assert not partial
    assert result['metrics']['requests_total'] == 0
    assert result['metrics']['avg_latency_ms'] is None
    assert result['metrics']['cache_hit_rate'] is None
    assert result['metrics']['inflight'] == 7
    assert result['caches'][0]['entries'] == 20


def test_new_or_missing_series_are_partial_not_fabricated_from_zero():
    baseline = metrics.parse_metrics(server('old', 'udp', 100, 2, 500, 100))
    current = metrics.parse_metrics(server('new', 'udp', 5, 1, 20, 5, 2))
    adjusted, partial = metrics.apply_metrics_baseline(current, baseline)
    result = metrics.summarize_observations(adjusted)
    assert partial
    assert result['metrics']['requests_total'] is None
    assert result['metrics']['avg_latency_ms'] is None
    assert result['metrics']['inflight'] == 2


def test_plugin_counter_reset_reports_new_lifetime_instead_of_negative():
    baseline = metrics.parse_metrics(upstream('f', 'dns', 100, 5, 1000, 100))
    current = metrics.parse_metrics(upstream('f', 'dns', 3, 0, 15, 3))
    adjusted, partial = metrics.apply_metrics_baseline(current, baseline)
    result = metrics.summarize_observations(adjusted)
    assert partial
    assert result['upstreams'][0]['requests'] == 3
    assert result['upstreams'][0]['avg_latency_ms'] == 5


def test_missing_latency_counter_does_not_become_average_of_averages():
    baseline = metrics.parse_metrics(upstream('f', 'dns', 20, 2, 'NaN', 18))
    current = metrics.parse_metrics(upstream('f', 'dns', 25, 3, 1000, 22))
    adjusted, partial = metrics.apply_metrics_baseline(current, baseline)
    assert partial
    result = metrics.summarize_observations(adjusted)
    assert result['upstreams'][0]['requests'] == 5
    assert result['upstreams'][0]['avg_latency_ms'] is None


def test_upstream_identity_is_group_and_name_not_name_alone():
    result = metrics.summarize_metrics('\n'.join([
        upstream('fallback', 'dns', 1, 0, 10, 1),
        upstream('remote', 'dns', 3, 1, 12, 2),
        'forward_upstream_query_total{plugin_tag="missing_name"} 99',
    ]))
    assert len(result['upstreams']) == 2
    assert [row['requests'] for row in result['upstreams']] == [3, 1]
    assert [row['avg_latency_ms'] for row in result['upstreams']] == [6, 10]



def test_counter_reset_detection_excludes_gauges_and_missing_series():
    baseline = metrics.parse_metrics(server('s', 'udp', 100, 0, 1000, 100, 7) + '\n' + cache('c', 5, 2, 1, entries=20))
    gauges_changed = metrics.parse_metrics(server('s', 'udp', 101, 0, 1010, 101, 0) + '\n' + cache('c', 5, 2, 1, entries=0))
    assert not metrics.metrics_counters_restarted(gauges_changed, baseline)
    assert not metrics.metrics_counters_restarted(metrics.parse_metrics(''), baseline)
    assert metrics.metrics_counters_restarted(metrics.parse_metrics(server('s', 'udp', 1, 0, 10, 1)), baseline)
