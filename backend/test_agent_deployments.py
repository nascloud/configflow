"""Transactional deployment preparation, persistent ownership and result handling."""
import hashlib
import io
import json
import tarfile
from copy import deepcopy

import pytest
import requests
import yaml

from backend.agents.deployment_bundle import (
    DeploymentPreparationError, build_deployment_bundle, convert_mosdns_rules,
)
from backend.agents.manager import AgentManager
from backend.common import config as config_module
from backend.common.config_repository import ProfileRepository


@pytest.fixture
def env(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path)
    config_module.set_repository(repository)
    manager = AgentManager(repository)
    registration = manager.register_agent({'name': 'test', 'host': 'agent.test', 'port': 8080,
                                           'service_type': 'mihomo'})
    agent = manager.get_agent_by_id(registration['id'])
    config = {'profile_id': 'default', 'rule_library': [], 'system_config': {'server_domain': 'https://config.test'}}
    return repository, manager, agent, config


def response(payload, status=200):
    value = requests.Response()
    value.status_code = status
    value._content = json.dumps(payload).encode()
    value._content_consumed = True
    return value


def contents(bundle):
    with tarfile.open(fileobj=io.BytesIO(bundle.archive), mode='r:gz') as archive:
        return {member.name: archive.extractfile(member).read() for member in archive.getmembers()}


def minimal_bundle(agent, config, deployment_id='test-release'):
    return build_deployment_bundle(agent, config, 'mode: rule\nrules: [MATCH,DIRECT]', deployment_id=deployment_id)


def test_bundle_preserves_binary_mrs_and_all_hashes(env, monkeypatch):
    _, _, agent, config = env
    original = b'\x00MRS\xff\xfe\x80\r\n'
    seen = []
    def fetch(url, **kwargs):
        seen.append((url, kwargs))
        result = response(None)
        result._content = original
        return result
    monkeypatch.setattr('backend.agents.deployment_bundle.request_rule', fetch)
    main = {'rule-providers': {'binary': {'type': 'http', 'format': 'mrs', 'behavior': 'domain',
                                         'url': 'https://rules.test/raw.mrs', 'path': './ruleset/binary.mrs'}}}
    bundle = build_deployment_bundle(agent, config, yaml.safe_dump(main))
    members = contents(bundle)
    assert members['files/ruleset/binary.mrs'] == original
    assert hashlib.sha256(bundle.archive).hexdigest() == bundle.sha256
    for member in bundle.manifest['files']:
        content = members['files/' + member['path']]
        assert member['size'] == len(content)
        assert member['sha256'] == hashlib.sha256(content).hexdigest()
    assert seen[0][1]['config_data'] == config
    assert seen[0][1]['stream'] is True


def test_inline_rule_snapshot_and_empty_files_do_not_use_self_http(env, monkeypatch):
    _, _, agent, config = env
    config['rule_library'] = [{'id': 'r', 'name': 'Empty', 'source_type': 'content', 'content': ''}]
    monkeypatch.setattr('backend.agents.deployment_bundle.request_rule', lambda *_a, **_k: pytest.fail('self HTTP'))
    main = {'rule-providers': {'empty': {'type': 'http', 'path': './ruleset/empty.list',
                                       'url': 'https://config.test/api/profiles/default/rules/local/Empty'}}}
    bundle = build_deployment_bundle(agent, config, yaml.safe_dump(main))
    assert contents(bundle)['files/ruleset/empty.list'] == b''


def test_failed_required_download_aborts_without_leaking_credentials(env, monkeypatch):
    _, _, agent, config = env
    def fail(*_args, **_kwargs):
        raise requests.exceptions.ProxyError('http://secret:password@proxy.test')
    monkeypatch.setattr('backend.agents.deployment_bundle.request_rule', fail)
    with pytest.raises(DeploymentPreparationError) as exc:
        build_deployment_bundle(agent, config, 'mode: rule', ruleset_downloads=[{
            'name': 'required', 'url': 'https://secret.test', 'local_path': './rules/list'}])
    assert 'password' not in str(exc.value)
    assert 'required' in str(exc.value)


