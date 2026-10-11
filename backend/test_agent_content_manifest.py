"""Content identity covers every delivered file without transaction metadata."""
from copy import deepcopy
import hashlib

import pytest

from backend.agents.deployment_bundle import (
    MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, DeploymentPreparationError,
    build_deployment_bundle, deployment_content_fingerprint, retarget_config_path,
)


def entry(path, content, role):
    return {'path': path, 'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'role': role}


def manifest():
    return {
        'protocol_version': 1, 'deployment_id': 'first', 'agent_id': 'agent-one',
        'profile_id': 'profile-one', 'service_type': 'mihomo', 'config_path': 'config.yaml',
        'files': [
            entry('config.yaml', b'mode: rule\n', 'config'),
            entry('providers/nodes.yaml', b'proxies: []\n', 'provider'),
            entry('rules/domain.list', b'DOMAIN,example.test', 'rule'),
            entry('rules/binary.mrs', b'\x00MRS\xff\x80', 'rule'),
            entry('rules/custom.txt', b'full:custom.test', 'custom'),
        ],
    }


def test_transaction_metadata_order_and_main_filename_do_not_change_content_identity():
    agent = {'id': 'first-agent', 'service_type': 'mihomo', 'profile_id': 'first-profile'}
    bundle = build_deployment_bundle(agent, {}, 'mode: rule', deployment_id='first')
    renamed = retarget_config_path(bundle, 'installed-config.yml')
    expected = deployment_content_fingerprint(bundle.manifest)
    assert deployment_content_fingerprint(renamed.manifest) == expected
    assert bundle.sha256 != renamed.sha256
    changed = deepcopy(renamed.manifest)
    changed.update(deployment_id='second', agent_id='second-agent', profile_id='second-profile',
                   created_at='later', updated_at='later', config_version='unrelated')
    assert deployment_content_fingerprint(changed) == expected
    complete = manifest()
    original = deepcopy(complete)
    expected = deployment_content_fingerprint(complete)
    complete['files'].reverse()
    assert deployment_content_fingerprint(complete) == expected
    assert complete == {**original, 'files': list(reversed(original['files']))}
    assert expected.startswith('v1:') and len(expected) == 67


@pytest.mark.parametrize('index', range(5), ids=['config', 'provider', 'rule', 'mrs', 'custom'])
def test_each_file_content_changes_the_fingerprint(index):
    original = manifest()
    changed = deepcopy(original)
    # Keep byte length unchanged so this proves content digests are included.
    changed['files'][index]['sha256'] = hashlib.sha256(b'different content').hexdigest()
    assert deployment_content_fingerprint(changed) != deployment_content_fingerprint(original)


@pytest.mark.parametrize('change', ['add', 'remove', 'path', 'size', 'role', 'service'])
def test_file_membership_metadata_and_service_are_part_of_content_identity(change):
    original = manifest()
    changed = deepcopy(original)
    if change == 'add':
        changed['files'].append(entry('rules/additional.list', b'new.test', 'rule'))
    elif change == 'remove':
        changed['files'].pop()
    elif change == 'path':
        changed['files'][-1]['path'] = 'rules/renamed.txt'
    elif change == 'size':
        changed['files'][-1]['size'] += 1
    elif change == 'role':
        changed['files'][-1]['role'] = 'rule'
    else:
        changed['service_type'] = 'mosdns'
    assert deployment_content_fingerprint(changed) != deployment_content_fingerprint(original)


def test_empty_rule_is_valid_and_distinct_from_an_absent_file():
    original = manifest()
    original['files'].append(entry('rules/empty.list', b'', 'rule'))
    fingerprint = deployment_content_fingerprint(original)
    original['files'].pop()
    assert deployment_content_fingerprint(original) != fingerprint


@pytest.mark.parametrize('field,value', [
    ('protocol_version', None), ('protocol_version', True), ('protocol_version', 2),
    ('service_type', 'surge'), ('service_type', None),
    ('config_path', None), ('config_path', '/etc/config.yaml'),
    ('config_path', '../config.yaml'), ('config_path', 'subdir/config.yaml'),
    ('files', None), ('files', {}), ('files', []), ('files', [None]),
])
def test_incomplete_or_unsupported_manifest_is_rejected(field, value):
    invalid = manifest()
    invalid[field] = value
    with pytest.raises(DeploymentPreparationError):
        deployment_content_fingerprint(invalid)


@pytest.mark.parametrize('field,value', [
    ('path', None), ('path', '/etc/passwd'), ('path', '../escape'),
    ('path', 'rules/../escape'), ('path', './rules/alias'), ('path', 'rules//alias'),
    ('path', 'rules\\alias'), ('path', 'rules/\x00bad'), ('path', 'rules/\x7fbad'),
    ('path', '.configflow-deployments/state.json'), ('path', 'manifest.json'),
    ('size', None), ('size', True), ('size', -1), ('size', 1.0), ('size', MAX_FILE_BYTES + 1),
    ('sha256', None), ('sha256', 'a' * 63), ('sha256', 'z' * 64),
    ('role', None), ('role', []), ('role', 'other'),
])
def test_malicious_or_malformed_file_metadata_is_rejected(field, value):
    invalid = manifest()
    invalid['files'][-1][field] = value
    with pytest.raises(DeploymentPreparationError):
        deployment_content_fingerprint(invalid)


@pytest.mark.parametrize('problem', ['missing', 'multiple', 'wrong-path', 'wrong-role', 'empty', 'duplicate', 'canonical-collision'])
def test_main_configuration_and_paths_must_be_unambiguous(problem):
    invalid = manifest()
    if problem == 'missing':
        invalid['files'].pop(0)
    elif problem == 'multiple':
        invalid['files'].append(entry('second.yaml', b'mode: rule', 'config'))
    elif problem == 'wrong-path':
        invalid['config_path'] = 'absent.yaml'
    elif problem == 'wrong-role':
        invalid['files'][0]['role'] = 'custom'
    elif problem == 'empty':
        invalid['files'][0] = entry('config.yaml', b'', 'config')
    elif problem == 'duplicate':
        invalid['files'].append(deepcopy(invalid['files'][1]))
    else:
        invalid['config_path'] = 'installed.yaml'
        invalid['files'][0]['path'] = 'installed.yaml'
        invalid['files'].append(entry('config.yaml', b'collision', 'custom'))
    with pytest.raises(DeploymentPreparationError):
        deployment_content_fingerprint(invalid)


def test_invalid_manifest_type_and_deployment_limits_are_rejected():
    for invalid in (None, [], 'manifest'):
        with pytest.raises(DeploymentPreparationError):
            deployment_content_fingerprint(invalid)
    oversized = manifest()
    oversized['files'] = [entry('config.yaml', b'mode: rule', 'config')]
    oversized['files'] += [
        {'path': f'rules/{index}.list', 'size': MAX_FILE_BYTES, 'sha256': 'a' * 64, 'role': 'rule'}
        for index in range(MAX_TOTAL_BYTES // MAX_FILE_BYTES)
    ]
    with pytest.raises(DeploymentPreparationError, match='size limit'):
        deployment_content_fingerprint(oversized)
    oversized['files'] = [entry(f'rules/{index}.list', b'', 'rule') for index in range(MAX_FILES + 1)]
    with pytest.raises(DeploymentPreparationError):
        deployment_content_fingerprint(oversized)
