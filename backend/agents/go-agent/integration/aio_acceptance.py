#!/usr/bin/env python3
"""Isolated real Docker AIO acceptance for Mihomo plus either MosDNS or OxiDNS and their Agents.

Run on the disposable Linux acceptance host:
  python3 aio_acceptance.py --source-root /path/to/prepared-root --root /path/to/aio-results

source-root contains bin/configflow-agent and src/ repository files. Uses only
ports 23280/23281/23282/23290/23289/23253/23254 and its dedicated named container. The container
is removed in finally; persistent test files, logs and JSON report remain.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid

from acceptance import Target, bundle, dns_answer

NAME = 'configflow-acceptance-aio-final'
IMAGE = 'thsrite/config-flow-agent:latest'
TERMINAL = {'succeeded', 'failed', 'rolled_back', 'rollback_failed'}


def run(*args, check=True):
    result = subprocess.run([str(arg) for arg in args], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=90)
    text = result.stdout.decode(errors='replace')
    if check and result.returncode:
        raise RuntimeError(f'Acceptance command failed ({result.returncode}): {text[-8000:]}')
    return result.returncode, text


class AioTarget(Target):
    def __init__(self, root, docker, kind, run_id):
        super().__init__(root, docker, 'aio', kind, run_id)
        self.base = 23200
        self.agent_port = {'mihomo': 23280, 'mosdns': 23281, 'oxidns': 23282}[kind]
        self.core_port = {'mihomo': 23290, 'mosdns': 23253, 'oxidns': 23254}[kind]
        self.agent_id = NAME + '-' + kind
        self.token = 'aio-acceptance-' + kind + '-token'
        self.url = 'http://127.0.0.1:' + str(self.agent_port)

    def files(self, *args, **kwargs):
        files = super().files(*args, **kwargs)
        if self.kind == 'mihomo':
            value = json.loads(files['config.yaml'])
            value['dns'] = {'enable': False}
            files['config.yaml'] = json.dumps(value).encode()
        return files

    def request(self, path, method='GET', data=None, headers=None, token=None):
        request = urllib.request.Request(self.url + path, data=data, method=method,
            headers={'Authorization': 'Bearer ' + (token or self.token), **(headers or {})})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=15) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def upload(self, files):
        ident, content = bundle(self, files)
        code, state = self.request('/api/deployments', 'POST', content,
            {'Content-Type': 'application/gzip', 'X-Deployment-ID': ident,
             'X-Content-SHA256': hashlib.sha256(content).hexdigest(), 'X-Activate': 'true'})
        assert code in (200, 202), (code, state)
        return ident

    def wait(self, ident, expected):
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            code, state = self.request('/api/deployments/' + ident)
            assert code == 200, state
            if state['status'] in expected:
                return state
            if state['status'] in TERMINAL:
                raise AssertionError((self.kind, expected, state))
            time.sleep(.1)
        raise AssertionError((self.kind, 'deployment timeout'))

    def probe(self):
        if self.kind in ('mosdns', 'oxidns'):
            return dns_answer(self.core_port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:23290/version', timeout=2) as response:
            assert json.load(response).get('version')
        with socket.create_connection(('127.0.0.1', 23289), timeout=2) as connection:
            connection.sendall(b'\x05\x02\x00\x02')
            assert connection.recv(2) in (b'\x05\x00', b'\x05\x02')

    def wait_ready(self, require_core=True):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                code, value = self.request('/api/capabilities')
                if code == 200 and 1 in value.get('deployment_protocols', []):
                    if require_core:
                        self.probe()
                    return
            except Exception:
                pass
            time.sleep(.25)
        raise AssertionError('Agent/core did not become ready: ' + self.kind)


def monitor(target, action):
    stop = threading.Event()
    checks = []
    failures = []
    def worker():
        while not stop.is_set():
            try:
                target.probe()
                checks.append(True)
            except Exception as error:
                failures.append(str(error))
            stop.wait(.15)
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        result = action()
    finally:
        stop.set()
        thread.join(timeout=3)
    assert checks and not failures, {'successful_checks': len(checks), 'errors': failures}
    return result, len(checks)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--docker', default='/usr/local/bin/docker')
    parser.add_argument('--dns-core', choices=['mosdns', 'oxidns'], default='mosdns',
                        help='Run each DNS core in a separate AIO invocation; the entrypoint forbids enabling both together.')
    args = parser.parse_args()
    dns_kind = args.dns_core
    run_id = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6]
    args.root.mkdir(parents=True, exist_ok=True)
    working = args.root / run_id
    working.mkdir()
    report = {'suite': 'real_docker_aio', 'run_id': run_id, 'success': False, 'tests': [],
              'agent_sha256': hashlib.sha256((args.source_root / 'bin/configflow-agent').read_bytes()).hexdigest()}
    created = False
    targets = {kind: AioTarget(args.root, args.docker, kind, run_id) for kind in ('mihomo', dns_kind)}
    identity = working / 'identities'
    identity.mkdir()
    supervisor = working / 'supervisor'
    shutil.copytree(args.source_root / 'src/backend/agents/go-agent/supervisor', supervisor)
    entrypoint = working / 'entrypoint.sh'
    shutil.copy2(args.source_root / 'src/docker/docker-agent-entrypoint-service.sh', entrypoint)
    entrypoint.chmod(0o755)
    def record(name, **details):
        name = name.replace('mosdns', dns_kind)
        report['tests'].append({'name': name, 'passed': True, **details})
        print('PASS', name, flush=True)
    def persisted_identities():
        return {kind: json.loads((identity / ('config-' + kind + '.json')).read_text())
                for kind in targets}
    def assert_identities():
        for kind, value in persisted_identities().items():
            assert value['agent_id'] == targets[kind].agent_id
            assert value['token'] == targets[kind].token
    def core_pid(kind):
        return run(args.docker, 'exec', NAME, 'supervisorctl', '-c', '/etc/supervisor/supervisord.conf', 'pid', kind)[1].strip()
    try:
        code, _ = run(args.docker, 'inspect', NAME, check=False)
        assert code != 0, 'Dedicated acceptance container name already exists; refusing to replace it'
        for target in targets.values():
            target.write_initial()
            # Simulate interruption before the first durable transaction record.
            (target.live / '.configflow-deployments' / 'interrupted-first-record').mkdir(parents=True)
            (identity / ('config-' + target.kind + '.json')).write_text(json.dumps({
                'agent_id': target.agent_id, 'token': target.token}))
        command = [args.docker, 'run', '-d', '--name', NAME, '--network', 'host',
            '-v', str(identity) + ':/opt/configflow-agent',
            '-v', str(supervisor) + ':/etc/supervisor/conf.d',
            '-v', str(entrypoint) + ':/entrypoint.sh:ro',
            '-v', str(args.source_root / 'bin/configflow-agent') + ':/usr/local/bin/configflow-agent:ro',
            '-e', 'ENABLE_MIHOMO=true', '-e', 'ENABLE_MOSDNS=' + str(dns_kind == 'mosdns').lower(),
            '-e', 'ENABLE_OXIDNS=' + str(dns_kind == 'oxidns').lower(),
            '-e', 'AGENT_HOST=127.0.0.1', '-e', 'AGENT_MIHOMO_PORT=23280',
            '-e', 'AGENT_' + dns_kind.upper() + '_PORT=' + str(targets[dns_kind].agent_port),
            '-e', 'AGENT_MIHOMO_NAME=' + targets['mihomo'].agent_id,
            '-e', 'AGENT_' + dns_kind.upper() + '_NAME=' + targets[dns_kind].agent_id,
            '-e', 'SERVER_URL=http://127.0.0.1:1', '-e', 'HEARTBEAT_INTERVAL=300',
            '-e', 'DEPLOYMENT_HEALTH_TIMEOUT=8', '-e', dns_kind.upper() + '_HEALTH_DNS_NAME=acceptance.test']
        for target in targets.values():
            command += ['-v', str(target.live) + ':/etc/' + target.kind]
        command.append(IMAGE)
        run(*command)
        created = True
        for target in targets.values():
            target.wait_ready()
            orphan = target.live / '.configflow-deployments' / 'interrupted-first-record'
            assert orphan.is_dir() and not list(orphan.iterdir()), 'Startup modified the interrupted first record'
            code, output = run(args.docker, 'exec', NAME, '/usr/local/bin/configflow-agent', '-config',
                               '/opt/configflow-agent/config-' + target.kind + '.json', '-check-deployments')
            assert code == 0 and 'Deployment check requires attention:' in output, output
            assert orphan.is_dir() and not list(orphan.iterdir()), 'Read-only check modified the first record'
        record('empty_initial_transaction_is_preserved_without_blocking_core_boot')
        for target in targets.values():
            run(args.docker, 'exec', NAME, 'supervisorctl', '-c', '/etc/supervisor/supervisord.conf',
                'stop', 'agent-' + target.kind)
            run(args.docker, 'exec', NAME, '/usr/local/bin/configflow-agent', '-config',
                '/opt/configflow-agent/config-' + target.kind + '.json', '-recover-and-exit')
            assert not (target.live / '.configflow-deployments' / 'interrupted-first-record').exists()
            run(args.docker, 'exec', NAME, 'supervisorctl', '-c', '/etc/supervisor/supervisord.conf',
                'start', 'agent-' + target.kind)
            target.wait_ready()
        record('explicit_recovery_cleans_empty_initial_transactions')
        assert_identities()
        assert targets['mihomo'].request('/api/capabilities', token=targets[dns_kind].token)[0] == 401
        assert targets[dns_kind].request('/api/capabilities', token=targets['mihomo'].token)[0] == 401
        record('both_agents_and_real_cores_with_separate_persistent_identity')

        def publish(target):
            ident = target.upload(target.files('new'))
            target.wait(ident, {'succeeded'})
            target.probe()
            assert (target.live / 'cache.keep').read_bytes() == b'preserve-runtime-state'
            return ident
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = {kind: pool.submit(publish, target) for kind, target in targets.items()}
            committed = {kind: task.result() for kind, task in futures.items()}
        assert targets[dns_kind].probe() == '192.0.2.20'
        record('both_services_publish_concurrently_and_keep_runtime_files')
        new_mihomo = (targets['mihomo'].live / 'config.yaml').read_bytes()
        new_dns = (targets[dns_kind].live / 'config.yaml').read_bytes()

        pid = core_pid(dns_kind)
        def reject_bad_mihomo():
            ident = targets['mihomo'].upload(targets['mihomo'].files('bad', invalid=True))
            return targets['mihomo'].wait(ident, {'failed'})
        _, checks = monitor(targets[dns_kind], reject_bad_mihomo)
        assert pid == core_pid(dns_kind)
        assert (targets[dns_kind].live / 'config.yaml').read_bytes() == new_dns
        assert (targets['mihomo'].live / 'config.yaml').read_bytes() == new_mihomo
        record('mihomo_invalid_config_does_not_interrupt_mosdns', live_probes=checks)

        pid = core_pid('mihomo')
        def rollback_bad_dns():
            ident = targets[dns_kind].upload(targets[dns_kind].files('bad', fail_start=True))
            return targets[dns_kind].wait(ident, {'rolled_back'})
        _, checks = monitor(targets['mihomo'], rollback_bad_dns)
        assert pid == core_pid('mihomo')
        assert targets[dns_kind].probe() == '192.0.2.20'
        assert (targets['mihomo'].live / 'config.yaml').read_bytes() == new_mihomo
        record('mosdns_start_failure_rolls_back_without_interrupting_mihomo', live_probes=checks)

        run(args.docker, 'restart', NAME)
        for target in targets.values():
            target.wait_ready()
        assert_identities()
        assert targets[dns_kind].probe() == '192.0.2.20'
        record('container_restart_preserves_both_identities_and_service_versions')

        # Simulate the durable on-disk checkpoint of an interrupted replacement.
        # Boot must preserve it for explicit recovery without blocking the other
        # core or either Agent API. This fixture does not kill a mid-copy process.
        run(args.docker, 'stop', NAME)
        state_path = targets['mihomo'].live / '.configflow-deployments' / committed['mihomo'] / 'state.json'
        state = json.loads(state_path.read_text())
        assert state['backup_complete']
        state['status'], state['success'] = 'replacing', False
        with state_path.open('w') as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        (targets['mihomo'].live / 'config.yaml').write_text('corrupt: [')
        interrupted_state = state_path.read_bytes()
        run(args.docker, 'start', NAME)
        targets['mihomo'].wait_ready(require_core=False)
        targets[dns_kind].wait_ready()
        assert state_path.read_bytes() == interrupted_state
        assert (targets['mihomo'].live / 'config.yaml').read_text() == 'corrupt: ['
        assert (targets[dns_kind].live / 'config.yaml').read_bytes() == new_dns
        assert targets[dns_kind].probe() == '192.0.2.20'
        assert targets['mihomo'].request('/api/restart', 'POST', b'')[0] == 409
        record('aio_boot_preserves_interrupted_mihomo_and_keeps_mosdns_available')
        pid = core_pid(dns_kind)
        for flag in ('-check-deployments', '-recover-only'):
            code, output = run(args.docker, 'exec', NAME, '/usr/local/bin/configflow-agent', '-config',
                               '/opt/configflow-agent/config-mihomo.json', flag)
            assert code == 0 and 'Deployment check requires attention:' in output, output
            assert state_path.read_bytes() == interrupted_state
            assert (targets['mihomo'].live / 'config.yaml').read_text() == 'corrupt: ['
        def recover_mihomo():
            run(args.docker, 'exec', NAME, 'supervisorctl', '-c', '/etc/supervisor/supervisord.conf',
                'stop', 'agent-mihomo')
            run(args.docker, 'exec', NAME, '/usr/local/bin/configflow-agent', '-config',
                '/opt/configflow-agent/config-mihomo.json', '-recover-and-exit')
            run(args.docker, 'exec', NAME, 'supervisorctl', '-c', '/etc/supervisor/supervisord.conf',
                'start', 'agent-mihomo')
            targets['mihomo'].wait_ready()
        _, checks = monitor(targets[dns_kind], recover_mihomo)
        assert pid == core_pid(dns_kind)
        targets['mihomo'].wait(committed['mihomo'], {'rolled_back'})
        assert (targets['mihomo'].live / 'config.yaml').read_bytes() == targets['mihomo'].files()['config.yaml']
        assert (targets[dns_kind].live / 'config.yaml').read_bytes() == new_dns
        assert targets[dns_kind].probe() == '192.0.2.20'
        assert_identities()
        if dns_kind == 'oxidns':
            assert (targets[dns_kind].live / 'state/learned.txt').read_bytes() == b'full:preserve.runtime.test\n'
        record('aio_explicit_mihomo_recovery_preserves_mosdns', live_probes=checks)
        report['success'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        if created:
            (working / 'container.log').write_text(run(args.docker, 'logs', NAME, check=False)[1])
            run(args.docker, 'rm', '-f', NAME, check=False)
        path = working / 'report.json'
        path.write_text(json.dumps(report, indent=2))
        (args.root / 'latest-report.json').write_text(json.dumps(report, indent=2))
        print('REPORT', path, flush=True)


if __name__ == '__main__':
    main()
