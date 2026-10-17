"""Feature switches remove inactive runtime plugins without losing draft values."""
from copy import deepcopy
import json
from itertools import permutations

import pytest

from backend.test_oxidns_common import prepared, apply, doc, plugin, edited
from backend.common.oxidns_validation import validate_settings
from backend.common.oxidns_common import read_common_settings
from backend.common.oxidns_settings import OxidnsSettingsError


def valid_feature(feature):
    changes = {f'{feature}.enabled': True}
    if feature.startswith('ros_'):
        changes.update({f'{feature}.args.address': '192.0.2.1:8728', f'{feature}.args.username': 'test-user',
                        f'{feature}.args.password': 'test-password'})
        if feature == 'ros_route':
            changes.update({'ros_route.args.routing_table': 'main', 'ros_route.args.gateway4': '192.0.2.254'})
    return apply(prepared(), changes)['settings']


@pytest.mark.parametrize('feature,tag,field,invalid,valid', [
    ('query_recorder', 'query_recorder', 'query_recorder.args.queue_size', 0, 1024),
    ('ip_selector', 'ip_selector', 'ip_selector.args.top_n', -1, 3),
    ('ros_address_list', 'ros_address_list', 'ros_address_list.args.address', '', '192.0.2.2:8728'),
    ('ros_route', 'ros_route', 'ros_route.args.routing_table', '', 'next-table'),
    ('dynamic_domain_set', 'dynamic_domains', 'dynamic_domain_set.args.queue_size', 0, 1024),
])
def test_disabled_features_do_not_validate_or_initialize_their_incomplete_plugins(feature, tag, field, invalid, valid):
    settings = edited(valid_feature(feature), lambda value: plugin(value, tag).update(future={'keep': [0, False, None]}))
    assert validate_settings(settings)['valid']
    incomplete = apply(settings, {field: invalid})['settings']
    assert not validate_settings(incomplete)['valid']
    disabled = apply(incomplete, {f'{feature}.enabled': False, field: invalid})['settings']
    assert not any(item['tag'] == tag for item in doc(disabled)['plugins'])
    assert validate_settings(disabled)['valid']
    changed = apply(json.loads(json.dumps(disabled)), {field: valid})['settings']
    assert not any(item['tag'] == tag for item in doc(changed)['plugins'])
    assert validate_settings(changed)['valid']
    restored = apply(changed, {f'{feature}.enabled': True})
    assert restored['values'][feature]['enabled'] is True
    args = plugin(doc(restored['settings']), tag)['args']
    assert args[field.rsplit('.', 1)[1]] == valid
    assert plugin(doc(restored['settings']), tag)['future'] == {'keep': [0, False, None]}
    assert validate_settings(restored['settings'])['valid']


@pytest.mark.parametrize('enabled_field,size_field,tag', [
    ('cache_enabled', 'cache_size', 'lazy_cache'),
    ('probe_cache_enabled', 'probe_cache_size', 'local_probe_cache'),
])
def test_disabled_cache_keeps_parameters_without_an_active_plugin(enabled_field, size_field, tag):
    settings = apply(prepared(), {size_field: -1})['settings']
    disabled = apply(settings, {enabled_field: False})['settings']
    assert validate_settings(disabled)['valid']
    assert not any(item['tag'] == tag for item in doc(disabled)['plugins'])
    changed = apply(disabled, {size_field: 4096})
    assert changed['values'][enabled_field] is False
    assert changed['values'][size_field] == 4096
    assert not any(item['tag'] == tag for item in doc(changed['settings'])['plugins'])
    restored = apply(changed['settings'], {enabled_field: True})['settings']
    assert plugin(doc(restored), tag)['args']['size'] == 4096
    assert validate_settings(restored)['valid']


@pytest.mark.parametrize('feature', ['ros_address_list', 'ros_route'])
@pytest.mark.parametrize('off_first', [True, False])
def test_tls_disable_parameter_batch_stays_off_and_restores_saved_certificate(feature, off_first):
    field = f'{feature}.args.tls'
    original = {'ca': 'original.pem', 'server_name': 'router.test', 'insecure': False}
    settings = apply(valid_feature(feature), {field: original})['settings']
    changes = {field: None, field + '.ca': 'next.pem'} if off_first else {field + '.ca': 'next.pem', field: None}
    disabled = apply(settings, changes)['settings']
    assert 'tls' not in plugin(doc(disabled), feature)['args']
    assert validate_settings(disabled)['valid']
    restored = apply(json.loads(json.dumps(disabled)), {field: {}})['settings']
    assert plugin(doc(restored), feature)['args']['tls'] == {**original, 'ca': 'next.pem'}
    assert validate_settings(restored)['valid']


