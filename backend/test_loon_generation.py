"""Loon config generation, public URL access and Loon-format subscription delivery."""
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask

from backend.common import config as config_store
from backend.common.config_repository import ProfileRepository
from backend.converters.loon import convert_node_to_loon, generate_loon_config
from backend.routes import register_blueprints
from backend.utils.dialer_references import DialerReferenceError
from backend.utils.strategy_references import StrategyReferenceError
from backend.utils.subscription_cache import save_subscription_nodes


@pytest.fixture
def loon_app(tmp_path):
    repository = ProfileRepository(tmp_path)
    repository.save_shared({
        'subscriptions': [{'id': 'sub', 'name': 'Feed', 'url': '', 'enabled': True}],
        'nodes': [{'id': 'manual', 'name': 'Home', 'type': 'trojan', 'server': 'home.test', 'port': 443,
                   'params': {'password': 'secret', 'sni': 'home.test'}, 'enabled': True}],
        'subscription_aggregations': [],
        'rule_library': [
            {'id': 'library', 'name': 'Shared Rules', 'source_type': 'content',
             'behavior': 'domain', 'content': 'DOMAIN,example.com', 'enabled': True},
            {'id': 'yaml', 'name': 'Yaml Rules', 'url': 'https://rules.test/a.yaml',
             'behavior': 'domain', 'enabled': True},
        ],
    })
    repository.save_profile('default', {
        'proxy_groups': [
            {'id': 'hk', 'name': 'HK', 'type': 'url-test', 'subscriptions': ['sub'], 'regex': '(?i)(港|HK)'},
            {'id': 'group', 'name': 'Proxy', 'type': 'select', 'manual_nodes': ['manual', 'DIRECT'],
             'include_groups': ['hk'], 'subscriptions': ['sub']},
        ],
        'rule_configs': [
            {'id': 'inline', 'itemType': 'rule', 'rule_type': 'DST-PORT', 'value': '22',
             'policy': 'DIRECT', 'enabled': True},
            {'id': 'process', 'itemType': 'rule', 'rule_type': 'PROCESS-NAME', 'value': 'curl',
             'policy': 'Proxy', 'enabled': True},
            {'id': 'ruleset', 'itemType': 'ruleset', 'library_rule_id': 'library', 'policy': 'Proxy', 'enabled': True},
            {'id': 'yaml', 'itemType': 'ruleset', 'library_rule_id': 'yaml', 'policy': 'Proxy', 'enabled': True},
            {'id': 'final', 'itemType': 'rule', 'rule_type': 'MATCH', 'value': '', 'policy': 'Proxy', 'enabled': True},
        ],
        'loon': {'custom_config': '[General]\nip-mode = ipv4-only\n\n[MITM]\nhostname = a.test\n'},
    })
    repository.update_system_transaction(lambda system: system['system_config'].update({
        'server_domain': 'https://config.example', 'config_token': 'global+token&secret',
    }))
    config_store.set_repository(repository)
    save_subscription_nodes('sub', [{'id': 'cached', 'name': 'HK 01', 'type': 'ss', 'server': 'hk.test',
                                     'port': 8388, 'params': {'cipher': 'aes-128-gcm', 'password': 'pw'},
                                     'enabled': True}])
    app = Flask(__name__)
    app.config['TESTING'] = True
    register_blueprints(app)
    yield app, repository
    config_store.reset_config_context()


def _section(config, name):
    lines = config.splitlines()
    start = lines.index(f'[{name}]') + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith('[')), len(lines))
    return [line for line in lines[start:end] if line.strip()]


def test_loon_config_sections(loon_app):
    _app, repository = loon_app
    config = generate_loon_config(repository.get_compat_config('default'))

    assert _section(config, 'General') == ['ip-mode = ipv4-only']
    assert _section(config, 'MITM') == ['hostname = a.test']
    assert _section(config, 'Proxy') == ['Home = Trojan,home.test,443,"secret",transport=tcp,sni=home.test,'
                                         'skip-cert-verify=false,fast-open=false,udp=false']

    remote = _section(config, 'Remote Proxy')
    assert len(remote) == 1 and remote[0].startswith('Feed = https://config.example/api/profiles/default/subscriptions/sub/proxies?')
    assert remote[0].endswith(',udp=default,block-quic=default,fast-open=default,'
                              'vmess-aead=default,skip-cert-verify=default,enabled=true')
    query = parse_qs(urlsplit(remote[0].split(' = ', 1)[1].split(',')[0]).query)
    assert query == {'format': ['loon'], 'token': ['global+token&secret']}

    assert _section(config, 'Remote Filter') == ['HK-筛选 = NameRegex,Feed,FilterKey="(?i)(港|HK)"']
    groups = _section(config, 'Proxy Group')
    assert groups[0].startswith('HK = url-test,HK-筛选,url=')
    assert groups[1] == 'Proxy = select,Home,DIRECT,HK,Feed'

    rules = _section(config, 'Rule')
    assert rules[0] == 'DEST-PORT,22,DIRECT'
    assert rules[1].startswith('# ') and 'PROCESS-NAME' in rules[1]
    assert rules[-1] == 'FINAL,Proxy'

    remote_rules = _section(config, 'Remote Rule')
    assert remote_rules[0] == ('https://config.example/api/profiles/default/rule-library/content/library,'
                               'policy=Proxy,tag=Shared Rules,enabled=true')
    assert remote_rules[1].startswith('# ') and 'a.yaml' in remote_rules[1]


