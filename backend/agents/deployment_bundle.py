"""Build self-contained, byte-preserving Agent deployment archives.

All callbacks are resolved from the captured profile, never from a second HTTP
request to ConfigFlow. A missing resource aborts publication instead of leaving
an Agent to download a partial configuration.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import gzip
import io
import ipaddress
import json
from pathlib import PurePosixPath
import re
import tarfile
from urllib.parse import parse_qs, unquote, urlsplit
import uuid

import yaml

from backend.utils.rule_fetch import is_internal_rule_url, request_rule

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_FILES = 4096


class DeploymentPreparationError(ValueError):
    """An artifact cannot be published as a complete deployment."""


@dataclass(frozen=True)
class DeploymentBundle:
    manifest: dict
    archive: bytes
    sha256: str
    config_version: str


def relative_path(value):
    if not isinstance(value, str) or not value or '\\' in value or any(ord(c) < 32 for c in value):
        raise DeploymentPreparationError('Invalid deployment file path')
    while value.startswith('./'):
        value = value[2:]
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(p in ('..', '.', '') for p in value.split('/')):
        raise DeploymentPreparationError('Deployment files must use safe relative paths')
    if value.startswith('.configflow') or value == 'manifest.json':
        raise DeploymentPreparationError('Reserved deployment file path')
    return path.as_posix()


def _local_rule(url, config, base_url):
    """Return the snapshot's library entry only for a recognized local URL."""
    from backend.common.profile_context import profile_api_path
    parsed = urlsplit(url)
    settings = deepcopy(config.get('system_config', {}))
    settings['server_domain'] = settings.get('server_domain') or base_url
    if (parsed.scheme or parsed.netloc) and not is_internal_rule_url(url, {'system_config': settings}):
        return None
    prefix = urlsplit(settings['server_domain']).path.rstrip('/')
    path = unquote(parsed.path)
    for rule in config.get('rule_library', []):
        paths = [profile_api_path(config, '/rules/local/' + rule.get('name', '')),
                 profile_api_path(config, '/rule-library/content/' + rule.get('id', ''))]
        if path in paths or path in [prefix + p for p in paths]:
            return rule
    if '/api/' in path or path.startswith('/rules/local/') or path.startswith('/rule-library/content/'):
        raise DeploymentPreparationError('Rule callback is not part of this profile snapshot')
    return None


def _fetch_bytes(url, config):
    if not url:
        raise DeploymentPreparationError('Required rule has no source URL')
    response = None
    try:
        response = request_rule(url, timeout=(10, 60), config_data=config, stream=True)
        response.raise_for_status()
        value = bytearray()
        for chunk in response.iter_content(chunk_size=64 * 1024):
            value.extend(chunk)
            if len(value) > MAX_FILE_BYTES:
                raise DeploymentPreparationError('Rule exceeds deployment file size limit')
        return bytes(value)
    except DeploymentPreparationError:
        raise
    except Exception as exc:
        # Exceptions from requests may include subscription or proxy credentials.
        raise DeploymentPreparationError(f'Required rule download failed ({type(exc).__name__})') from None
    finally:
        if response is not None:
            response.close()


def materialize_rule(url, config, base_url, *, depth=0, raw_mosdns=False):
    if depth > 3:
        raise DeploymentPreparationError('Recursive rule callback')
    parsed = urlsplit(url)
    settings = {**config.get('system_config', {}),
                'server_domain': config.get('system_config', {}).get('server_domain') or base_url}
    internal = not (parsed.scheme or parsed.netloc) or is_internal_rule_url(url, {'system_config': settings})
    if internal and parsed.path.endswith('/mosdns/rule-proxy'):
        original = parse_qs(parsed.query).get('url', [''])[0]
        if not original:
            raise DeploymentPreparationError('Rule conversion callback has no source')
        value = materialize_rule(original, config, base_url, depth=depth + 1, raw_mosdns=raw_mosdns)
        # The final consuming plugin chooses domain/IP conversion for bundles.
        return value if raw_mosdns else convert_mosdns_rules(value)
    rule = _local_rule(url, config, base_url)
    if rule is not None:
        if rule.get('source_type') == 'content':
            return rule.get('content', '').encode('utf-8')
        source = rule.get('url', '')
        if source == url:
            raise DeploymentPreparationError('Rule callback refers to itself')
        return materialize_rule(source, config, base_url, depth=depth + 1, raw_mosdns=raw_mosdns)
    return _fetch_bytes(url, config)


