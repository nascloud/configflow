"""客户端配置导入：Mihomo / Surge / Loon / Shadowrocket 配置转换为配置空间。"""
import pytest
import yaml
from flask import Flask

from backend.common import config as config_store
from backend.common.config_repository import ProfileRepository
from backend.converters.loon import generate_loon_config
from backend.converters.mihomo import generate_mihomo_config
from backend.converters.surge import generate_surge_config
from backend.routes import register_blueprints
from backend.utils.config_importer import ConfigImportError, build_import, parse_client_config


MIHOMO_CONFIG = """
mixed-port: 7890
mode: rule
dns:
  enable: true
proxies:
  - {name: HK 01, type: ss, server: hk.test, port: 8388, cipher: aes-128-gcm, password: pw}
  - {name: JP 01, type: trojan, server: jp.test, port: 443, password: pw, sni: jp.test}
  - {name: Chained, type: ss, server: c.test, port: 1, cipher: aes-128-gcm, password: pw, dialer-proxy: HK 01}
proxy-providers:
  Airport:
    type: http
    url: https://sub.test/clash
    interval: 3600
proxy-groups:
  - {name: Proxy, type: select, proxies: [Auto, HK 01, DIRECT], use: [Airport]}
  - {name: Auto, type: url-test, include-all: true, filter: "HK|JP", url: http://cp.test, interval: 300, tolerance: 50}
  - {name: Relay, type: relay, proxies: [HK 01]}
rule-providers:
  google:
    type: http
    behavior: domain
    url: https://rules.test/google.yaml
  inline-ads:
    type: inline
    behavior: classical
    payload: [DOMAIN,ads.test]
  binary:
    type: http
    behavior: domain
    format: mrs
    url: https://rules.test/x.mrs
rules:
  - RULE-SET,google,Proxy
  - RULE-SET,inline-ads,REJECT
  - RULE-SET,binary,Proxy
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - AND,((DOMAIN,a.test),(NETWORK,UDP)),REJECT
  - DOMAIN,b.test,Missing
  - SUB-RULE,(NETWORK,tcp),sub
  - MATCH,Proxy
"""

SURGE_CONFIG = """
[General]
loglevel = notify
dns-server = 223.5.5.5

[Proxy]
HK = ss, hk.test, 8388, encrypt-method=aes-128-gcm, password=pw, udp-relay=true
WS = vmess, ws.test, 443, username=uuid-1, tls=true, ws=true, ws-path=/ws, ws-headers=Host:cdn.test, sni=ws.test
Block = reject
WG = wireguard, section-name=Home

[Proxy Group]
Proxy = select, HK, WS, Auto, Block, DIRECT
Auto = url-test, HK, WS, url=http://cp.test, interval=600, tolerance=30
Airport = select, policy-path=https://sub.test/surge, policy-regex-filter=HK
Smart = smart, HK, WS

[Rule]
RULE-SET,https://rules.test/Apple.list,DIRECT
DOMAIN-SET,https://rules.test/ads.txt,REJECT-TINYGIF
DOMAIN-SUFFIX,google.com,Proxy,extended-matching
DEST-PORT,22,DIRECT
URL-REGEX,^http://x,REJECT
RULE-SET,LAN,DIRECT
GEOIP,CN,DIRECT,no-resolve
FINAL,Proxy,dns-failed

[WireGuard Home]
private-key = priv
self-ip = 10.0.0.2
peer = (public-key = pub, allowed-ips = "0.0.0.0/0", endpoint = wg.test:51820)

[MITM]
hostname = a.test
"""

