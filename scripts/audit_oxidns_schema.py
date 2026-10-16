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


def schema_nodes(schema, path):
    """Follow declared fields across JSON Schema unions, arrays and named maps."""
    nodes = [schema]
    for key in re.findall(r'\[\]|[^.\[\]]+', path):
        next_nodes = []
        pending = list(nodes)
        while pending:
            node = pending.pop()
            if not isinstance(node, dict):
                continue
            pending.extend(node.get('anyOf', []))
            if key == '[]':
                child = node.get('items')
            elif key == '*':
                child = node.get('additionalProperties')
            else:
                child = node.get('properties', {}).get(key)
                if child is None:
                    child = node.get('additionalProperties')
            if isinstance(child, dict):
                next_nodes.append(child)
        nodes = next_nodes
    return nodes


def schema_at(schema, path):
    return bool(schema_nodes(schema, path))


def schema_properties(schema, path=''):
    pending = schema_nodes(schema, path)
    fields = set()
    while pending:
        node = pending.pop()
        fields.update(node.get('properties', {}))
        pending.extend(node.get('anyOf', []))
    return fields


def declared_paths(schema, prefix=''):
    """All UI field paths, including nested array and arbitrary-name map items."""
    for key, child in schema.get('properties', {}).items():
        path = f'{prefix}.{key}' if prefix else key
        yield path
        yield from declared_paths(child, path)
    if isinstance(schema.get('items'), dict):
        yield from declared_paths(schema['items'], prefix + '[]')
    if isinstance(schema.get('additionalProperties'), dict):
        yield from declared_paths(schema['additionalProperties'], prefix + '.*')
    for variant in schema.get('anyOf', []):
        yield from declared_paths(variant, prefix)


# Nested YAML-facing structures are checked separately so a new child field is
# detected even when the parent object already exists in both catalogs.
NESTED_STRUCTS = [
    ('http_server', 'entries[]', 'plugin/server/http/mod.rs', 'Entry'),
    ('forward', 'upstreams[]', 'infra/network/upstream/config.rs', 'UpstreamConfig'),
    ('sequence', '[]', 'plugin/executor/sequence/mod.rs', 'Rule'),
    ('ip_selector', 'cache', 'plugin/executor/ip_selector/config.rs', 'IpSelectorCacheConfig'),
    ('nftset', 'ipv4', 'plugin/executor/nftset.rs', 'NftSetArgs'),
    ('nftset', 'ipv6', 'plugin/executor/nftset.rs', 'NftSetArgs'),
    ('download', 'downloads[]', 'plugin/executor/download.rs', 'DownloadItemConfig'),
    ('cron', 'jobs[]', 'plugin/executor/cron.rs', 'JobConfig'),
    ('time', 'periods[]', 'plugin/matcher/time.rs', 'PeriodConfig'),
    ('prefer_ipv6', '', 'plugin/executor/dual_selector.rs', 'DualSelectorConfig'),
]
for kind in ('ros_route', 'ros_address_list'):
    NESTED_STRUCTS.extend([
        (kind, 'persistent', f'plugin/executor/{kind}/config.rs', 'PersistentArgs'),
        (kind, 'tls', 'plugin/executor/routeros/transport.rs', 'RouterOsTlsArgs'),
    ])
GLOBAL_STRUCTS = {
    '': 'Config', 'runtime': 'RuntimeConfig', 'log': 'LogConfig',
    'network': 'NetworkConfig', 'network.outbound': 'NetworkOutboundConfig',
    'network.outbound.profiles.*': 'OutboundProfileConfig',
    'network.outbound.profiles.*.resolver': 'OutboundResolverDetailedConfig',
    'network.outbound.profiles.*.resolver.nameservers[]': 'OutboundNameserverConfig',
    'api': 'ApiConfig', 'api.http': 'ApiHttpDetailedConfig',
    'api.http.cors': 'ApiCorsConfig', 'api.http.webui': 'ApiWebUiConfig',
    'api.http.ssl': 'ApiTlsConfig',
}
GLOBAL_ENUMS = {
    'log.rotation': 'LogRotation', 'api.http.auth': 'ApiAuthConfig',
    'network.outbound.profiles.*.proxy': 'OutboundProxyConfig',
}


