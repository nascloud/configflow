"""Global/network/server/forward contracts shared by every editor save path."""
import copy
import json
from pathlib import Path

import pytest

from backend.common.oxidns_global_constraints import collect_global_issues
from backend.common.oxidns_validation import validate_native_config


def issue_path(issue):
    parts = issue['path']
    if isinstance(parts, str):
        return parts
    return ''.join(('[%s]' % part) if isinstance(part, int) else ('.' if index else '') + part
                   for index, part in enumerate(parts))


def errors(document):
    return [issue for issue in validate_native_config(document)
            if issue.get('severity', 'error') == 'error']


def plugin(kind, args):
    return {'plugins': [{'tag': 'main', 'type': 'sequence', 'args': [{'exec': 'accept'}]},
                        {'tag': 'subject', 'type': kind, 'args': args}]}


@pytest.mark.parametrize('document,path', [
    ({'runtime': {'worker_threads': 0}}, 'runtime.worker_threads'),
    ({'runtime': {'worker_threads': -1}}, 'runtime.worker_threads'),
    ({'runtime': {'worker_threads': True}}, 'runtime.worker_threads'),
    ({'runtime': {'worker_threads': 1.5}}, 'runtime.worker_threads'),
    ({'runtime': {'worker_threads': 18446744073709551616}}, 'runtime.worker_threads'),
    ({'runtime': None}, 'runtime'),
    ({'api': None}, 'api'),
    ({'log': None}, 'log'),
    ({'network': None}, 'network'),
    ({'log': {'level': None}}, 'log.level'),
    ({'log': {'level': 'fatal'}}, 'log.level'),
    ({'log': {'level': ' info '}}, 'log.level'),
    ({'log': {'rotation': None}}, 'log.rotation'),
    ({'log': {'rotation': {}}}, 'log.rotation.type'),
    ({'log': {'rotation': {'type': 'monthly'}}}, 'log.rotation.type'),
    ({'log': {'rotation': {'type': 'daily', 'max_files': -1}}}, 'log.rotation.max_files'),
    ({'api': {'http': ''}}, 'api.http'),
    ({'api': {'http': 'hostname:9191'}}, 'api.http'),
    ({'api': {'http': {'listen': ':9191', 'auth': {'type': 'basic', 'username': '', 'password': 'x'}}}}, 'api.http'),
    ({'api': {'http': {'listen': ':9191', 'auth': {'type': 'bearer', 'username': 'x', 'password': 'x'}}}}, 'api.http'),
    ({'api': {'http': {'listen': ':9191', 'ssl': {'cert': '/cert', 'key': None}}}}, 'api.http.ssl.key'),
    ({'api': {'http': {'listen': ':9191', 'ssl': {'require_client_cert': True}}}}, 'api.http.ssl.client_ca'),
    ({'api': {'http': {'listen': ':9191', 'webui': {'root': ''}}}}, 'api.http'),
    ({'network': {'outbound': {'default': 'absent'}}}, 'network.outbound.default'),
    ({'network': {'outbound': {'profiles': {' x': {}}}}}, 'network.outbound.profiles. x'),
    ({'network': {'outbound': {'profiles': {'x': {'resolver': 'none'}}}}}, 'network.outbound.profiles.x.resolver'),
    ({'network': {'outbound': {'profiles': {'x': {'proxy': 'http'}}}}}, 'network.outbound.profiles.x.proxy'),
    ({'network': {'outbound': {'profiles': {'x': {'unused': True}}}}}, 'network.outbound.profiles.x.unused'),
    ({'network': {'outbound': {'profiles': {'x': {'resolver': {'nameservers': [{'addr': '1.1.1.1'}], 'unused': True}}}}}}, 'network.outbound.profiles.x.resolver'),
    ({'network': {'outbound': {'profiles': {'x': {'resolver': {'nameservers': [{'addr': '1.1.1.1', 'unused': True}]}}}}}}, 'network.outbound.profiles.x.resolver'),
    ({'network': {'outbound': {'profiles': {'x': {'resolver': {'nameservers': []}}}}}}, 'network.outbound.profiles.x.resolver'),
    ({'network': {'outbound': {'profiles': {'x': {'resolver': {'nameservers': [{'addr': 'tls://dns.example'}]}}}}}}, 'network.outbound.profiles.x.resolver.nameservers[0].dial_addr'),
    ({'network': {'outbound': {'profiles': {'x': {'resolver': {'nameservers': [{'addr': '1.1.1.1'}], 'timeout': 5}}}}}}, 'network.outbound.profiles.x.resolver'),
])
def test_global_rejects_invalid_values(document, path):
    problems = errors(document)
    assert any(issue_path(issue).startswith(path) or path.startswith(issue_path(issue)) for issue in problems), problems