@pytest.mark.parametrize('path', ['/etc/passwd', '../config.yaml', 'rules/../../config.yaml', '.configflow-deployments/x'])
def test_bundle_rejects_unsafe_paths(env, path):
    _, _, agent, config = env
    with pytest.raises(DeploymentPreparationError):
        build_deployment_bundle(agent, config, 'mode: rule', custom_files=[{'path': path, 'content': ''}])


def test_bundle_rejects_unbundled_file_and_duplicate_paths(env):
    _, _, agent, config = env
    with pytest.raises(DeploymentPreparationError, match='bundled'):
        build_deployment_bundle(agent, config, 'proxy-providers: {local: {type: file, path: ./missing.yaml}}')
    with pytest.raises(DeploymentPreparationError, match='Duplicate'):
        build_deployment_bundle(agent, config, 'mode: rule', custom_files=[{'path': 'config.yaml', 'content': 'bad'}])


def test_mosdns_conversion_and_snapshot_callback(env, monkeypatch):
    _, _, agent, config = env
    agent['service_type'] = 'mosdns'
    config['rule_library'] = [{'id': 'r', 'source_type': 'content', 'content': 'DOMAIN-SUFFIX,example.test\nIP-CIDR,10.0.0.0/8,no-resolve'}]
    config['rule_configs'] = [{'library_rule_id': 'r', 'enabled': True}]
    url = 'https://config.test/api/profiles/default/mosdns/rule-proxy?url=https%3A%2F%2Fconfig.test%2Fapi%2Fprofiles%2Fdefault%2Frule-library%2Fcontent%2Fr'
    monkeypatch.setattr('backend.agents.deployment_bundle.request_rule', lambda *_a, **_k: pytest.fail('self HTTP'))
    bundle = build_deployment_bundle(agent, config, 'plugins: [{type: domain_set, args: {files: [./rules/r.txt]}}]',
                                     ruleset_downloads=[{'name': 'r', 'url': url, 'local_path': './rules/r.txt'}])
    assert contents(bundle)['files/rules/r.txt'] == b'domain:example.test\n10.0.0.0/8'
    assert convert_mosdns_rules(b'') == b''
    with pytest.raises(DeploymentPreparationError):
        convert_mosdns_rules(b'\xffMRS')


def test_upload_ready_does_not_mark_version_and_status_survives_restart(env, monkeypatch):
    repository, manager, agent, config = env
    bundle = minimal_bundle(agent, config)
    seen = []
    def get(url, **_kwargs):
        if url.endswith('/capabilities'):
            return response({'success': True, 'deployment_protocols': [1], 'service_type': 'mihomo'})
        return response({'success': True, 'deployment_id': bundle.manifest['deployment_id'],
                         'status': 'succeeded', 'config_version': 'agent-final-hash'})
    def post(url, **kwargs):
        seen.append((url, kwargs))
        return response({'success': True, 'deployment_id': bundle.manifest['deployment_id'], 'status': 'ready'}, 202)
    monkeypatch.setattr('backend.agents.manager.requests.get', get)
    monkeypatch.setattr('backend.agents.manager.requests.post', post)
    result = manager.publish_deployment(agent['id'], bundle, activate=False)
    assert result['status'] == 'ready'
    assert manager.get_agent_by_id(agent['id'])['config_version'] == '0'
    assert seen[0][1]['data'] == bundle.archive
    assert seen[0][1]['headers']['X-Activate'] == 'false'
    assert seen[0][1]['headers']['X-Content-SHA256'] == bundle.sha256
    restarted = AgentManager(repository)
    result = restarted.get_deployment(agent['id'], result['deployment_id'])
    assert result['status'] == 'succeeded'
    assert restarted.get_agent_by_id(agent['id'])['config_version'] == 'agent-final-hash'
    assert restarted.get_agent_by_id(agent['id'])['latest_deployment']['status'] == 'succeeded'


def test_old_agents_fail_closed_without_legacy_upload(env, monkeypatch):
    _, manager, agent, config = env
    monkeypatch.setattr('backend.agents.manager.requests.get', lambda *_a, **_k: response({}, 404))
    monkeypatch.setattr('backend.agents.manager.requests.post', lambda *_a, **_k: pytest.fail('legacy fallback'))
    result = manager.publish_deployment(agent['id'], minimal_bundle(agent, config))
    assert result['success'] is False
    assert result['http_status'] == 409
    assert result['status'] == 'failed'
    assert 'upgrade' in result['message']
    assert manager.get_agent_by_id(agent['id'])['config_version'] == '0'