def native_fields(text, name, enum=False):
    match = re.search(('enum ' if enum else 'struct ') + name + r'\s*{(.*?)\n}', text, re.S)
    if not match:
        raise ValueError('Native type not found: ' + name)
    body = re.sub(r'(?m)^\s*///.*$', '', match[1])
    if enum:
        fields = set(re.findall(r'\b(\w+)\s*:\s*(?:String|Option<)', body))
        # Both of these enums are internally tagged; outbound proxy is untagged.
        if name in ('LogRotation', 'ApiAuthConfig'):
            fields.add('type')
        return fields
    fields = set()
    attributes = ''
    for line in body.splitlines():
        if '#[' in line:
            attributes += line
            continue
        field = re.match(r'\s*(?:pub(?:\([^)]*\))?\s+)?(\w+)\s*:', line)
        if field:
            if not re.search(r'\bskip(?:_deserializing)?\b', attributes):
                renamed = re.search(r'rename\s*=\s*"([^"]+)"', attributes)
                fields.add(renamed[1] if renamed else field[1])
            attributes = ''
    return fields


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
    backend_documents = [json.loads((REPO / 'backend/schemas/oxidns' / name).read_text()) for name in ('global.json', 'plugins.json')]
    backend_plugins = {key: value for document in backend_documents for key, value in document['plugins'].items()}
    backend_global = backend_documents[0]['global']
    for document in backend_documents:
        if document['source']['commit'] != actual_commit or document['version'] != exported['oxidnsSchemaVersion']:
            failures.append('Backend schema version/source does not match official checkout and UI catalog')
    if actual_commit != expected_commit:
        failures.append(f'Source commit changed: {actual_commit}; catalog pins {expected_commit}')
    inventory = set()
    for names in re.findall(r'\| \[(.*?)\]\(', (docs / 'overview.md').read_text()):
        inventory.update(re.findall(r'`(\w+)`', names))
    if set(catalog) != inventory:
        failures.append(f'Plugin inventory: missing {inventory - set(catalog)}, extra {set(catalog) - inventory}')
    if set(backend_plugins) != inventory:
        failures.append(f'Backend plugin inventory: missing {inventory - set(backend_plugins)}, extra {set(backend_plugins) - inventory}')
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
                failures.append(f'UI missing documented field: {kind}.args.{path}')
            if not schema_at(backend_plugins.get(kind, {}), path):
                failures.append(f'Backend missing documented field: {kind}.args.{path}')
    ui_path_count = 0
    for label, ui_schema, backend_schema in [('global', exported['oxidnsGlobalSchema'], backend_global)] + [
        (kind + '.args', plugin['args'], backend_plugins.get(kind, {})) for kind, plugin in catalog.items()
    ]:
        for path in sorted(set(declared_paths(ui_schema))):
            ui_path_count += 1
            if not schema_at(backend_schema, path):
                failures.append(f'Backend missing UI field: {label}.{path}')

    def check_fields(label, source, name, path, ui_schema, backend_schema, enum=False):
        try:
            native = native_fields((official / 'src' / source).read_text(), name, enum)
        except ValueError as error:
            failures.append(str(error))
            return
        for surface, schema in [('UI', ui_schema), ('Backend', backend_schema)]:
            fields = schema_properties(schema, path)
            compared = native - ({'plugins'} if label == 'global' and not path and surface == 'UI' else set())
            if compared != fields:
                failures.append(f'{surface} {label}.{path} ({name}): missing {compared - fields}, extra {fields - compared}')

    for kind, (filename, struct_name) in STRUCTS.items():
        check_fields(kind, filename, struct_name, '', catalog[kind]['args'], backend_plugins.get(kind, {}))
    for kind, path, filename, struct_name in NESTED_STRUCTS:
        check_fields(kind, filename, struct_name, path, catalog[kind]['args'], backend_plugins.get(kind, {}))
    for path, name in GLOBAL_STRUCTS.items():
        check_fields('global', 'config/types.rs', name, path, exported['oxidnsGlobalSchema'], backend_global)
    for path, name in GLOBAL_ENUMS.items():
        check_fields('global', 'config/types.rs', name, path, exported['oxidnsGlobalSchema'], backend_global, enum=True)
    print(f'Official source: {actual_commit}')
    print(f'Checked {len(inventory)} plugins, {field_count} documented paths, {ui_path_count} UI/backend paths, {len(STRUCTS) + len(NESTED_STRUCTS) + len(GLOBAL_STRUCTS) + len(GLOBAL_ENUMS)} native YAML structures.')
    if failures:
        print('\n'.join(failures))
        return 1
    print('No missing documented fields or mismatched native object fields.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