@pytest.mark.parametrize('document', [
    {},
    {'runtime': {}, 'api': {}, 'log': {}, 'network': {}, 'plugins': []},
    {'runtime': {'worker_threads': None}},
    {'runtime': {'worker_threads': 1}},
    {'log': {'level': 'INFO', 'file': None, 'rotation': {'type': 'weekly', 'max_files': 0}}},
    {'log': {'rotation': {'type': 'never'}}},
    {'log': {'rotation': {'type': 'never', 'max_files': 'unused extension'}}},
    {'api': {'http': None}},
    {'api': {'http': ':0'}},
    {'api': {'http': '[::]:9191'}},
    {'api': {'http': {'listen': ':9191', 'auth': None, 'ssl': {}, 'webui': None,
                      'cors': {'allowed_origins': []}}}},
    {'api': {'http': {'listen': ':9191', 'ssl': {'cert': None, 'key': None,
                                                'client_ca': None, 'require_client_cert': None},
                      'webui': {'root': './webui', 'index': None}}}},
    {'network': {'outbound': {'default': None, 'profiles': {}}}},
    {'network': {'outbound': {'default': 'direct', 'profiles': {'direct': {'resolver': ' SYSTEM ', 'proxy': ' DIRECT '}}}}},
    {'network': {'outbound': {'profiles': {'direct': {'resolver': None, 'proxy': None}}}}},
    {'network': {'outbound': {'profiles': {'direct': {'resolver': {'nameservers': [{'addr': 'tls://dns.example', 'dial_addr': '1.1.1.1'}], 'timeout': '0 ms', 'proxy': None}}}}}},
])
def test_global_preserves_legal_empty_and_optional_values(document):
    original = copy.deepcopy(document)
    assert errors(document) == []
    assert document == original


@pytest.mark.parametrize('kind,args,path', [
    ('udp_server', {}, 'args.listen'),
    ('udp_server', {'listen': ':53', 'entry': 'main', 'future': True}, None),
    ('tcp_server', {'listen': ':53', 'entry': 'main', 'idle_timeout': -1}, 'args.idle_timeout'),
    ('tcp_server', {'listen': ':53', 'entry': 'main', 'idle_timeout': 1.25}, 'args.idle_timeout'),
    ('tcp_server', {'listen': ':53', 'entry': 'main', 'idle_timeout': '1h30m'}, 'args.idle_timeout'),
    ('tcp_server', {'listen': ':53', 'entry': 'main', 'cert': '/cert'}, 'args.key'),
    ('http_server', {'listen': ':80', 'entries': None}, 'args.entries'),
    ('http_server', {'listen': ':80', 'entries': [{'path': 'dns-query', 'exec': 'main'}]}, 'args.entries[0].path'),
    ('http_server', {'listen': ':80', 'entries': [], 'enable_http3': True}, 'args.cert'),
    ('http_server', {'listen': ':80', 'entries': [{'path': '/dns', 'exec': 'main', 'json_api': 'true'}]}, 'args.entries[0].json_api'),
    ('quic_server', {'listen': ':853', 'entry': 'main', 'cert': None, 'key': '/key'}, 'args.cert'),
    ('forward', {'upstreams': []}, 'args.upstreams'),
    ('forward', {'upstreams': [{'addr': 'ftp://dns.example'}]}, 'args.upstreams[0].addr'),
    ('forward', {'upstreams': [{'addr': '1.1.1.1', 'max_conns': 0}]}, 'args.upstreams[0].max_conns'),
    ('forward', {'upstreams': [{'addr': '1.1.1.1', 'max_conns': 4097}]}, 'args.upstreams[0].max_conns'),
    ('forward', {'upstreams': [{'addr': '1.1.1.1', 'min_conns': 65}]}, 'args.upstreams[0].min_conns'),
    ('forward', {'upstreams': [{'addr': '1.1.1.1', 'min_conns': 3, 'max_conns': 2}]}, 'args.upstreams[0].min_conns'),
    ('forward', {'upstreams': [{'addr': '1.1.1.1', 'bootstrap_version': 5}]}, 'args.upstreams[0].bootstrap_version'),
    ('forward', {'upstreams': [{'addr': '1.1.1.1', 'port': 65536}]}, 'args.upstreams[0].port'),
    ('forward', {'upstreams': [{'addr': '1.1.1.1', 'so_mark': 4294967296}]}, 'args.upstreams[0].so_mark'),
    ('forward', {'upstreams': [{'addr': '1.1.1.1', 'dial_addr': 'dns.example'}]}, 'args.upstreams[0].dial_addr'),
    ('forward', {'upstreams': [{'addr': '1.1.1.1', 'socks5': 'user@proxy:1080'}]}, 'args.upstreams[0].socks5'),
    ('forward', {'upstreams': [{'addr': 'tls://dns.example', 'bootstrap': 'resolver.example:53'}]}, 'args.upstreams[0].bootstrap'),
    ('forward', {'upstreams': [{'addr': 'tls://dns.example', 'bootstrap': ':53'}]}, 'args.upstreams[0].bootstrap'),
    ('forward', {'upstreams': [{'addr': '1.1.1.1'}], 'response_selection': 'random'}, 'args.response_selection'),
    ('forward', {'upstreams': [{'addr': '1.1.1.1'}], 'response_selection': None}, 'args.response_selection'),
    ('forward', {'upstreams': [{'addr': '1.1.1.1'}], 'short_circuit': None}, 'args.short_circuit'),
    ('forward', {'upstreams': [{'addr': '1.1.1.1'}], 'concurrent': -1}, 'args.concurrent'),
])
def test_server_and_forward_arguments(kind, args, path):
    problems = errors(plugin(kind, args))
    if path is None:
        assert problems == []
    else:
        assert any(issue_path(issue).startswith('plugins[1].' + path) for issue in problems), problems