LOON_CONFIG = """
[General]
ip-mode = ipv4-only

[Proxy]
HK = Shadowsocks,hk.test,8388,aes-128-gcm,"pw",fast-open=false,udp=true
V = VLESS,v.test,443,"uuid-2",transport=ws,path=/v,host=cdn.test,over-tls=true,sni=v.test,skip-cert-verify=false

[Remote Proxy]
Airport = https://sub.test/loon,udp=true,enabled=true

[Remote Filter]
HKFilter = NameRegex, Airport, FilterKey = "(?i)(港|HK)"

[Proxy Group]
Proxy = select,HK,V,Auto,DIRECT
Auto = url-test,HKFilter,url=http://cp.test,interval=600

[Rule]
DOMAIN-SUFFIX,apple.com,DIRECT
FINAL,Proxy

[Remote Rule]
https://rules.test/Netflix.list, policy=Proxy, tag=Netflix, enabled=true
https://rules.test/Off.list, policy=Proxy, tag=Off, enabled=false

[MITM]
hostname = b.test
"""

SHADOWROCKET_CONFIG = """
[General]
bypass-system = true

[Proxy Group]
HK = url-test,url=http://cp.test,interval=600,policy-regex-filter=港|HK
Media = select,HK,DIRECT

[Rule]
DOMAIN-SUFFIX,netflix.com,Media
DOMAIN-KEYWORD,google,PROXY
FINAL,PROXY
"""


@pytest.fixture
def repository(tmp_path):
    repository = ProfileRepository(tmp_path)
    repository.save_shared({
        'subscriptions': [{'id': 'existing-sub', 'name': 'Existing', 'url': 'https://sub.test/clash',
                           'type': 'universal', 'enabled': True}],
        'nodes': [{'id': 'existing-hk', 'name': 'HK 01', 'type': 'ss', 'server': 'other.test', 'port': 1,
                   'params': {'cipher': 'aes-128-gcm', 'password': 'x'}, 'enabled': True}],
        'rule_library': [{'id': 'existing-lib', 'name': 'google', 'url': 'https://rules.test/other.yaml',
                          'behavior': 'domain', 'enabled': True}],
    })
    config_store.set_repository(repository)
    yield repository
    config_store.reset_config_context()


@pytest.fixture
def client(repository):
    repository.update_system_transaction(lambda system: system['system_config'].update({'config_token': 'token'}))
    app = Flask(__name__)
    app.config['TESTING'] = True
    register_blueprints(app)
    return app.test_client()


def _import(repository, client_type, content, name='Imported', **kwargs):
    plan = parse_client_config(client_type, content)
    return repository.import_client_profile(
        {'id': kwargs.pop('profile_id', 'imported'), 'name': name},
        lambda shared: build_import(plan, shared, kwargs.get('default_subscription_ids')),
    )


def _groups(repository, profile_id='imported'):
    return {group['name']: group for group in repository.get_profile(profile_id)['proxy_groups']}


def test_mihomo_import_maps_resources_groups_and_rules(repository):
    result = _import(repository, 'mihomo', MIHOMO_CONFIG)
    shared = repository.get_shared()
    nodes = {node['name']: node for node in shared['nodes']}
    # 与已有节点重名但定义不同：改名后新增
    assert 'HK 01 (2)' in nodes and nodes['HK 01']['id'] == 'existing-hk'
    assert 'dialer-proxy' not in nodes['Chained']['params']
    # 同 URL 订阅复用
    assert [sub['id'] for sub in shared['subscriptions']] == ['existing-sub']
    library = {item['name']: item for item in shared['rule_library']}
    assert library['google_2']['url'] == 'https://rules.test/google.yaml'
    assert library['inline-ads']['source_type'] == 'content'
    assert 'binary' not in library

    groups = _groups(repository)
    assert set(groups) == {'Proxy', 'Auto'}
    proxy = groups['Proxy']
    assert proxy['subscriptions'] == ['existing-sub']
    assert [item['type'] for item in proxy['proxies_order']] == ['strategy', 'node', 'node']
    assert proxy['manual_nodes'][-1] == 'DIRECT'
    auto = groups['Auto']
    assert auto['regex'] == 'HK|JP' and auto['subscriptions'] == ['existing-sub']
    assert auto['tolerance'] == 50 and auto['interval'] == 300
    # include-all 时按 filter 预筛手动节点
    assert {nodes_by_id['name'] for nodes_by_id in shared['nodes'] if nodes_by_id['id'] in auto['manual_nodes']} == {'HK 01 (2)', 'JP 01'}

    rules = repository.get_profile('imported')['rule_configs']
    assert [rule.get('rule_type', 'RULE-SET') for rule in rules] == ['RULE-SET', 'RULE-SET', 'IP-CIDR', 'AND', 'MATCH']
    assert rules[2]['no_resolve'] is True
    assert rules[3]['value'] == '((DOMAIN,a.test),(NETWORK,UDP))'

    custom = yaml.safe_load(repository.get_profile('imported')['mihomo']['custom_config'])
    assert custom['mixed-port'] == 7890 and 'proxies' not in custom and 'rules' not in custom
    warnings = '\n'.join(result['warnings'])
    for expected in ('relay', 'mrs', 'SUB-RULE', 'Missing', 'dialer-proxy', 'HK 01 → HK 01 (2)'):
        assert expected in warnings

    generated = yaml.safe_load(generate_mihomo_config(repository.get_compat_config('imported')))
    assert 'MATCH,Proxy' in generated['rules']
    assert generated['mixed-port'] == 7890