def test_unknown_upload_keeps_queryable_identity(env, monkeypatch):
    repository, manager, agent, config = env
    monkeypatch.setattr('backend.agents.manager.requests.get', lambda *_a, **_k: response({
        'success': True, 'deployment_protocols': [1], 'service_type': 'mihomo'}))
    def timeout(*_args, **_kwargs):
        raise requests.Timeout('http://token-secret@agent')
    monkeypatch.setattr('backend.agents.manager.requests.post', timeout)
    result = manager.publish_deployment(agent['id'], minimal_bundle(agent, config))
    assert result['success'] is True
    assert result['status'] == 'unknown'
    assert 'token-secret' not in str(result)
    assert AgentManager(repository).get_agent_by_id(agent['id'])['latest_deployment']['deployment_id'] == 'test-release'
    assert manager.begin_deployment(agent['id'], 'test-release')['existing']
    assert manager.begin_deployment(agent['id'], 'another')['http_status'] == 409


def test_preparation_interruption_and_profile_binding(env):
    repository, manager, agent, _ = env
    manager.begin_deployment(agent['id'], 'interrupted')
    assert manager.get_deployment(agent['id'], 'interrupted')['status'] == 'preparing'
    restarted = AgentManager(repository)
    assert restarted.get_deployment(agent['id'], 'interrupted')['status'] == 'preparing'
    manager.finish_deployment_preparation(agent['id'], 'interrupted')
    assert restarted.get_deployment(agent['id'], 'interrupted')['status'] == 'failed'
    repository.create_profile({'id': 'other'})
    restarted.begin_deployment(agent['id'], 'bound')
    # Simulate an out-of-band edit to verify status/activation ownership too.
    repository.update_system_transaction(lambda system: system['agents'][0].update(profile_id='other'))
    assert restarted.get_deployment(agent['id'], 'bound')['http_status'] == 409
    assert restarted.activate_deployment(agent['id'], 'bound')['http_status'] == 409


def test_route_client_id_persisted_and_staging_repeated_request_is_idempotent(env, monkeypatch):
    from flask import Flask
    from backend.routes import register_blueprints
    repository, manager, agent, _ = env
    monkeypatch.setattr('backend.routes.agents.get_agent_manager', lambda: manager)
    monkeypatch.setattr('backend.routes.agents.generate_mihomo_config', lambda *_a, **_k: 'mode: rule')
    monkeypatch.setattr('backend.routes.agents.get_mihomo_provider_downloads', lambda *_a, **_k: [])
    monkeypatch.setattr('backend.routes.agents.get_mihomo_ruleset_downloads', lambda *_a, **_k: [])
    posts = []
    def post(url, **kwargs):
        posts.append(url)
        return response({'success': True, 'deployment_id': 'client-id', 'status': 'ready'}, 202)
    def get(url, **kwargs):
        if url.endswith('/capabilities'):
            return response({'success': True, 'deployment_protocols': [1], 'service_type': 'mihomo'})
        return response({'success': True, 'deployment_id': 'client-id', 'status': 'ready'})
    monkeypatch.setattr('backend.agents.manager.requests.post', post)
    monkeypatch.setattr('backend.agents.manager.requests.get', get)
    app = Flask(__name__)
    register_blueprints(app)
    client = app.test_client()
    path = f'/api/agents/{agent["id"]}/push-config'
    first = client.post(path, json={'deployment_id': 'client-id', 'restart': False})
    assert first.status_code == 202
    assert first.json['status'] == 'ready'
    assert client.post(path, json={'deployment_id': 'client-id'}).status_code == 202
    assert len(posts) == 1
    assert client.get(f'/api/agents/{agent["id"]}/deployments/client-id').json['status'] == 'ready'