def convert_mosdns_rules(content, rule_type='domain_set'):
    """Convert text for the final MosDNS domain_set or ip_set consumer."""
    if rule_type not in ('domain_set', 'ip_set'):
        raise DeploymentPreparationError('Unsupported MosDNS rule plugin type')
    try:
        text = content.decode('utf-8-sig')
    except UnicodeError:
        raise DeploymentPreparationError('MosDNS requires text rules; binary MRS is only supported by Mihomo') from None
    if '\x00' in text:
        raise DeploymentPreparationError('MosDNS rule source is binary')
    lines = text.splitlines()
    if 'payload:' in text:
        try:
            document = yaml.safe_load(text)
            lines = document['payload']
            if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
                raise ValueError
        except Exception:
            raise DeploymentPreparationError('Invalid rule YAML payload') from None
    converted = []
    mapping = {'DOMAIN': 'full', 'DOMAIN-SUFFIX': 'domain', 'DOMAIN-KEYWORD': 'keyword', 'DOMAIN-REGEX': 'regexp'}
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if rule_type == 'ip_set':
            if re.match(r'^(domain|full|keyword|regexp):', line):
                continue
            value = line[3:].strip() if line.startswith('ip:') else line
            if ',' in line:
                kind, value, *_ = [part.strip() for part in line.split(',')]
                if kind not in ('IP-CIDR', 'IP-CIDR6'):
                    continue
            try:
                ipaddress.ip_network(value, strict=False)
            except ValueError:
                raise DeploymentPreparationError('Invalid IP network in MosDNS ip_set rule source') from None
            converted.append(value)
            continue
        if line.startswith('ip:'):
            continue
        if re.match(r'^(domain|full|keyword|regexp):', line):
            converted.append(line)
        elif ',' in line:
            # Match the existing rule-proxy conversion, including commas inside
            # DOMAIN-REGEX expressions such as repetition bounds {1,3}.
            kind, value = [part.strip() for part in line.split(',', 1)]
            if kind in mapping:
                converted.append(f'{mapping[kind]}:{value}')
            # Domain consumers omit IP/process rules, as rule-proxy did for
            # Clash input. In particular, a bare IPv6 CIDR cannot load in domain_set.
        elif line.startswith('+.'):
            converted.append('domain:' + line[2:])
        elif line.startswith('*.'):
            converted.append(r'regexp:^[^.]+\.' + re.escape(line[2:]) + '$')
        elif line.startswith('.'):
            converted.append(r'regexp:.+\.' + re.escape(line[1:]) + '$')
        else:
            try:
                ipaddress.ip_network(line, strict=False)
                # Raw address/CIDR lists must not reach a domain_set either.
            except ValueError:
                if '.' not in line or any(c.isspace() for c in line) or '<' in line or '>' in line:
                    raise DeploymentPreparationError('Unrecognized MosDNS rule source content') from None
                converted.append('full:' + line)
    return '\n'.join(converted).encode('utf-8')


