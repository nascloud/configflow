"""Actual dependency capability and quick-setup syntax, not just tag presence."""
import copy

import pytest

from backend.common.oxidns_validation import schemas, validate_native_config
from backend.common.oxidns_reference_validation import collect_reference_issues


def reference_issues(plugins):
    return collect_reference_issues({'plugins': plugins}, schemas()[1])


def errors(issues):
    return [item for item in issues if item.get('severity', 'error') == 'error']


def sequence(expr, matches=None):
    rule = {'exec': expr}
    if matches is not None:
        rule['matches'] = matches
    return {'tag': 'main', 'type': 'sequence', 'args': [rule]}


@pytest.mark.parametrize('plugin,path', [
    ({'tag': 'x', 'type': 'udp_server', 'args': {'entry': 'missing'}}, ['entry']),
    ({'tag': 'x', 'type': 'tcp_server', 'args': {'entry': 'missing'}}, ['entry']),
    ({'tag': 'x', 'type': 'quic_server', 'args': {'entry': 'missing'}}, ['entry']),
    ({'tag': 'x', 'type': 'http_server', 'args': {'entries': [{'exec': 'missing'}]}}, ['entries', 0, 'exec']),
    ({'tag': 'x', 'type': 'fallback', 'args': {'primary': 'missing'}}, ['primary']),
    ({'tag': 'x', 'type': 'prefer_ipv4', 'args': {'probe_executor': 'missing'}}, ['probe_executor']),
    ({'tag': 'x', 'type': 'prefer_ipv6', 'args': {'probe_executor': 'missing'}}, ['probe_executor']),
    ({'tag': 'x', 'type': 'learn_domain', 'args': {'provider': 'missing'}}, ['provider']),
    ({'tag': 'x', 'type': 'domain_set', 'args': {'sets': ['missing']}}, ['sets', 0]),
    ({'tag': 'x', 'type': 'ip_set', 'args': {'sets': ['missing']}}, ['sets', 0]),
    ({'tag': 'x', 'type': 'qname', 'args': '$missing'}, []),
    ({'tag': 'x', 'type': 'cname', 'args': ['$missing']}, []),
    ({'tag': 'x', 'type': 'client_ip', 'args': '$missing'}, []),
    ({'tag': 'x', 'type': 'ptr_ip', 'args': '$missing'}, []),
    ({'tag': 'x', 'type': 'resp_ip', 'args': '$missing'}, []),
    ({'tag': 'x', 'type': 'question', 'args': '$missing'}, []),
    ({'tag': 'x', 'type': 'reload_provider', 'args': '$missing'}, []),
    ({'tag': 'x', 'type': 'any_match', 'args': ['!$missing']}, [0]),
    ({'tag': 'x', 'type': 'cron', 'args': {'jobs': [{'executors': ['missing']}]}}, ['jobs', 0, 'executors', 0]),
])
def test_missing_reference_paths(plugin, path):
    issues = reference_issues([plugin])
    assert any(item['path'] == ['plugins', 0, 'args', *path] and item['code'] == 'reference' for item in issues)


@pytest.mark.parametrize('source,target,code', [
    ({'type': 'udp_server', 'args': {'entry': 'target'}}, {'type': 'domain_set'}, 'reference-kind'),
    ({'type': 'learn_domain', 'args': {'provider': 'target'}}, {'type': 'domain_set'}, 'reference-type'),
    ({'type': 'qname', 'args': '$target'}, {'type': 'ip_set'}, 'reference-capability'),
    ({'type': 'client_ip', 'args': '$target'}, {'type': 'domain_set'}, 'reference-capability'),
    ({'type': 'domain_set', 'args': {'sets': ['target']}}, {'type': 'ip_set'}, 'reference-capability'),
    ({'type': 'ip_set', 'args': {'sets': ['target']}}, {'type': 'geosite'}, 'reference-capability'),
    ({'type': 'sequence', 'args': [{'exec': 'goto target'}]}, {'type': 'cache'}, 'reference-type'),
    ({'type': 'sequence', 'args': [{'exec': 'accept', 'matches': '$target'}]}, {'type': 'cache'}, 'reference-kind'),
])
def test_kind_and_provider_capability_are_checked(source, target, code):
    problems = reference_issues([{'tag': 'source', **source}, {'tag': 'target', **target}])
    assert any(item['code'] == code for item in problems)