def test_custom_installed_filename_negotiated_without_changing_bytes(env, monkeypatch):
    _, manager, agent, config = env
    bundle = minimal_bundle(agent, config)
    original_config = contents(bundle)['files/config.yaml']
    monkeypatch.setattr('backend.agents.manager.requests.get', lambda *_a, **_k: response({
        'success': True, 'deployment_protocols': [1], 'service_type': 'mihomo', 'config_path': 'custom.conf'}))
    def receive(url, **kwargs):
        with tarfile.open(fileobj=io.BytesIO(kwargs['data']), mode='r:gz') as archive:
            manifest = json.load(archive.extractfile('manifest.json'))
            assert manifest['config_path'] == 'custom.conf'
            assert archive.extractfile('files/custom.conf').read() == original_config
            assert 'files/config.yaml' not in archive.getnames()
            assert kwargs['headers']['X-Content-SHA256'] == hashlib.sha256(kwargs['data']).hexdigest()
        return response({'success': True, 'status': 'verifying'})
    monkeypatch.setattr('backend.agents.manager.requests.post', receive)
    assert manager.publish_deployment(agent['id'], bundle)['status'] == 'verifying'


def test_inline_rule_provider_and_local_file_failure(env):
    _, _, agent, config = env
    bundle = build_deployment_bundle(agent, config,
        'rule-providers: {inline: {type: inline, behavior: domain, payload: [example.test]}}')
    assert len(bundle.manifest['files']) == 1
    with pytest.raises(DeploymentPreparationError, match='managed rule source'):
        build_deployment_bundle(agent, config,
            'rule-providers: {local: {type: file, behavior: domain, path: ./rules/local.list}}')


def test_failed_rollback_is_blocking_and_can_reconcile_after_agent_recovery(env, monkeypatch):
    _, manager, agent, _ = env
    manager.begin_deployment(agent['id'], 'broken')
    manager.record_deployment(agent['id'], 'broken', {'success': False, 'status': 'rollback_failed', 'submitted': True})
    assert manager.begin_deployment(agent['id'], 'next')['http_status'] == 409
    monkeypatch.setattr('backend.agents.manager.requests.get', lambda *_a, **_k: response({
        'success': False, 'status': 'rolled_back'}))
    assert manager.get_deployment(agent['id'], 'broken')['status'] == 'rolled_back'
    assert manager.begin_deployment(agent['id'], 'next')['success']


@pytest.mark.parametrize('listen,expected', [(None, ':53'), ('127.0.0.1:23053', '127.0.0.1:23053')])
def test_mosdns_optional_listener_supports_isolated_deployment_without_changing_default(listen, expected):
    from backend.converters.mosdns import generate_mosdns_config
    config = {'rule_configs': [], 'mosdns': {'cache_enabled': False}}
    if listen is not None:
        config['mosdns']['listen_address'] = listen
    rendered = yaml.safe_load(generate_mosdns_config(config))
    listeners = [plugin['args']['listen'] for plugin in rendered['plugins']
                 if plugin['type'] in ('udp_server', 'tcp_server')]
    assert listeners == [expected, expected]


def test_late_pending_response_never_regresses_terminal_result(env):
    _, manager, agent, _ = env
    manager.begin_deployment(agent['id'], 'late-response')
    manager.record_deployment(agent['id'], 'late-response', {'status': 'unknown', 'message': 'Awaiting upload'})
    result = manager.record_deployment(agent['id'], 'late-response', {
        'success': True, 'status': 'succeeded', 'config_version': 'confirmed'})
    assert 'message' not in result
    late = manager.record_deployment(agent['id'], 'late-response', {'success': True, 'status': 'verifying'})
    assert late['status'] == 'succeeded'
    assert manager.get_agent_by_id(agent['id'])['config_version'] == 'confirmed'
    manager.finish_deployment_preparation(agent['id'], 'late-response')


@pytest.mark.parametrize('status', ['preparing', 'uploading', 'checking', 'ready', 'rollback_failed'])
def test_profile_rebinding_rejected_for_every_unfinished_deployment(env, status):
    from backend.agents.manager import AgentDeploymentConflict
    repository, manager, agent, _ = env
    repository.create_profile({'id': 'other'})
    manager.begin_deployment(agent['id'], 'bound-publish')
    manager.record_deployment(agent['id'], 'bound-publish', {'status': status})
    with pytest.raises(AgentDeploymentConflict):
        manager.update_agent(agent['id'], {'profile_id': 'other'})
    assert manager.get_agent_by_id(agent['id'])['profile_id'] == 'default'
    assert manager.update_agent(agent['id'], {'profile_id': 'default', 'name': 'renamed'})
    manager.finish_deployment_preparation(agent['id'], 'bound-publish')


