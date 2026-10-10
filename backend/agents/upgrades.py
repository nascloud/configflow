"""Durable, version-confirmed Agent updates; never confuse acceptance with success."""
from datetime import datetime
import hashlib
import os
from pathlib import Path
import time
import uuid

import requests

from .version import get_latest_version

TERMINAL = {'succeeded', 'failed', 'rolled_back', 'rollback_failed'}
ARCHES = ('linux-amd64', 'linux-arm64', 'linux-armv7')
STAGES = {'queued', 'downloading', 'verifying', 'backing_up', 'migrating',
          'replacing', 'restarting', 'checking', 'rolling_back'} | TERMINAL


def artifact_directory():
    configured = Path(os.getenv('AGENTS_STATIC_DIR', '/opt/configflow/static/agents'))
    return configured if configured.exists() else Path(__file__).resolve().parents[1] / 'static/agents'


def artifacts():
    result = {}
    for arch in ARCHES:
        path = artifact_directory() / ('configflow-agent-' + arch)
        if not path.is_file():
            continue
        data = path.read_bytes()
        if not data.startswith(b'\x7fELF') or len(data) > 128 * 1024 * 1024:
            continue
        result[arch] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    return result


def _request(agent, method, path, **kwargs):
    # Control-plane calls must not inherit the rule-download proxy.
    with requests.Session() as session:
        session.trust_env = False
        return session.request(method, f"http://{agent['host']}:{agent['port']}{path}",
                               headers={'Authorization': 'Bearer ' + agent['token']}, timeout=5, **kwargs)


def _save(manager, agent_id, update_id, patch):
    def update(agents):
        agent = next(item for item in agents if item['id'] == agent_id)
        state = agent.get('latest_upgrade') or {}
        if state.get('update_id') != update_id:
            raise ValueError('Upgrade ownership changed')
        if state.get('status') in TERMINAL - {'rollback_failed'}:
            return dict(state)
        state.update(patch)
        state['updated_at'] = time.time()
        agent['latest_upgrade'] = state
        return dict(state)
    return manager._update_agents(update)


def start(manager, agent_id):
    agent = manager.get_agent_by_id(agent_id)
    if not agent:
        raise ValueError('Agent not found')
    if agent.get('deployment_method') == 'docker':
        raise ValueError('Docker Agent 请更新镜像并重建容器；网页二进制更新不适用于 Docker')
    try:
        info_response = _request(agent, 'GET', '/api/upgrade-info')
        if info_response.status_code == 404:
            info = None
        else:
            info_response.raise_for_status()
            info = info_response.json()
    except (requests.RequestException, ValueError) as exc:
        raise ValueError('无法连接 Agent 并确认更新能力') from exc
    if info and (info.get('agent_id') != agent_id or info.get('deployment_method') != 'shell'):
        raise ValueError('Agent 身份或安装方式与网页记录不符')
    if not info and agent.get('deployment_method') != 'shell':
        raise ValueError('旧 Agent 未报告 Shell 安装方式，无法安全选择更新流程')
    available = artifacts()
    arch = info.get('architecture') if info else None
    if (arch and arch not in available) or (not info and set(available) != set(ARCHES)):
        raise ValueError('更新文件未准备就绪，请先更新 ConfigFlow 主服务镜像')
    state = {'update_id': uuid.uuid4().hex, 'target_version': get_latest_version(),
             'previous_version': agent.get('version', ''), 'status': 'queued',
             'agent_id': agent_id, 'started_at': time.time(), 'updated_at': time.time(),
             'protocol': 2 if info else 1, 'artifacts': available}

    def reserve(agents):
        current = next(item for item in agents if item['id'] == agent_id)
        previous = current.get('latest_upgrade') or {}
        if previous and (previous.get('status') not in TERMINAL or previous.get('status') == 'rollback_failed'):
            raise ValueError('已有更新正在执行或等待恢复，请先查询结果')
        deployment = current.get('latest_deployment') or {}
        if deployment and deployment.get('status') not in {'succeeded', 'failed', 'rolled_back'}:
            raise ValueError('存在未结束的配置发布，请先完成或恢复')
        current['latest_upgrade'] = dict(state)
    manager._update_agents(reserve)
    payload = {'version': state['target_version'], 'update_id': state['update_id']}
    if info:
        payload.update(available[arch])
    try:
        response = _request(agent, 'POST', '/api/update', json=payload)
        if response.status_code not in (200, 202):
            try:
                message = response.json().get('message', 'Agent rejected upgrade')
            except ValueError:
                message = 'Agent rejected upgrade'
            return _save(manager, agent_id, state['update_id'], {'status': 'failed', 'error': message})
    except requests.RequestException:
        # The response may have been lost after acceptance. Keep the same ID
        # pending and reconcile with the Agent instead of resubmitting a new job.
        return _save(manager, agent_id, state['update_id'], {'status': 'checking', 'error': '请求结果待确认，正在查询 Agent'})
    return _save(manager, agent_id, state['update_id'], {'status': 'downloading'})


