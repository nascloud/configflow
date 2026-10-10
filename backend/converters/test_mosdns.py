import unittest

import yaml

from backend.converters.mosdns import (
    convert_rule_content_for_mosdns,
    generate_mosdns_config,
    get_mosdns_ruleset_downloads,
)


class MosdnsCacheConfigTest(unittest.TestCase):
    @staticmethod
    def _cache_plugin(mosdns_settings):
        generated = generate_mosdns_config({'mosdns': mosdns_settings})
        config = yaml.safe_load(generated)
        return next(
            (plugin for plugin in config['plugins'] if plugin.get('tag') == 'lazy_cache'),
            None
        )

    def test_cache_persistence_can_be_disabled(self):
        plugin = self._cache_plugin({
            'cache_enabled': True,
            'cache_dump_enabled': False,
        })

        self.assertIsNotNone(plugin)
        self.assertNotIn('dump_file', plugin['args'])
        self.assertNotIn('dump_interval', plugin['args'])

    def test_cache_persistence_defaults_remain_compatible(self):
        plugin = self._cache_plugin({'cache_enabled': True})

        self.assertEqual(plugin['args']['dump_file'], './cache.dump')
        self.assertEqual(plugin['args']['dump_interval'], 300)

    def test_cache_plugin_can_still_be_disabled(self):
        plugin = self._cache_plugin({'cache_enabled': False})

        self.assertIsNone(plugin)



MIXED_CLASH = """payload:
  - DOMAIN-SUFFIX,example.com
  - DOMAIN,full.example.org
  - IP-CIDR,1.2.3.0/24,no-resolve
  - IP-CIDR6,2001:db8::/32
  - GEOIP,CN
"""


class MosdnsRuleConversionTest(unittest.TestCase):
    def test_legacy_mode_keeps_previous_output(self):
        self.assertEqual(
            convert_rule_content_for_mosdns(MIXED_CLASH),
            'domain:example.com\nfull:full.example.org',
        )
        self.assertEqual(
            convert_rule_content_for_mosdns('+.a.com\n10.0.0.0/8'),
            'domain:a.com\n10.0.0.0/8',
        )

    def test_mixed_rules_split_into_domain_and_ip_parts(self):
        self.assertEqual(
            convert_rule_content_for_mosdns(MIXED_CLASH, 'domain'),
            'domain:example.com\nfull:full.example.org',
        )
        self.assertEqual(
            convert_rule_content_for_mosdns(MIXED_CLASH, 'ip'),
            '1.2.3.0/24\n2001:db8::/32',
        )

    def test_list_format_bare_ips_go_to_ip_part_only(self):
        content = '+.a.com\n10.0.0.0/8\nb.com'
        self.assertEqual(convert_rule_content_for_mosdns(content, 'domain'), 'domain:a.com\nfull:b.com')
        self.assertEqual(convert_rule_content_for_mosdns(content, 'ip'), '10.0.0.0/8')

    def test_empty_split_part_returns_comment_placeholder(self):
        self.assertEqual(convert_rule_content_for_mosdns('DOMAIN,a.com', 'ip'), '# no ip rules')
        self.assertEqual(convert_rule_content_for_mosdns('IP-CIDR,1.1.1.0/24', 'domain'), '# no domain rules')
        self.assertEqual(convert_rule_content_for_mosdns('IP-CIDR,1.1.1.0/24'), '')

    def test_mosdns_format_is_filtered_by_part(self):
        content = 'domain:a.com\n1.1.1.1/32\nfull:b.com'
        self.assertEqual(convert_rule_content_for_mosdns(content), content)
        self.assertEqual(convert_rule_content_for_mosdns(content, 'domain'), 'domain:a.com\nfull:b.com')
        self.assertEqual(convert_rule_content_for_mosdns(content, 'ip'), '1.1.1.1/32')