def test_valid_reference_capabilities_and_unknown_target_warning():
    plugins = [
        {'tag': 'domains', 'type': 'geosite'}, {'tag': 'addresses', 'type': 'geoip'},
        {'tag': 'dynamic', 'type': 'dynamic_domain_set'},
        {'tag': 'd', 'type': 'domain_set', 'args': {'sets': ['domains', 'dynamic']}},
        {'tag': 'i', 'type': 'ip_set', 'args': {'sets': ['addresses']}},
        {'tag': 'future', 'type': 'future_executor'},
        sequence('$future', ['qname $d', 'client_ip $i', 'question $domains']),
    ]
    before = copy.deepcopy(plugins)
    problems = reference_issues(plugins)
    assert errors(problems) == []
    assert any(item['code'] == 'unknown-reference' for item in problems)
    assert plugins == before


def test_duplicate_tags_self_reference_and_dependency_cycles():
    duplicate = reference_issues([sequence('accept'), sequence('accept')])
    assert any(item['code'] == 'duplicate-tag' for item in duplicate)
    self_cycle = reference_issues([sequence('$main')])
    assert any(item['code'] == 'dependency-cycle' for item in self_cycle)
    cycle = reference_issues([
        {'tag': 'a', 'type': 'domain_set', 'args': {'sets': ['b']}},
        {'tag': 'b', 'type': 'domain_set', 'args': {'sets': ['a']}},
    ])
    assert any(item['code'] == 'dependency-cycle' for item in cycle)


@pytest.mark.parametrize('expr', [
    '', '$', 'jump', 'jump $main', 'goto missing', 'reject 16', 'reject BADVERS',
    'reject 0 1', 'mark', 'set_mark -1', 'mark 4294967296',
    'cache 1000', 'cache short_circuit=maybe', 'forward', 'forward udp://',
    'forward 1.1.1.1 short_circuit=maybe', 'black_hole custom',
    'black_hole nxdomain 1.1.1.1', 'black_hole not-an-ip',
    'ttl', 'ttl -1', 'ttl 1-4294967296', 'sleep', 'sleep -1', 'sleep 1h30m',
    'forward_edns0opt 65536', 'client_ip_from_ecs not-an-ip',
    'ipset name,inet,33', 'ipset invalid', 'nftset inet,table,set,ipv4_addr,33',
    'nftset invalid', 'ip_selector first_success tcp:0', 'ip_selector none,ping',
    'download https://example.test/file', 'upgrade force=maybe', 'upgrade bundle=bad',
    'upgrade unknown=secret', 'http_request GET https://example.test',
])
def test_invalid_executor_expressions(expr):
    issues = reference_issues([sequence(expr)])
    assert errors(issues), expr
    assert all(item['path'] == ['plugins', 0, 'args', 0, 'exec'] for item in issues)