def poll(manager, agent_id):
    agent = manager.get_agent_by_id(agent_id)
    if not agent or not agent.get('latest_upgrade'):
        raise ValueError('No upgrade recorded')
    state = dict(agent['latest_upgrade'])
    if state['status'] in TERMINAL - {'rollback_failed'}:
        return state
    if state.get('recovery_reported_at') and agent.get('version') == state['previous_version']:
        try:
            confirmed = datetime.fromisoformat(agent.get('last_heartbeat', '')).timestamp()
            if confirmed > state['recovery_reported_at']:
                return _save(manager, agent_id, state['update_id'], {'status': 'rolled_back'})
        except ValueError:
            pass
    try:
        response = _request(agent, 'GET', '/api/update') if state['protocol'] == 2 else None
        if response is not None and response.status_code == 200:
            result = response.json()
            if result.get('update_id') == state['update_id'] and result.get('agent_id') == agent_id and result.get('status') in STAGES:
                if result['status'] != 'succeeded':
                    return _save(manager, agent_id, state['update_id'], {
                        'status': result['status'], 'error': str(result.get('error', ''))[:2048]})
        info_response = _request(agent, 'GET', '/api/upgrade-info')
        if info_response.status_code == 200:
            info = info_response.json()
            if info.get('agent_id') == agent_id and info.get('version') == state['target_version'] and info.get('migration_ready') is True:
                # Protocol 2 also needs the independent worker's final result.
                if state['protocol'] == 1 or response.status_code == 200 and result.get('update_id') == state['update_id'] and result.get('status') == 'succeeded':
                    return _save(manager, agent_id, state['update_id'], {'status': 'succeeded', 'error': ''})
    except (requests.RequestException, ValueError):
        pass
    deadline = state['started_at'] + 600
    if time.time() > deadline:
        # The Agent did not report this job in progress. A heartbeat sent after
        # the deadline that still carries the old version proves the update
        # never took effect, so release the lock and allow a retry.
        if agent.get('version') == state['previous_version'] and _heartbeat_after(agent, deadline):
            return _save(manager, agent_id, state['update_id'], {
                'status': 'failed', 'error': f"更新未生效：Agent 仍在运行 {state['previous_version']}，可重新更新；详情请查看 Agent 日志"})
        # Do not claim rollback when the old executable cannot be reached.
        if state['status'] != 'unknown':
            return _save(manager, agent_id, state['update_id'], {'status': 'unknown', 'error': '升级结果未能确认，正在继续查询；请查看 Agent 日志'})
    return state


def _heartbeat_after(agent, moment):
    try:
        return datetime.fromisoformat(agent.get('last_heartbeat', '')).timestamp() > moment
    except ValueError:
        return False


def report_legacy(manager, agent_id, payload):
    agent = manager.get_agent_by_id(agent_id)
    state = agent.get('latest_upgrade') or {}
    if state.get('protocol') != 1 or payload.get('update_id') != state.get('update_id') or payload.get('status') != 'rolling_back':
        raise ValueError('Invalid legacy upgrade report')
    return _save(manager, agent_id, state['update_id'], {'status': 'rolling_back', 'recovery_reported_at': time.time(), 'error': str(payload.get('error', 'Migration failed'))[:2048]})
