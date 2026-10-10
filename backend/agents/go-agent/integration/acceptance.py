#!/usr/bin/env python3
"""Real Linux acceptance: native systemd + Docker, actual Mihomo and MosDNS.

Run as root on a disposable test host with --root pointing to a prepared directory
containing bin/configflow-agent and src/ (repository source). Uses dedicated ports,
runtime systemd units and named temporary containers; never edits production units.
All reports and service logs stay under --root after test services are removed.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
import uuid

TERMINAL = {'succeeded', 'failed', 'rolled_back', 'rollback_failed'}
TOKEN = 'isolated-acceptance-token'
IMAGE = 'thsrite/config-flow-agent:latest'


def run(*args, check=True, **kwargs):
    result = subprocess.run([str(x) for x in args], check=False, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, **kwargs)
    output = result.stdout.decode(errors='replace')
    if check and result.returncode:
        raise RuntimeError('Acceptance command failed (%s): %s' % (result.returncode, output[-12000:]))
    return output


def request(url, method='GET', body=None, headers=None):
    req = urllib.request.Request(url, data=body, method=method,
        headers={'Authorization': 'Bearer ' + TOKEN, **(headers or {})})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def service_request(url, method='POST', body=None, headers=None):
    deadline = time.monotonic() + 5
    while True:
        code, result = request(url, method, body, headers)
        # A completed/staged transaction may still be releasing its process lock.
        if code != 409 or result.get('message') != 'another service operation is in progress' or time.monotonic() >= deadline:
            return code, result
        time.sleep(.1)


def bundle(target, files, deployment_id=None):
    deployment_id = deployment_id or uuid.uuid4().hex
    manifest = dict(protocol_version=1, deployment_id=deployment_id,
                    agent_id=target.agent_id, profile_id='acceptance',
                    service_type=target.kind, config_path='config.yaml', files=[])
    for name, data in sorted(files.items()):
        manifest['files'].append(dict(path=name, size=len(data),
            sha256=hashlib.sha256(data).hexdigest(), role='config' if name == 'config.yaml' else 'rule'))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, data in [('manifest.json', json.dumps(manifest).encode())] + [('files/' + n, d) for n, d in sorted(files.items())]:
            item = tarfile.TarInfo(name)
            item.size, item.mode = len(data), 0o600
            archive.addfile(item, io.BytesIO(data))
    return deployment_id, buffer.getvalue()


def dns_answer(port):
    ident = os.urandom(2)
    query = ident + b'\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'
    query += b'\x0aacceptance\x04test\x00\x00\x01\x00\x01'
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(2)
        sock.sendto(query, ('127.0.0.1', port))
        answer = sock.recv(4096)
    assert answer[:2] == ident and answer[3] & 15 == 0
    assert struct.unpack('!H', answer[6:8])[0] > 0
    return socket.inet_ntoa(answer[-4:])


class Target:
    def __init__(self, root, docker, mode, kind, run_id):
        self.root, self.docker, self.mode, self.kind = root, docker, mode, kind
        self.name = 'configflow-acceptance-' + mode + '-' + kind
        self.agent_id = self.name
        self.path = root / 'runs' / run_id / mode / kind
        self.live = self.path / 'live'
        self.identity = self.path / 'identity'
        self.path.mkdir(parents=True, exist_ok=True)
        self.live.mkdir(exist_ok=True)
        self.identity.mkdir(exist_ok=True)
        self.base = 23000 if mode == 'native' else 23100
        self.agent_port = self.base + (80 if kind == 'mihomo' else 81)
        self.core_port = self.base + (90 if kind == 'mihomo' else 53)
        self.url = 'http://127.0.0.1:' + str(self.agent_port)
        self.unit = self.name + '.service'
        self.agent_process = None
        self.events = []

    def files(self, version='old', fail_start=False, invalid=False):
        if self.kind == 'mihomo':
            controller = ('192.0.2.222' if fail_start else '127.0.0.1') + ':' + str(self.core_port)
            config = {'mixed-port': self.base + 89, 'external-controller': controller,
                      'log-level': 'warning', 'ipv6': False, 'mode': 'rule',
                      'rule-providers': {'sample': {'type': 'file', 'behavior': 'domain',
                         'format': 'text', 'path': './ruleset/sample.list'}},
                      'rules': ['RULE-SET,sample,' + ('NONEXISTENT' if invalid else 'DIRECT'), 'MATCH,DIRECT']}
            return {'config.yaml': json.dumps(config).encode(),
                    'ruleset/sample.list': (version + '.test\n').encode(),
                    'ruleset/empty.list': b''}
        plugins = [
            {'tag': 'hosts', 'type': 'nonexistent_plugin' if fail_start else 'hosts',
             'args': {'files': ['./rules/missing.txt' if invalid else './rules/hosts.txt']}},
            {'tag': 'main', 'type': 'sequence', 'args': [{'exec': '$hosts'}]},
            {'tag': 'udp', 'type': 'udp_server', 'args': {'entry': 'main', 'listen': '127.0.0.1:' + str(self.core_port)}}]
        address = '192.0.2.10' if version == 'old' else '192.0.2.20'
        return {'config.yaml': json.dumps({'log': {'level': 'error'}, 'plugins': plugins}).encode(),
                'rules/hosts.txt': ('acceptance.test ' + address + '\n').encode(),
                'rules/empty.txt': b''}

    def write_initial(self):
        for name, data in self.files().items():
            dest = self.live / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        (self.live / 'cache.keep').write_bytes(b'preserve-runtime-state')

    def start(self):
        self.write_initial()
        if self.mode == 'docker':
            (self.identity / ('config-' + self.kind + '.json')).write_text(json.dumps({'agent_id': self.agent_id, 'token': TOKEN}))
            supervisor = self.path / 'supervisor'
            shutil.copytree(self.root / 'src/backend/agents/go-agent/supervisor', supervisor)
            os.chmod(self.root / 'src/docker/docker-agent-entrypoint-service.sh', 0o755)
            run(self.docker, 'run', '-d', '--name', self.name, '--network', 'host',
                '-v', str(self.live) + ':/etc/' + self.kind,
                '-v', str(self.identity) + ':/opt/configflow-agent',
                '-v', str(supervisor) + ':/etc/supervisor/conf.d',
                '-v', str(self.root / 'bin/configflow-agent') + ':/usr/local/bin/configflow-agent:ro',
                '-v', str(self.root / 'src/docker/docker-agent-entrypoint-service.sh') + ':/entrypoint.sh:ro',
                '-e', 'SERVICE_TYPE=' + self.kind, '-e', 'AGENT_HOST=127.0.0.1',
                '-e', 'AGENT_PORT=' + str(self.agent_port), '-e', 'SERVER_URL=http://127.0.0.1:1',
                '-e', 'DEPLOYMENT_HEALTH_TIMEOUT=8', '-e', 'HEARTBEAT_INTERVAL=300',
                '-e', self.kind.upper() + '_HEALTH_DNS_NAME=acceptance.test', IMAGE)
        else:
            binary = self.root / 'bin' / self.kind
            command = str(binary) + ' -d ' + str(self.live)
            if self.kind == 'mosdns':
                command = str(binary) + ' start -c ' + str(self.live / 'config.yaml') + ' -d ' + str(self.live)
            unit = '[Unit]\nDescription=Isolated ConfigFlow acceptance core\n[Service]\nType=simple\nExecStart=' + command + '\nRestart=no\n'
            Path('/run/systemd/system', self.unit).write_text(unit)
            run('systemctl', 'daemon-reload')
            run('systemctl', 'start', self.unit)
            config = dict(server_url='http://127.0.0.1:1', agent_name=self.name,
                agent_host='127.0.0.1', agent_port=self.agent_port, service_type=self.kind,
                service_name=self.unit, config_path=str(self.live / 'config.yaml'),
                service_manager='systemd', service_unit=self.unit, service_binary=str(binary),
                health_dns_name='acceptance.test', deployment_health_timeout=8,
                agent_id=self.agent_id, token=TOKEN, heartbeat_interval=300, enable_metrics=False)
            (self.identity / 'config.json').write_text(json.dumps(config))
            self.start_native_agent()
        self.wait_ready()

    def start_native_agent(self):
        log = open(self.path / 'agent.log', 'ab')
        self.agent_process = subprocess.Popen([str(self.root / 'bin/configflow-agent'), '-config', str(self.identity / 'config.json')], stdout=log, stderr=log)
        log.close()

    def wait_ready(self):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            try:
                code, data = request(self.url + '/api/capabilities')
                if code == 200 and 1 in data['deployment_protocols']:
                    self.probe()
                    return
            except Exception:
                pass
            time.sleep(.25)
        raise AssertionError('Service failed to become ready: ' + self.name)

    def probe(self):
        if self.kind == 'mihomo':
            code, data = request('http://127.0.0.1:' + str(self.core_port) + '/version')
            assert code == 200 and data.get('version')
        else:
            return dns_answer(self.core_port)

    def upload(self, files, activate=True, deployment_id=None):
        ident, archive = bundle(self, files, deployment_id)
        headers = {'Content-Type': 'application/gzip', 'X-Deployment-ID': ident,
                   'X-Content-SHA256': hashlib.sha256(archive).hexdigest(), 'X-Activate': str(activate).lower()}
        code, result = service_request(self.url + '/api/deployments', 'POST', archive, headers)
        assert code in (200, 202), (code, result)
        return ident, archive, headers

    def wait(self, ident, expected):
        deadline = time.monotonic() + 100
        stages = []
        while time.monotonic() < deadline:
            code, data = request(self.url + '/api/deployments/' + ident)
            assert code == 200, data
            state = data['status']
            if not stages or stages[-1] != state:
                stages.append(state)
            if state in expected:
                self.events.append({'id': ident, 'stages': stages, 'result': state})
                return data
            if state in TERMINAL:
                raise AssertionError((self.name, expected, data))
            time.sleep(.08)
        raise AssertionError((self.name, 'deployment timed out', stages))

    def tests(self):
        results = []
        previous = (self.live / 'config.yaml').read_bytes()
        committed_files = self.files('new')
        ident, archive, headers = self.upload(committed_files, activate=False)
        self.wait(ident, {'ready'})
        assert (self.live / 'config.yaml').read_bytes() == previous
        self.probe()
        results.append('stage_without_changing_running_service')
        code, activation = service_request(self.url + '/api/deployments/' + ident + '/activate', 'POST', b'')
        assert code == 202, (code, activation)
        self.wait(ident, {'succeeded'})
        self.probe()
        if self.kind == 'mosdns':
            assert self.probe() == '192.0.2.20'
        assert (self.live / 'cache.keep').read_bytes() == b'preserve-runtime-state'
        results.append('real_core_publish_and_runtime_preserved')
        code, repeated = request(self.url + '/api/deployments', 'POST', archive, headers)
        assert code == 200 and repeated['status'] == 'succeeded'
        different = dict(headers, **{'X-Content-SHA256': '0' * 64})
        assert request(self.url + '/api/deployments', 'POST', archive, different)[0] == 409
        results.append('idempotent_retry_and_conflict')
        if self.kind == 'mihomo':
            source = self.path / 'mrs-source.txt'
            mrs = self.path / 'sample.mrs'
            source.write_text('+.binary-acceptance.test\n')
            run(self.root / 'bin/mihomo', 'convert-ruleset', 'domain', 'text', source, mrs)
            files = self.files('mrs')
            config = json.loads(files['config.yaml'])
            config['rule-providers']['sample'].update(format='mrs', path='./ruleset/sample.mrs')
            files['config.yaml'] = json.dumps(config).encode()
            del files['ruleset/sample.list']
            files['ruleset/sample.mrs'] = mrs.read_bytes()
            mrs_id, _, _ = self.upload(files)
            self.wait(mrs_id, {'succeeded'})
            assert (self.live / 'ruleset/sample.mrs').read_bytes() == mrs.read_bytes()
            assert not (self.live / 'ruleset/sample.list').exists()
            committed_files = files
            results.append('real_mrs_loaded_byte_exact_and_stale_managed_file_removed')
        def snapshot():
            values = {}
            for path in committed_files:
                file = self.live / path
                metadata = file.stat()
                values[path] = (file.read_bytes(), metadata.st_mode & 0o777, metadata.st_uid, metadata.st_gid)
            return values
        committed_snapshot = snapshot()
        # Agent normalizes config paths/YAML while retaining resource bytes.
        assert {name: value[0] for name, value in committed_snapshot.items() if name != 'config.yaml'} == {
            name: data for name, data in committed_files.items() if name != 'config.yaml'}
        good = (self.live / 'config.yaml').read_bytes()
        bad, _, _ = self.upload(self.files('invalid', invalid=True))
        self.wait(bad, {'failed'})
        assert (self.live / 'config.yaml').read_bytes() == good
        assert snapshot() == committed_snapshot
        results.append('preflight_rejects_bad_configuration')
        damaged_id, damaged = bundle(self, self.files('damaged'))
        assert request(self.url + '/api/deployments', 'POST', damaged[:-9], {
            'X-Deployment-ID': damaged_id, 'X-Content-SHA256': hashlib.sha256(damaged).hexdigest()})[0] == 400
        assert (self.live / 'config.yaml').read_bytes() == good
        assert snapshot() == committed_snapshot
        results.append('damaged_upload_keeps_current_version')
        files = self.files('fail', fail_start=True)
        files['rules/new-only.txt'] = b'new-file-must-be-removed-on-rollback\n'
        bad, _, _ = self.upload(files)
        self.wait(bad, {'rolled_back'})
        assert (self.live / 'config.yaml').read_bytes() == good
        assert not (self.live / 'rules/new-only.txt').exists()
        assert snapshot() == committed_snapshot
        self.probe()
        results.append('real_start_failure_restores_complete_previous_version')
        # Kill the actual worker/container after replacement, before durable success.
        files = self.files('interrupted')
        files['rules/interrupted-only.txt'] = b'not-committed\n'
        interrupted, _, _ = self.upload(files)
        self.wait(interrupted, {'checking'})
        if self.mode == 'docker':
            run(self.docker, 'kill', self.name)
            run(self.docker, 'start', self.name)
        else:
            self.agent_process.kill()
            self.agent_process.wait(timeout=5)
            self.start_native_agent()
        self.wait_ready()
        self.wait(interrupted, {'rolled_back'})
        assert (self.live / 'config.yaml').read_bytes() == good
        assert not (self.live / 'rules/interrupted-only.txt').exists()
        assert snapshot() == committed_snapshot
        self.probe()
        results.append('actual_process_or_container_crash_recovers_previous_version')
        return results

    def backend_tests(self):
        (self.path / 'backend').mkdir(exist_ok=True)
        out = run(self.docker, 'run', '--rm', '--network', 'host',
            '-v', str(self.root / 'src') + ':/src:ro',
            '-v', str(self.path / 'backend') + ':/acceptance',
            '-e', 'PYTHONPATH=/src', '-w', '/src', '--entrypoint', 'python3',
            'thsrite/config-flow:latest',
            '/src/backend/agents/go-agent/integration/backend_acceptance.py',
            '--agent-url', self.url, '--agent-id', self.agent_id, '--agent-token', TOKEN,
            '--service', self.kind, '--core-port', self.core_port, '--mixed-port', self.base + 89,
            '--root', '/acceptance')
        (self.path / 'backend.log').write_text(out)
        reports = list((self.path / 'backend').glob('*/report.json'))
        assert reports, 'Backend acceptance did not produce a report'
        report = json.loads(reports[-1].read_text())
        assert report['success'], report
        return report['tests']

    def cleanup(self):
        if self.mode == 'docker':
            (self.path / 'container.log').write_text(run(self.docker, 'logs', self.name, check=False))
            run(self.docker, 'rm', '-f', self.name, check=False)
        else:
            if self.agent_process and self.agent_process.poll() is None:
                self.agent_process.terminate()
                try:
                    self.agent_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.agent_process.kill()
            run('systemctl', 'stop', self.unit, check=False)
            (self.path / 'core.log').write_text(run('journalctl', '-u', self.unit, '--no-pager', '-n', '250', check=False))
            Path('/run/systemd/system', self.unit).unlink(missing_ok=True)
            run('systemctl', 'daemon-reload', check=False)
            run('systemctl', 'reset-failed', self.unit, check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--docker', default='/usr/local/bin/docker')
    parser.add_argument('--modes', nargs='+', default=['native', 'docker'])
    parser.add_argument('--kinds', nargs='+', default=['mihomo', 'mosdns'])
    parser.add_argument('--backend', action='store_true', help='Also exercise actual Flask HTTP publication endpoints')
    args = parser.parse_args()
    assert os.geteuid() == 0, 'Run inside an isolated acceptance host as root.'
    assert args.root.name.startswith('configflow-deployment-acceptance-'), 'Use a dedicated acceptance directory.'
    run_id = time.strftime('%Y%m%dT%H%M%S', time.gmtime())
    report = {'run_id': run_id, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'agent_sha256': hashlib.sha256((args.root / 'bin/configflow-agent').read_bytes()).hexdigest(),
              'targets': []}
    try:
        for mode in args.modes:
            for kind in args.kinds:
                target = Target(args.root, args.docker, mode, kind, run_id)
                item = {'mode': mode, 'service': kind, 'passed': False}
                report['targets'].append(item)
                try:
                    target.start()
                    item['checks'] = target.tests()
                    if args.backend:
                        item['backend_checks'] = target.backend_tests()
                    item['passed'] = True
                    print(json.dumps(item), flush=True)
                except Exception as error:
                    item['error'] = repr(error)
                    raise
                finally:
                    item['events'] = target.events
                    target.cleanup()
    finally:
        report['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        (args.root / 'report.json').write_text(json.dumps(report, indent=2))
        (args.root / ('report-' + run_id + '.json')).write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
