"""Upgrade persistence, acceptance/health distinction and failure concurrency."""
from datetime import datetime
import json
import time

import pytest
import requests

from backend.agents import upgrades
from backend.agents.manager import AgentManager
from backend.common import config as config_module
from backend.common.config_repository import ProfileRepository


def response(payload, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result._content_consumed = True
    return result


@pytest.fixture
def env(tmp_path, monkeypatch):
    repo = ProfileRepository(tmp_path)
    config_module.set_repository(repo)
    manager = AgentManager(repo)
    agent = manager.register_agent({'name': 'upgrade', 'host': 'localhost', 'port': 1234,
                                   'service_type': 'mihomo', 'deployment_method': 'shell', 'version': '1.1.0-go'})
    ident = agent['id']
    monkeypatch.setattr(upgrades, 'artifacts', lambda: {arch: {'sha256': 'a' * 64, 'size': 100} for arch in upgrades.ARCHES})
    return repo, manager, ident


def test_acceptance_is_not_success_and_reserves_across_restart(env, monkeypatch):
    repo, manager, ident = env
    info = {'agent_id': ident, 'deployment_method': 'shell', 'architecture': 'linux-amd64', 'version': '1.1.0-go'}
    monkeypatch.setattr(upgrades, '_request', lambda a, m, p, **kw: response(info if p.endswith('info') else {}, 200))
    state = upgrades.start(manager, ident)
    assert state['status'] == 'downloading'
    restarted = AgentManager(repo)
    with pytest.raises(ValueError, match='已有更新'):
        upgrades.start(restarted, ident)
    assert restarted.get_agent_by_id(ident)['latest_upgrade']['update_id'] == state['update_id']


def test_worker_success_still_requires_version_identity_and_migration(env, monkeypatch):
    _, manager, ident = env
    state = {'update_id': 'job', 'target_version': upgrades.get_latest_version(), 'previous_version': '1.1.0-go',
             'status': 'checking', 'protocol': 2, 'started_at': time.time()}
    manager._update_agents(lambda agents: agents[0].update(latest_upgrade=state))
    info = {'agent_id': ident, 'version': state['target_version'], 'migration_ready': False}
    monkeypatch.setattr(upgrades, '_request', lambda a, m, p, **kw: response(info if p.endswith('info') else {'update_id': 'job', 'agent_id': ident, 'status': 'succeeded'}))
    assert upgrades.poll(manager, ident)['status'] == 'checking'
    info['migration_ready'] = True
    info['agent_id'] = 'wrong-agent'
    assert upgrades.poll(manager, ident)['status'] == 'checking'
    info['agent_id'] = ident
    assert upgrades.poll(manager, ident)['status'] == 'succeeded'


def test_legacy_rollback_requires_fresh_authenticated_heartbeat(env, monkeypatch):
    _, manager, ident = env
    state = {'update_id': 'job', 'target_version': upgrades.get_latest_version(), 'previous_version': '1.1.0-go',
             'status': 'downloading', 'protocol': 1, 'started_at': time.time()}
    manager._update_agents(lambda agents: agents[0].update(latest_upgrade=state))
    def request(a, m, p, **kw):
        assert p != '/api/update', 'legacy GET endpoint is unsupported'
        return response({}, 404)
    monkeypatch.setattr(upgrades, '_request', request)
    assert upgrades.report_legacy(manager, ident, {'update_id': 'job', 'status': 'rolling_back'})['status'] == 'rolling_back'
    assert upgrades.poll(manager, ident)['status'] == 'rolling_back'
    manager.update_heartbeat(ident, {'version': '1.1.0-go'})
    assert upgrades.poll(manager, ident)['status'] == 'rolled_back'


def test_unknown_timeout_blocks_retry_and_does_not_claim_rollback(env, monkeypatch):
    _, manager, ident = env
    state = {'update_id': 'job', 'target_version': upgrades.get_latest_version(), 'previous_version': '1.1.0-go',
             'status': 'checking', 'protocol': 2, 'started_at': time.time() - 601}
    manager._update_agents(lambda agents: agents[0].update(latest_upgrade=state))
    monkeypatch.setattr(upgrades, '_request', lambda *a, **kw: response({}, 404))
    assert upgrades.poll(manager, ident)['status'] == 'unknown'
    with pytest.raises(ValueError, match='已有更新'):
        upgrades.start(manager, ident)


def test_docker_is_not_sent_binary_update(env, monkeypatch):
    _, manager, ident = env
    manager._update_agents(lambda agents: agents[0].update(deployment_method='docker'))
    monkeypatch.setattr(upgrades, '_request', lambda *a, **kw: pytest.fail('contacted Docker binary updater'))
    with pytest.raises(ValueError, match='Docker'):
        upgrades.start(manager, ident)


def test_failed_rollback_can_confirm_later_recovery(env, monkeypatch):
    _, manager, ident = env
    state = {'update_id': 'job', 'target_version': upgrades.get_latest_version(), 'previous_version': '1.1.0-go',
             'status': 'rollback_failed', 'protocol': 2, 'started_at': time.time()}
    manager._update_agents(lambda agents: agents[0].update(latest_upgrade=state))
    monkeypatch.setattr(upgrades, '_request', lambda *a, **kw: response({'update_id':'job', 'agent_id':ident, 'status':'rolled_back'}))
    assert upgrades.poll(manager, ident)['status'] == 'rolled_back'