class MosdnsIpRuleConfigTest(unittest.TestCase):
    @staticmethod
    def _config(default_forward='forward_remote'):
        return {
            'system_config': {'server_domain': 'https://cf.test', 'rule_proxy_token': 'tok'},
            'mosdns': {
                'direct_rulesets': ['rs-cn', 'rs-cnip'],
                'proxy_rulesets': ['rs-mixed'],
                'direct_rules': ['r-ip'],
                'proxy_rules': [],
                'default_forward': default_forward,
            },
            'rule_configs': [
                {'id': 'rs-mixed', 'name': 'Telegram', 'itemType': 'ruleset',
                 'behavior': 'classical', 'url': 'https://r.test/tg.yaml'},
                {'id': 'rs-cn', 'name': 'CN', 'itemType': 'ruleset',
                 'behavior': 'domain', 'url': 'https://r.test/cn.txt'},
                {'id': 'rs-cnip', 'name': 'CNIP', 'itemType': 'ruleset',
                 'behavior': 'ipcidr', 'url': 'https://r.test/cnip.txt'},
                {'id': 'r-ip', 'itemType': 'rule', 'rule_type': 'IP-CIDR', 'value': '192.0.2.0/24'},
            ],
        }

    def _generate(self, default_forward='forward_remote'):
        config = yaml.safe_load(generate_mosdns_config(self._config(default_forward)))
        plugins = {plugin.get('tag'): plugin for plugin in config['plugins'] if plugin.get('tag')}
        return plugins, plugins['sequence_main']['args']

    def test_mixed_ruleset_creates_domain_and_ip_sets(self):
        plugins, _ = self._generate()
        self.assertEqual(plugins['Telegram']['type'], 'domain_set')
        self.assertEqual(plugins['Telegram']['args']['files'], ['./rules/Telegram.txt'])
        self.assertEqual(plugins['Telegram_ip']['type'], 'ip_set')
        self.assertEqual(plugins['Telegram_ip']['args']['files'], ['./rules/Telegram_ip.txt'])
        self.assertEqual(plugins['CNIP']['type'], 'ip_set')

    def test_downloads_request_split_parts(self):
        downloads = {d['local_path']: d['url'] for d in get_mosdns_ruleset_downloads(self._config())}
        self.assertIn('part=domain', downloads['./rules/Telegram.txt'])
        self.assertIn('part=ip', downloads['./rules/Telegram_ip.txt'])
        self.assertIn('part=ip', downloads['./rules/CNIP.txt'])
        self.assertNotIn('part=', downloads['./rules/CN.txt'])

    def test_ip_rules_match_response_after_domain_rules(self):
        plugins, sequence = self._generate()
        probe = sequence.index({'exec': '$forward_local'})
        domain_rules = [entry for entry in sequence if entry.get('matches', [''])[0].startswith('qname')]
        self.assertTrue(all(sequence.index(entry) < probe for entry in domain_rules))
        self.assertEqual(sequence[probe + 1:probe + 4], [
            {'matches': ['resp_ip $Telegram_ip'], 'exec': 'goto ip_requery_proxy'},
            {'matches': ['resp_ip $CNIP'], 'exec': 'accept'},
            {'matches': ['resp_ip $direct_ip_rules'], 'exec': 'accept'},
        ])
        self.assertEqual(sequence[probe + 4:], [{'exec': 'drop_resp'}, {'exec': 'goto proxy_dns_seq'}])
        self.assertEqual(plugins['ip_requery_proxy']['args'][0], {'exec': 'drop_resp'})

    def test_local_default_reuses_probe_response(self):
        _, sequence = self._generate('forward_local')
        self.assertEqual(sequence[-2:], [
            {'matches': ['has_resp'], 'exec': 'accept'},
            {'exec': 'goto china_dns'},
        ])

    def test_no_ip_rules_keeps_sequence_without_probe(self):
        config = self._config()
        config['rule_configs'] = [config['rule_configs'][1]]
        config['mosdns']['direct_rules'] = []
        sequence = yaml.safe_load(generate_mosdns_config(config))['plugins']
        main = next(p for p in sequence if p.get('tag') == 'sequence_main')['args']
        self.assertNotIn({'exec': '$forward_local'}, main)


if __name__ == '__main__':
    unittest.main()