def test_loon_rejects_unknown_policy_and_chains(loon_app):
    _app, repository = loon_app
    snapshot = repository.get_compat_config('default')
    snapshot['rule_configs'][0]['policy'] = 'Missing'
    with pytest.raises(StrategyReferenceError):
        generate_loon_config(snapshot)

    from backend.utils.dialer_references import reject_surge_dialers
    with pytest.raises(DialerReferenceError, match='^Loon 暂不支持代理链'):
        reject_surge_dialers({'proxy_groups': [{'id': 'chain', 'type': 'chain'}]}, client='Loon')


def test_loon_public_url_requires_token(loon_app):
    app, _repository = loon_app
    client = app.test_client()
    assert client.get('/api/config/default/loon').status_code == 401
    response = client.get('/api/config/default/loon', query_string={'token': 'global+token&secret'})
    assert response.status_code == 200
    assert '[Remote Proxy]' in response.get_data(as_text=True)


def test_loon_url_names_config_after_profile(loon_app):
    app, repository = loon_app
    repository.update_profile('default', {'name': '家里/iPad'})
    client = app.test_client()
    url = '/api/config/default/loon/%E5%AE%B6%E9%87%8C%20iPad.lcf'
    assert client.get(url).status_code == 401
    response = client.get(url, query_string={'token': 'global+token&secret'})
    assert response.status_code == 200
    assert '[Remote Proxy]' in response.get_data(as_text=True)
    # 名称中的非法文件名字符被替换
    assert response.headers['Content-Disposition'] == "inline; filename*=UTF-8''%E5%AE%B6%E9%87%8C%20iPad.lcf"
    assert client.get('/api/config/default/loon/a/b.lcf', query_string={'token': 'global+token&secret'}).status_code == 404


def test_subscription_serves_loon_nodes(loon_app):
    app, _repository = loon_app
    response = app.test_client().get('/api/profiles/default/subscriptions/sub/proxies',
                                     query_string={'format': 'loon', 'token': 'global+token&secret'})
    assert response.status_code == 200
    assert response.get_data(as_text=True) == 'HK 01 = Shadowsocks,hk.test,8388,aes-128-gcm,"pw",fast-open=false,udp=false'


def test_custom_loon_config_round_trip(loon_app):
    app, repository = loon_app
    client = app.test_client()
    with app.test_request_context():
        from backend.common.auth import generate_token
        token = generate_token('admin')
    headers = {'Authorization': f'Bearer {token}'}
    assert client.post('/api/custom-config/loon', json={'config': '[General]\nipv6 = false'}, headers=headers).status_code == 200
    assert repository.get_profile('default')['loon']['custom_config'] == '[General]\nipv6 = false'


def test_unsupported_transports_are_skipped():
    node = {'name': 'grpc', 'type': 'vless', 'server': 'a.test', 'port': 443,
            'params': {'uuid': 'u', 'network': 'grpc'}}
    assert convert_node_to_loon(node) is None
    assert convert_node_to_loon({'name': 't', 'type': 'tuic', 'server': 'a.test', 'port': 443}) is None


def test_default_general_keeps_fake_ip_range_in_tun():
    from backend.converters.loon import DEFAULT_GENERAL
    bypass = next(line for line in DEFAULT_GENERAL if line.startswith('bypass-tun'))
    assert '198.18.0.0/15' not in bypass


def test_logical_rules_with_unsupported_subrules_are_commented():
    from backend.converters.loon import _build_rules
    config = {'rule_configs': [
        {'itemType': 'rule', 'rule_type': 'AND', 'value': '((DOMAIN,a.test),(DST-PORT,443))', 'policy': 'DIRECT'},
        {'itemType': 'rule', 'rule_type': 'OR', 'value': '((PROCESS-NAME,curl),(DOMAIN,b.test))', 'policy': 'DIRECT'},
        {'itemType': 'ruleset', 'name': 'Apple', 'policy': 'DIRECT',
         'url': 'https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/Clash/Apple/Apple.list'},
    ]}
    rules, remote_rules = _build_rules(config, '')
    assert rules[1] == 'AND,((DOMAIN,a.test),(DEST-PORT,443)),DIRECT'
    assert rules[2].startswith('# ')
    assert remote_rules[1].startswith('https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/Loon/Apple/Apple.list,')


def test_node_formats_follow_loon_docs():
    def line(node_type, **params):
        return convert_node_to_loon({'name': 'n', 'type': node_type, 'server': 'a.test', 'port': 443, 'params': params})
    assert line('vless', uuid='u', tls=True, flow='xtls-rprx-vision', servername='s.test',
                **{'reality-opts': {'public-key': 'pk', 'short-id': 'ab'}}) == (
        'n = VLESS,a.test,443,"u",transport=tcp,flow=xtls-rprx-vision,over-tls=true,sni=s.test,'
        'skip-cert-verify=false,public-key="pk",short-id=ab,udp=false')
    assert line('anytls', password='p', sni='s.test', udp=True) == \
        'n = AnyTLS,a.test,443,"p",sni=s.test,skip-cert-verify=false,udp=true'
    assert line('vmess', uuid='u', network='ws', **{'ws-opts': {'path': '/a,b'}}).split(',transport=ws,')[1].startswith('path="/a,b"')
    assert line('hysteria2', password='p', obfs='salamander', **{'obfs-password': 'o'}).count('salamander-password=o') == 1
