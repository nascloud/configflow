#!/usr/bin/env python3
"""Compare ConfigFlow's native catalog with a pinned official OxiDNS checkout.

Usage: .venv/bin/python scripts/audit_oxidns_schema.py /path/to/oxidns
Requires the frontend dev dependencies and the project's PyYAML dependency.
No network access or running OxiDNS service is needed.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys

import yaml

REPO = Path(__file__).resolve().parents[1]

# YAML-facing structs, not resolved runtime structs. The two implementations of
# prefer_ipv4/prefer_ipv6 share DualSelectorConfig. Array/scalar matchers are
# checked against the docs rather than inventing object argument fields.
STRUCTS = {
    'udp_server': ('plugin/server/udp.rs', 'UdpServerConfig'),
    'tcp_server': ('plugin/server/tcp.rs', 'TcpServerConfig'),
    'http_server': ('plugin/server/http/mod.rs', 'HttpServerConfig'),
    'quic_server': ('plugin/server/quic.rs', 'QuicServerConfig'),
    'fallback': ('plugin/executor/fallback.rs', 'FallbackConfig'),
    'forward': ('plugin/executor/forward/config.rs', 'ForwardConfig'),
    'cache': ('plugin/executor/cache/mod.rs', 'CacheConfig'),
    'hosts': ('plugin/executor/hosts.rs', 'HostsConfig'),
    'arbitrary': ('plugin/executor/arbitrary.rs', 'ArbitraryConfig'),
    'response': ('plugin/executor/response.rs', 'ResponseConfig'),
    'redirect': ('plugin/executor/redirect.rs', 'RedirectConfig'),
    'ecs_handler': ('plugin/executor/ecs_handler.rs', 'EcsHandlerConfig'),
    'forward_edns0opt': ('plugin/executor/forward_edns0opt.rs', 'ForwardEdns0OptConfig'),
    'ttl': ('plugin/executor/ttl.rs', 'TtlConfig'),
    'ip_selector': ('plugin/executor/ip_selector/config.rs', 'RawIpSelectorConfig'),
    'prefer_ipv4': ('plugin/executor/dual_selector.rs', 'DualSelectorConfig'),
    'black_hole': ('plugin/executor/black_hole.rs', 'BlackHoleConfig'),
    'reverse_lookup': ('plugin/executor/reverse_lookup.rs', 'ReverseLookupConfig'),
    'query_summary': ('plugin/executor/query_summary.rs', 'QuerySummaryConfig'),
    'query_recorder': ('plugin/executor/query_recorder/model.rs', 'QueryRecorderConfig'),
    'metrics_collector': ('plugin/executor/metrics_collector.rs', 'MetricsCollectorConfig'),
    'debug_print': ('plugin/executor/debug_print.rs', 'DebugPrintConfig'),
    'sleep': ('plugin/executor/sleep.rs', 'SleepConfig'),
    'learn_domain': ('plugin/executor/learn_domain/config.rs', 'LearnDomainArgs'),
    'http_request': ('plugin/executor/http_request.rs', 'HttpRequestConfig'),
    'script': ('plugin/executor/script.rs', 'ScriptConfig'),
    'ipset': ('plugin/executor/ipset.rs', 'IpSetConfig'),
    'nftset': ('plugin/executor/nftset.rs', 'NftSetConfig'),
    'ros_address_list': ('plugin/executor/ros_address_list/config.rs', 'MikrotikConfigArgs'),
    'ros_route': ('plugin/executor/ros_route/config.rs', 'MikrotikConfigArgs'),
    'upgrade': ('plugin/executor/upgrade.rs', 'UpgradePluginConfig'),
    'download': ('plugin/executor/download.rs', 'DownloadConfig'),
    'cron': ('plugin/executor/cron.rs', 'CronConfig'),
    'time': ('plugin/matcher/time.rs', 'TimeConfig'),
    'rate_limiter': ('plugin/matcher/rate_limiter.rs', 'RateLimiterConfig'),
    'domain_set': ('plugin/provider/domain_set.rs', 'DomainSetArgs'),
    'dynamic_domain_set': ('plugin/provider/dynamic_domain_set/config.rs', 'DynamicDomainSetArgs'),
    'geosite': ('plugin/provider/geosite.rs', 'GeoSiteArgs'),
    'adguard_rule': ('plugin/provider/adguard_rule/config.rs', 'AdGuardRuleConfig'),
    'ip_set': ('plugin/provider/ip_set.rs', 'IpSetArgs'),
    'geoip': ('plugin/provider/geoip.rs', 'GeoIpArgs'),
}


def load_catalog() -> dict:
    code = """