def test_profile_guard_observes_publication_started_before_transaction(env, monkeypatch):
    from backend.agents.manager import AgentDeploymentConflict
    repository, manager, agent, _ = env
    repository.create_profile({'id': 'other'})
    original = repository.update_system_transaction
    def race(updater):
        monkeypatch.setattr(repository, 'update_system_transaction', original)
        manager.begin_deployment(agent['id'], 'raced-publish')
        return original(updater)
    monkeypatch.setattr(repository, 'update_system_transaction', race)
    with pytest.raises(AgentDeploymentConflict):
        manager.update_agent(agent['id'], {'profile_id': 'other'})
    assert manager.get_agent_by_id(agent['id'])['profile_id'] == 'default'
    manager.record_deployment(agent['id'], 'raced-publish', {'status': 'succeeded'})
    assert manager.update_agent(agent['id'], {'profile_id': 'other'})
    manager.finish_deployment_preparation(agent['id'], 'raced-publish')


@pytest.mark.parametrize('suffix', ['deployments', 'deployments/test/activate'])
def test_post_retries_only_exact_busy_with_identical_archive_and_identity(env, monkeypatch, suffix):
    _, manager, agent, _ = env
    calls = []
    sleeps = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            return response({'success': False, 'message': 'another service operation is in progress'}, 409)
        return response({'success': True, 'status': 'verifying'}, 202)
    monkeypatch.setattr('backend.agents.manager.requests.post', post)
    monkeypatch.setattr('backend.agents.manager.time.sleep', sleeps.append)
    body = b'archive-original-bytes'
    result = manager._deployment_request(agent, 'post', suffix, data=body,
                                        headers={'X-Deployment-ID': 'same-id', 'X-Content-SHA256': 'same-sha'})
    assert result['status'] == 'verifying'
    assert len(calls) == 2 and sleeps == [.1]
    assert calls[0] == calls[1]
    assert calls[1][1]['data'] is body


def test_busy_retries_bounded_other_conflicts_and_network_errors_never_retried(env, monkeypatch):
    _, manager, agent, _ = env
    calls = []
    def busy(url, **kwargs):
        calls.append(url)
        return response({'message': 'another service operation is in progress'}, 409)
    monkeypatch.setattr('backend.agents.manager.requests.post', busy)
    monkeypatch.setattr('backend.agents.manager.time.sleep', lambda _: None)
    monkeypatch.setattr('backend.agents.manager.time.monotonic', lambda: 0)
    assert manager._deployment_request(agent, 'post', 'deployments')['http_status'] == 409
    assert len(calls) == 21
    calls.clear()
    assert manager._deployment_request(agent, 'post', 'restart')['http_status'] == 409
    assert len(calls) == 1
    def conflict(url, **kwargs):
        calls.append(url)
        return response({'message': 'deployment ID already belongs to different content'}, 409)
    monkeypatch.setattr('backend.agents.manager.requests.post', conflict)
    calls.clear()
    assert manager._deployment_request(agent, 'post', 'deployments')['http_status'] == 409
    assert len(calls) == 1
    def timeout(url, **kwargs):
        calls.append(url)
        raise requests.Timeout('ambiguous outcome')
    monkeypatch.setattr('backend.agents.manager.requests.post', timeout)
    calls.clear()
    with pytest.raises(requests.Timeout):
        manager._deployment_request(agent, 'post', 'deployments')
    assert len(calls) == 1


def test_profile_binding_route_reports_deployment_conflict_as_409(env, monkeypatch):
    from flask import Flask
    from backend.routes import register_blueprints
    repository, manager, agent, _ = env
    repository.create_profile({'id': 'other'})
    manager.begin_deployment(agent['id'], 'binding-request')
    monkeypatch.setattr('backend.routes.agents.get_agent_manager', lambda: manager)
    app = Flask(__name__)
    register_blueprints(app)
    result = app.test_client().put(f'/api/agents/{agent["id"]}', json={'profile_id': 'other'})
    assert result.status_code == 409
    assert 'unfinished deployment' in result.json['message']
    assert manager.get_agent_by_id(agent['id'])['profile_id'] == 'default'
    manager.finish_deployment_preparation(agent['id'], 'binding-request')