def test_surge_import_parses_proxies_groups_rules_and_custom_sections(repository):
    result = _import(repository, 'surge', SURGE_CONFIG)
    nodes = {node['name']: node for node in repository.get_shared()['nodes']}
    assert nodes['HK']['params'] == {'cipher': 'aes-128-gcm', 'password': 'pw', 'udp': True}
    ws = nodes['WS']['params']
    assert ws['uuid'] == 'uuid-1' and ws['network'] == 'ws'
    assert ws['ws-opts'] == {'path': '/ws', 'headers': {'Host': 'cdn.test'}} and ws['servername'] == 'ws.test'
    assert nodes['WG']['type'] == 'wireguard' and nodes['WG']['server'] == 'wg.test'
    assert nodes['WG']['port'] == 51820 and nodes['WG']['params']['private-key'] == 'priv'

    groups = _groups(repository)
    assert groups['Proxy']['manual_nodes'][-2:] == ['REJECT', 'DIRECT']
    assert groups['Auto']['type'] == 'url-test' and groups['Auto']['url'] == 'http://cp.test'
    airport = groups['Airport']
    sub = next(s for s in repository.get_shared()['subscriptions'] if s['id'] == airport['subscriptions'][0])
    assert sub['url'] == 'https://sub.test/surge' and airport['regex'] == 'HK'
    assert groups['Smart']['type'] == 'url-test'

    profile = repository.get_profile('imported')
    rule_types = [rule.get('rule_type', 'RULE-SET') for rule in profile['rule_configs']]
    assert rule_types == ['RULE-SET', 'RULE-SET', 'DOMAIN-SUFFIX', 'DST-PORT', 'GEOIP', 'MATCH']
    assert profile['rule_configs'][1]['policy'] == 'REJECT'
    library = {item['name']: item for item in repository.get_shared()['rule_library']}
    assert library['ads']['behavior'] == 'domain' and library['Apple']['behavior'] == 'classical'
    assert '[MITM]' in profile['surge']['custom_config'] and '[WireGuard' not in profile['surge']['custom_config']
    assert any('URL-REGEX' in warning for warning in result['warnings'])
    assert any('LAN' in warning for warning in result['warnings'])

    generated = generate_surge_config(repository.get_compat_config('imported'))
    assert 'hostname = a.test' in generated and 'FINAL,Proxy' in generated