def build_deployment_bundle(agent, config, config_content, *, provider_downloads=(),
                            ruleset_downloads=(), custom_files=(), base_url='', deployment_id=None):
    """Pack the exact generated graph and all its managed file references."""
    config = deepcopy(config)
    service = agent.get('service_type', 'mihomo')
    if service not in ('mihomo', 'mosdns'):
        raise DeploymentPreparationError('Unsupported transactional deployment service')
    main = yaml.safe_load(config_content)
    if not isinstance(main, dict):
        raise DeploymentPreparationError('Generated configuration must be a YAML mapping')
    rule_types = {}
    if service == 'mosdns':
        for plugin in main.get('plugins', []):
            rule_type = plugin.get('type')
            if rule_type not in ('domain_set', 'ip_set'):
                continue
            args = plugin.get('args') or {}
            if not isinstance(args, dict) or not isinstance(args.get('files', []), list):
                raise DeploymentPreparationError('MosDNS file references must be a list')
            for reference in args.get('files', []):
                path = relative_path(reference)
                if path in rule_types and rule_types[path] != rule_type:
                    raise DeploymentPreparationError('MosDNS file is used by both domain_set and ip_set: ' + path)
                rule_types[path] = rule_type
    files = {}

    def add(path, value, role):
        path = relative_path(path)
        value = value.encode('utf-8') if isinstance(value, str) else value
        if not isinstance(value, bytes):
            raise DeploymentPreparationError('Missing deployment file content')
        if path in rule_types:
            value = convert_mosdns_rules(value, rule_types[path])
        if len(value) > MAX_FILE_BYTES:
            raise DeploymentPreparationError('Deployment file exceeds size limit')
        if path in files:
            raise DeploymentPreparationError('Duplicate deployment path: ' + path)
        if len(files) >= MAX_FILES or sum(len(item[0]) for item in files.values()) + len(value) > MAX_TOTAL_BYTES:
            raise DeploymentPreparationError('Deployment exceeds size limit')
        files[path] = (value, role)
        return path

    for item in provider_downloads:
        add(item['local_path'], item.get('content'), 'provider')
    for item in custom_files:
        add(item['path'], item.get('content'), 'custom')

    # Use the final Mihomo configuration rather than reconstructing paths from
    # source suffixes; custom configuration can change provider format/path.
    downloads = list(ruleset_downloads)
    if service == 'mihomo' and 'rule-providers' in main:
        downloads = []
        for name, definition in main['rule-providers'].items():
            if definition.get('type') == 'inline':
                definition.pop('path', None)
                continue
            if not definition.get('path'):
                raise DeploymentPreparationError(f'Rule provider {name} has no bundled path')
            path = relative_path(definition['path'])
            if path in files:
                continue
            if not definition.get('url'):
                raise DeploymentPreparationError(f'Rule provider {name} references an unbundled local file; use a managed rule source')
            downloads.append({'name': name, 'url': definition['url'], 'local_path': path})
    def download(item):
        try:
            value = materialize_rule(item.get('url', ''), config, base_url,
                                     raw_mosdns=relative_path(item['local_path']) in rule_types)
        except DeploymentPreparationError as exc:
            raise DeploymentPreparationError(f"Rule {item.get('name', 'unnamed')}: {exc}") from None
        return item, value
    with ThreadPoolExecutor(max_workers=8) as pool:
        for item, value in pool.map(download, downloads):
            add(item['local_path'], value, 'rule')

    if service == 'mihomo':
        for section in ('proxy-providers', 'rule-providers'):
            for name, provider in (main.get(section) or {}).items():
                if 'path' not in provider:
                    if provider.get('type') == 'inline':
                        continue
                    raise DeploymentPreparationError(f'Provider {name} has no bundled path')
                path = relative_path(provider['path'])
                if path not in files:
                    raise DeploymentPreparationError(f'Provider {name} has no bundled file')
                provider['path'] = './' + path
    else:
        for plugin in main.get('plugins', []):
            args = plugin.get('args') or {}
            if not isinstance(args, dict):
                continue
            paths = args.get('files', [])
            if not isinstance(paths, list):
                raise DeploymentPreparationError('MosDNS file references must be a list')
            for index, value in enumerate(paths):
                path = relative_path(value)
                if path not in files:
                    raise DeploymentPreparationError('MosDNS references an unbundled file: ' + path)
                paths[index] = './' + path
    config_bytes = yaml.safe_dump(main, allow_unicode=True, sort_keys=False).encode('utf-8')
    add('config.yaml', config_bytes, 'config')
    manifest = {
        'protocol_version': 1, 'deployment_id': deployment_id or uuid.uuid4().hex,
        'agent_id': agent['id'], 'profile_id': config.get('profile_id') or agent.get('profile_id', 'default'),
        'service_type': service, 'config_path': 'config.yaml',
        'files': [{'path': path, 'size': len(value), 'sha256': hashlib.sha256(value).hexdigest(), 'role': role}
                  for path, (value, role) in sorted(files.items())],
    }
    return _pack(manifest, files, hashlib.sha256(config_bytes).hexdigest()[:8])


def _pack(manifest, files, config_version):
    archive = io.BytesIO()
    with gzip.GzipFile(fileobj=archive, mode='wb', mtime=0) as compressed, \
            tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as tar:
        members = [('manifest.json', json.dumps(manifest, ensure_ascii=False).encode('utf-8'))]
        members.extend(('files/' + path, value) for path, (value, _) in sorted(files.items()))
        for path, value in members:
            member = tarfile.TarInfo(path)
            member.size = len(value)
            member.mode = 0o600
            tar.addfile(member, io.BytesIO(value))
    content = archive.getvalue()
    return DeploymentBundle(manifest, content, hashlib.sha256(content).hexdigest(), config_version)


def retarget_config_path(bundle, config_path):
    """Honor the target's installed config filename without changing config bytes."""
    path = relative_path(config_path)
    if '/' in path:
        raise DeploymentPreparationError('Agent configuration filename must be a basename')
    current = bundle.manifest['config_path']
    if path == current:
        return bundle
    manifest = deepcopy(bundle.manifest)
    if any(item['path'] == path for item in manifest['files']):
        raise DeploymentPreparationError('Agent configuration filename collides with a managed file')
    manifest['config_path'] = path
    files = {}
    with tarfile.open(fileobj=io.BytesIO(bundle.archive), mode='r:gz') as archive:
        for item in manifest['files']:
            old_path = item['path']
            if old_path == current:
                item['path'] = path
            files[item['path']] = (archive.extractfile('files/' + old_path).read(), item['role'])
    return _pack(manifest, files, bundle.config_version)