@pytest.mark.parametrize('expr', [
    'accept', 'return', 'reject', 'reject REFUSED', 'reject 0', 'mark 1,2 3', 'set_mark 0',
    'cache', 'cache short_circuit=false', 'forward 1.1.1.1 tls://dns.example short_circuit',
    'black_hole', 'black_hole nodata', 'black_hole null', 'black_hole custom 0.0.0.0 ::',
    'ttl 300', 'ttl 300-10', 'sleep 5ms', 'sleep 0', 'ecs_handler ignored-value',
    'prefer_ipv4 ignored-value', 'prefer_ipv6', 'drop_resp ignored-value', 'reload ignored-value',
    'query_summary message', 'debug_print message', 'metrics_collector metric',
    'ipset', 'ipset name,inet,0', 'nftset', 'nftset inet,table,set,ipv6_addr,128',
    'ip_selector', 'ip_selector background tcp:443,ping', 'ip_selector first_success none',
    'forward_edns0opt', 'forward_edns0opt 1,65535', 'client_ip_from_ecs 10.0.0.0/8',
    'download https://example.test/file /tmp/dir with spaces', 'upgrade force bundle=full',
])
def test_valid_executor_expressions(expr):
    assert errors(reference_issues([sequence(expr)])) == [], expr


@pytest.mark.parametrize('expr', [
    '', '!', '_true unwanted', '_false unwanted', 'has_resp unwanted', 'has_wanted_ans unwanted',
    'qtype', 'qtype NOT_A_QTYPE', 'qclass 65536', 'rcode invalid', 'mark -1',
    'random 1.1', 'env =secret', 'string_exp invalid eq value', 'string_exp qname zl value',
    'time 25:00-26:00', 'time 00:00-01:00 02:00-03:00', 'rate_limiter 0',
    'rate_limiter 1 0', 'rate_limiter 1 1 33', 'qname $missing', 'question full:example.test',
])
def test_invalid_matcher_quick_setup(expr):
    assert errors(reference_issues([sequence('accept', expr)])), expr


@pytest.mark.parametrize('expr', [
    '_true', '!_false', 'has_resp', 'has_wanted_ans', 'qtype A,AAAA', 'qclass IN',
    'rcode NOERROR', 'mark 0,1', 'random 0.5', 'env HOME KEY=value',
    'string_exp qname suffix example.test', 'string_exp $ENVzl',
    'time 23:00-02:00', 'rate_limiter', 'rate_limiter 1 1 32 128 extra-ignored',
    'qname full:example.test', 'cname domain:example.test', 'client_ip 10.0.0.0/8',
    'resp_ip 1.1.1.1', 'ptr_ip ::1',
])
def test_valid_matcher_quick_setup(expr):
    assert errors(reference_issues([sequence('accept', expr)])) == [], expr


def test_cron_bare_tag_and_known_quick_setup_and_provider_dependency():
    plugins = [sequence('accept'), {'tag': 'provider', 'type': 'domain_set'},
               {'tag': 'job', 'type': 'cron', 'args': {'jobs': [{'executors': [
                   'main', '$main', 'reload_provider $provider', 'download https://example.test/file /tmp',
               ]}]}}]
    assert errors(reference_issues(plugins)) == []


def test_unknown_expression_and_environment_references_remain_unchanged():
    document = {'plugins': [sequence('future_exec unknown-data', '${MATCHER_EXPR}')]}
    before = copy.deepcopy(document)
    issues = validate_native_config(document)
    assert errors(issues) == []
    assert any(item['code'] == 'unknown-expression' and item['severity'] == 'warning' for item in issues)
    assert document == before


@pytest.mark.parametrize('bad_type', [[], {}, None, True, 1])
def test_malformed_plugin_type_and_reference_never_raise(bad_type):
    document = {'plugins': [{'tag': 'bad', 'type': bad_type}, sequence('$bad')]}
    assert errors(validate_native_config(document))


@pytest.mark.parametrize('expr,matcher', [
    ('mark ' + '9' * 5000, False), ('ipset set,inet,' + '9' * 5000, False),
    ('nftset inet,table,set,ipv4_addr,' + '9' * 5000, False),
    ('rate_limiter 1 ' + '9' * 5000, True),
    ('qtype ' + '9' * 5000, True),
])
def test_oversized_numeric_quick_parameters_report_issues(expr, matcher):
    item = sequence('accept', expr) if matcher else sequence(expr)
    assert errors(validate_native_config({'plugins': [item]}))