def test_loon_import_maps_remote_sections(repository):
    _import(repository, 'loon', LOON_CONFIG)
    shared = repository.get_shared()
    nodes = {node['name']: node for node in shared['nodes']}
    assert nodes['HK']['params']['password'] == 'pw' and nodes['HK']['params']['cipher'] == 'aes-128-gcm'
    assert nodes['V']['params']['ws-opts'] == {'path': '/v', 'headers': {'Host': 'cdn.test'}}
    assert nodes['V']['params']['tls'] is True

    groups = _groups(repository)
    auto = groups['Auto']
    sub = next(s for s in shared['subscriptions'] if s['name'] == 'Airport')
    assert auto['subscriptions'] == [sub['id']] and auto['regex'] == '(?i)(港|HK)'

    profile = repository.get_profile('imported')
    assert [rule.get('rule_type', 'RULE-SET') for rule in profile['rule_configs']] == ['DOMAIN-SUFFIX', 'RULE-SET', 'MATCH']
    assert 'Netflix' in {item['name'] for item in shared['rule_library']}
    assert 'Off' not in {item['name'] for item in shared['rule_library']}
    assert 'hostname = b.test' in profile['loon']['custom_config']

    generated = generate_loon_config(repository.get_compat_config('imported'))
    assert 'ip-mode = ipv4-only' in generated and 'FINAL,Proxy' in generated


def test_shadowrocket_regex_groups_use_default_subscriptions(repository):
    result = _import(repository, 'shadowrocket', SHADOWROCKET_CONFIG,
                     default_subscription_ids=['existing-sub', 'unknown'])
    groups = _groups(repository)
    assert groups['HK']['subscriptions'] == ['existing-sub'] and groups['HK']['regex'] == '港|HK'
    profile = repository.get_profile('imported')
    # 未定义的 PROXY 策略不会凭空生成
    assert [rule['rule_type'] for rule in profile['rule_configs']] == ['DOMAIN-SUFFIX']
    assert any('PROXY' in warning for warning in result['warnings'])
    assert any('Shadowrocket' in warning for warning in result['warnings'])


def test_failed_validation_writes_nothing(repository):
    before = repository.get_shared()

    def build(shared):
        return {'shared': {'nodes': [{'id': 'n', 'name': 'n', 'type': 'ss', 'enabled': True}]},
                'profile': {'proxy_groups': [{'id': 'g', 'name': 'DIRECT', 'type': 'select'}]}}

    with pytest.raises(Exception):
        repository.import_client_profile({'id': 'broken', 'name': 'Broken'}, build)
    assert repository.get_shared() == before
    assert 'broken' not in {profile['id'] for profile in repository.list_profiles()}


@pytest.mark.parametrize('client_type, content', [
    ('mihomo', 'not: [valid'),
    ('surge', 'no sections here'),
    ('unknown', '[General]'),
    ('loon', '   '),
])
def test_invalid_content_is_rejected(client_type, content):
    with pytest.raises(ConfigImportError):
        parse_client_config(client_type, content)


def test_import_route_dry_run_and_commit(client, repository):
    headers = {'Authorization': 'Bearer token'}
    payload = {'client_type': 'loon', 'content': LOON_CONFIG, 'name': '手机', 'id': 'phone', 'dry_run': True}
    response = client.post('/api/profiles/import-client', json=payload, headers=headers)
    assert response.status_code == 200
    assert response.get_json()['summary']['proxy_groups'] == 2
    assert 'phone' not in {profile['id'] for profile in repository.list_profiles()}

    payload['dry_run'] = False
    response = client.post('/api/profiles/import-client', json=payload, headers=headers)
    assert response.status_code == 201
    body = response.get_json()
    assert body['profile']['name'] == '手机' and body['summary']['nodes'] == 2

    response = client.post('/api/profiles/import-client', json=payload, headers=headers)
    assert response.status_code == 409

    response = client.post('/api/profiles/import-client', json={**payload, 'id': 'x', 'name': ' '}, headers=headers)
    assert response.status_code == 400
    response = client.post('/api/profiles/import-client',
                           json={**payload, 'id': 'x', 'content': 'nothing'}, headers=headers)
    assert response.status_code == 400
