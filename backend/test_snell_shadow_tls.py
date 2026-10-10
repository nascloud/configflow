"""Snell v4/v5 的 reuse、obfs 与 ShadowTLS 在 Surge 导入和 Surge 导出两个方向都不丢字段。

Mihomo 的 Snell 把 obfs 和 ShadowTLS 都放在 obfs-opts 里（mode: http / tls / shadow-tls）。
"""
import yaml

from backend.converters.surge import convert_node_to_surge
from backend.utils.config_importer import parse_client_config


def _import_surge_proxy(line):
    plan = parse_client_config('surge', f'[Proxy]\n{line}\n')
    assert len(plan['proxies']) == 1, plan['warnings']
    return plan['proxies'][0]


def test_surge_import_snell_shadow_tls_and_reuse():
    proxy = _import_surge_proxy(
        'HK = snell, 1.2.3.4, 443, psk=abcd, version=5, reuse=true, '
        'shadow-tls-password=stpw, shadow-tls-sni=www.apple.com, shadow-tls-version=3'
    )

    assert proxy['type'] == 'snell'
    assert proxy['version'] == 5
    assert proxy['reuse'] is True
    assert proxy['obfs-opts'] == {'mode': 'shadow-tls', 'host': 'www.apple.com', 'password': 'stpw', 'version': 3}


def test_surge_import_snell_shadow_tls_without_version_uses_mihomo_default():
    proxy = _import_surge_proxy(
        'HK = snell, 1.2.3.4, 443, psk=abcd, version=4, shadow-tls-password=stpw, shadow-tls-sni=sni.test'
    )

    assert proxy['obfs-opts'] == {'mode': 'shadow-tls', 'host': 'sni.test', 'password': 'stpw'}
    assert 'reuse' not in proxy


def test_surge_import_snell_plain_obfs_unchanged():
    proxy = _import_surge_proxy('HK = snell, 1.2.3.4, 443, psk=abcd, version=4, obfs=http, obfs-host=bing.com')

    assert proxy['obfs-opts'] == {'mode': 'http', 'host': 'bing.com'}


def _export(proxy):
    line, _ = convert_node_to_surge({'name': proxy['name'], 'proxy_string': yaml.safe_dump(proxy)})
    return line


def test_surge_export_snell_shadow_tls_from_obfs_opts():
    line = _export({'name': 'HK', 'type': 'snell', 'server': '1.2.3.4', 'port': 443, 'psk': 'abcd',
                    'version': 5, 'reuse': True, 'udp': True,
                    'obfs-opts': {'mode': 'shadow-tls', 'host': 'www.apple.com', 'password': 'stpw', 'version': 3}})

    assert line == ('HK = snell, 1.2.3.4, 443, psk=abcd, version=5, reuse=true, udp-relay=true, '
                    'shadow-tls-password=stpw, shadow-tls-sni=www.apple.com, shadow-tls-version=3')


def test_surge_export_snell_obfs_from_obfs_opts():
    line = _export({'name': 'HK', 'type': 'snell', 'server': '1.2.3.4', 'port': 443, 'psk': 'abcd',
                    'version': 4, 'obfs-opts': {'mode': 'tls', 'host': 'bing.com'}})

    assert line == 'HK = snell, 1.2.3.4, 443, psk=abcd, version=4, obfs=tls, obfs-host=bing.com'


def test_surge_round_trip_keeps_shadow_tls():
    original = ('HK = snell, 1.2.3.4, 443, psk=abcd, version=5, reuse=true, '
                'shadow-tls-password=stpw, shadow-tls-sni=www.apple.com, shadow-tls-version=3')
    assert _export(_import_surge_proxy(original)) == original