const fs = require('fs');
const path = require('path');
const repo = process.argv[1];
const ts = require(path.join(repo, 'frontend/node_modules/typescript'));
const source = fs.readFileSync(path.join(repo, 'frontend/src/lib/oxidnsSchema.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
const m = { exports: {} };
new Function('exports', 'module', compiled)(m.exports, m);
process.stdout.write(JSON.stringify(m.exports));
"""
    return json.loads(subprocess.check_output(['node', '-e', code, str(REPO)], text=True))


def object_paths(value, prefix=''):
    if isinstance(value, dict):
        for key, child in value.items():
            path = f'{prefix}.{key}' if prefix else str(key)
            yield path
            yield from object_paths(child, path)
    elif isinstance(value, list):
        for child in value:
            yield from object_paths(child, prefix + '[]')


def schema_at(schema, path):
    for key in re.findall(r'\[\]|[^.\[\]]+', path):
        schema = schema.get('items', {}) if key == '[]' else schema.get('properties', {}).get(key, schema.get('additionalProperties', {}))
    return schema


def main():
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    official = Path(sys.argv[1]).resolve()
    docs = official / 'docs/docs/plugin-reference'
    exported = load_catalog()
    catalog = {plugin['type']: plugin for plugin in exported['oxidnsPluginCatalog']}
    actual_commit = subprocess.check_output(['git', '-C', str(official), 'rev-parse', 'HEAD'], text=True).strip()
    expected_commit = exported['oxidnsSchemaSource']['commit']
    failures = []
    if actual_commit != expected_commit:
        failures.append(f'Source commit changed: {actual_commit}; catalog pins {expected_commit}')
    inventory = set()
    for names in re.findall(r'\| \[(.*?)\]\(', (docs / 'overview.md').read_text()):
        inventory.update(re.findall(r'`(\w+)`', names))
    if set(catalog) != inventory:
        failures.append(f'Plugin inventory: missing {inventory - set(catalog)}, extra {set(catalog) - inventory}')
    documented = {}

    def collect_examples(value):
        if isinstance(value, dict):
            kind = value.get('type')
            if isinstance(kind, str) and kind in catalog and isinstance(value.get('args'), (dict, list)):
                documented.setdefault(kind, set()).update(object_paths(value['args']))
            for child in value.values():
                collect_examples(child)
        elif isinstance(value, list):
            for child in value:
                collect_examples(child)

    for file in sorted(docs.glob('*/*.mdx')):
        text = file.read_text()
        for section in re.split(r'(?m)(?=^## `)', text)[1:]:
            types = re.findall(r'`(\w+)`', section.splitlines()[0])
            options = re.search(r'(?ms)^### 配置项\n(.*?)(?=^### |\Z)', section)
            if not options:
                continue
            for kind in types:
                fields = documented.setdefault(kind, set())
                for heading in re.findall(r'(?m)^#### (.*)$', options[1]):
                    for field in re.findall(r'`([^`]+)`', heading):
                        field = re.sub(r'^args\.', '', field)
                        if field == 'args':
                            continue
                        if field.startswith('args[]'):
                            field = field[4:]
                        fields.add(field)
        for block in re.findall(r'(?s)```yaml\n(.*?)```', text):
            try:
                collect_examples(yaml.safe_load(block))
            except yaml.YAMLError:
                # Some documentation snippets are deliberately incomplete.
                continue
    field_count = 0
    for kind, paths in documented.items():
        for path in sorted(paths):
            # HTTP JSON content is user data, not an OxiDNS option schema.
            if kind == 'http_request' and path.startswith('json.'):
                continue
            field_count += 1
            if not schema_at(catalog[kind]['args'], path):
                failures.append(f'Missing documented field: {kind}.args.{path}')
    for kind, (filename, struct_name) in STRUCTS.items():
        text = (official / 'src' / filename).read_text()
        match = re.search(r'struct ' + struct_name + r'\s*{(.*?)\n}', text, re.S)
        if not match:
            failures.append(f'Native struct not found: {filename}:{struct_name}')
            continue
        native = set(re.findall(r'^\s*(?:pub(?:\([^)]*\))?\s+)?(\w+)\s*:', match[1], re.M))
        if 'async_mode' in native:
            native.remove('async_mode')
            native.add('async')
        fields = set(catalog[kind]['args'].get('properties', {}))
        if native != fields:
            failures.append(f'{kind} native fields: missing {native - fields}, extra {fields - native}')
    print(f'Official source: {actual_commit}')
    print(f'Checked {len(inventory)} plugins, {field_count} documented paths, {len(STRUCTS)} native YAML structs.')
    if failures:
        print('\n'.join(failures))
        return 1
    print('No missing documented fields or mismatched native object fields.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