@pytest.mark.parametrize('kind,args', [
    ('tcp_server', {'listen': ':0', 'entry': 'main', 'idle_timeout': 0, 'cert': None, 'key': None}),
    ('tcp_server', {'listen': '[::]:5353', 'entry': 'main', 'idle_timeout': '5 ms'}),
    ('http_server', {'listen': ':80', 'entries': []}),
    ('http_server', {'listen': ':80', 'entries': [{'path': '/dns-query', 'exec': 'main', 'json_api': None}]}),
    ('quic_server', {'listen': ':853', 'entry': 'main', 'cert': '/cert', 'key': '/key', 'idle_timeout': None}),
    ('forward', {'upstreams': [{'addr': '1.1.1.1'}], 'concurrent': 0}),
    ('forward', {'upstreams': [{'addr': '1.1.1.1'}], 'concurrent': 100}),
    ('forward', {'upstreams': [{'addr': '1.1.1.1', 'tag': '', 'bootstrap': '', 'port': 0, 'min_conns': 0}]}),
    ('forward', {'upstreams': [{'addr': 'tls://dns.example', 'bootstrap': '', 'dial_addr': '1.1.1.1'}]}),
    ('forward', {'upstreams': [{'addr': 'https://dns.example/dns-query', 'bootstrap': '[::1]:53', 'timeout': '0s'}]}),
    ('forward', {'upstreams': [{'addr': 'tcp+pipeline://1.1.1.1', 'socks5': 'user:pass@proxy.example:1080', 'max_conns': 4096, 'min_conns': 4096}]}),
    ('forward', {'concurrent': None, 'upstreams': [{'addr': '1.1.1.1', 'outbound': None, 'bootstrap_version': None,
                                                  'timeout': None, 'idle_timeout': None, 'port': None,
                                                  'max_conns': None, 'min_conns': None, 'so_mark': None,
                                                  'enable_http3': None, 'enable_pipeline': None}]}),
])
def test_server_and_forward_legal_values_survive(kind, args):
    document = plugin(kind, args)
    original = copy.deepcopy(document)
    assert errors(document) == []
    assert document == original


def test_network_proxy_protocol_conditions_apply_to_nameservers_not_ignored_upstream_proxy():
    document = {'network': {'outbound': {'default': 'remote', 'profiles': {'remote': {
        'proxy': {'socks5': '127.0.0.1:1080'},
        'resolver': {'nameservers': [{'addr': '1.1.1.1'}], 'proxy': 'profile'},
    }}}}}
    assert any(issue_path(issue).endswith('nameservers[0].addr') for issue in errors(document))
    document['network']['outbound']['profiles']['remote']['resolver']['nameservers'][0]['addr'] = 'tcp://1.1.1.1'
    document['plugins'] = [{'tag': 'f', 'type': 'forward', 'args': {'upstreams': [{'addr': '1.1.1.1'}]}}]
    assert errors(document) == []


def test_constraints_do_not_reveal_credentials_and_resolve_all_outbound_references():
    document = {'plugins': [
        {'tag': 'a', 'type': 'forward', 'args': {'upstreams': [{'addr': '1.1.1.1', 'outbound': 'missing'}]}},
        {'tag': 'b', 'type': 'download', 'args': {'outbound': 'missing'}},
        {'tag': 'c', 'type': 'upgrade', 'args': {'outbound': 'missing'}},
        {'tag': 'd', 'type': 'http_request', 'args': {'outbound': 'missing'}},
        {'tag': 'e', 'type': 'ip_selector', 'args': {'outbound': 'missing'}},
    ]}
    issues = collect_global_issues(document)
    assert len(issues) == 5
    assert all(issue['code'] == 'reference' and 'missing' not in issue['message'] for issue in issues)


def test_global_schema_covers_every_serialized_upstream_field():
    schema = json.loads((Path(__file__).parent / 'schemas/oxidns/global.json').read_text())
    properties = schema['plugins']['forward']['properties']['upstreams']['items']['properties']
    assert set(properties) == {'tag', 'addr', 'outbound', 'dial_addr', 'port', 'bootstrap',
                               'bootstrap_version', 'socks5', 'idle_timeout', 'max_conns',
                               'min_conns', 'insecure_skip_verify', 'timeout', 'enable_pipeline',
                               'enable_http3', 'so_mark', 'bind_to_device'}