def test_dynamic_learning_and_proxy_route_survive_disable_save_reopen():
    settings = apply(prepared(), {'dynamic_domain_set.enabled': True, 'dynamic_domain_set.route': 'proxy',
                                  'dynamic_domain_set.learn': True})['settings']
    original = deepcopy(plugin(doc(settings), 'learn_classified_domain'))
    disabled = apply(settings, {'dynamic_domain_set.enabled': False})['settings']
    assert validate_settings(disabled)['valid']
    assert not any(item['tag'] in ('dynamic_domains', 'learn_classified_domain') for item in doc(disabled)['plugins'])
    restored = apply(json.loads(json.dumps(disabled)), {'dynamic_domain_set.enabled': True})
    assert restored['values']['dynamic_domain_set']['route'] == 'proxy'
    assert restored['values']['dynamic_domain_set']['learn'] is True
    assert plugin(doc(restored['settings']), 'learn_classified_domain') == original
    assert validate_settings(restored['settings'])['valid']


def test_learning_disable_does_not_leave_invalid_unreferenced_learner():
    settings = apply(prepared(), {'dynamic_domain_set.enabled': True, 'dynamic_domain_set.learn': True})['settings']
    incomplete = apply(settings, {'dynamic_domain_set.learn_args.qtypes': []})['settings']
    assert not validate_settings(incomplete)['valid']
    disabled = apply(incomplete, {'dynamic_domain_set.learn': False})['settings']
    assert validate_settings(disabled)['valid']
    assert not any(item['tag'] == 'learn_classified_domain' for item in doc(disabled)['plugins'])
    assert any(item['tag'] == 'dynamic_domains' for item in doc(disabled)['plugins'])


def test_dynamic_argument_edit_preserves_learner_called_from_include():
    settings = apply(prepared(), {'dynamic_domain_set.enabled': True, 'dynamic_domain_set.learn': True})['settings']
    settings = edited(settings, lambda value: (
        value.update(include=['custom.yaml']),
        plugin(value, 'direct_ip')['args'].remove({'exec': '$learn_classified_domain'}),
    ))
    settings['additional_files'] = [{'path': 'custom.yaml', 'encoding': 'text',
                                    'content': 'plugins:\n- tag: custom_learning\n  type: sequence\n  args:\n  - exec: $learn_classified_domain\n'}]
    assert validate_settings(settings)['valid']
    original = deepcopy(plugin(doc(settings), 'learn_classified_domain'))
    changed = apply(settings, {'dynamic_domain_set.args.queue_size': 2048})['settings']
    assert plugin(doc(changed), 'learn_classified_domain') == original
    assert validate_settings(changed)['valid']


def test_dynamic_provider_used_by_custom_learner_cannot_be_removed():
    settings = apply(prepared(), {'dynamic_domain_set.enabled': True, 'dynamic_domain_set.learn': True})['settings']
    def add_custom(value):
        custom = deepcopy(plugin(value, 'learn_classified_domain'))
        custom['tag'] = 'custom_learner'
        value['plugins'].append(custom)
    settings = edited(settings, add_custom)
    assert validate_settings(settings)['valid']
    assert not read_common_settings(settings, {})['capabilities']['dynamic_domain_set.enabled']['editable']
    with pytest.raises(OxidnsSettingsError, match='自定义学习器'):
        apply(settings, {'dynamic_domain_set.enabled': False})
    changed = apply(settings, {'dynamic_domain_set.args.queue_size': 2048})['settings']
    assert validate_settings(changed)['valid']


@pytest.mark.parametrize('restore_order', list(permutations(('ip_selector', 'ros_address_list', 'ros_route'))))
def test_multiple_feature_toggles_preserve_wrapper_order(restore_order):
    settings = valid_feature('ros_route')
    settings = apply(settings, {'ip_selector.enabled': True, 'ros_address_list.enabled': True,
                                'ros_address_list.args.address': '192.0.2.1:8728',
                                'ros_address_list.args.username': 'test-user',
                                'ros_address_list.args.password': 'test-password'})['settings']
    original = {tag: deepcopy(plugin(doc(settings), tag)['args']) for tag in ('china_dns', 'direct_ip', 'default_direct_ip')}
    for feature in ('ros_address_list', 'ip_selector', 'ros_route'):
        settings = apply(settings, {feature + '.enabled': False})['settings']
    settings = json.loads(json.dumps(settings))
    for feature in restore_order:
        settings = apply(settings, {feature + '.enabled': True})['settings']
    for tag, entries in original.items():
        assert plugin(doc(settings), tag)['args'] == entries
    assert validate_settings(settings)['valid']
