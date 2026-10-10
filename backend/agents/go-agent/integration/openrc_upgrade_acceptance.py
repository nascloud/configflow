#!/usr/bin/env python3
"""Run inside a disposable Alpine/OpenRC container sharing the fixture backend network.

This tests native Shell/OpenRC lifecycle. It does not test image upgrades.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import time
import uuid

from acceptance import Target, dns_answer
from online_upgrade_acceptance import request, run, wait_http

root = Path('/acceptance')
base = 'http://127.0.0.1:23601'
binary = Path('/usr/local/bin/configflow-agent')
identity = Path('/opt/configflow-agent')
updates = Path('/usr/local/bin/.configflow-updates')
report = {'success': False, 'manager': 'openrc', 'legacy_version': '1.0.8-go', 'cases': []}
Path('/run/openrc').mkdir(exist_ok=True)
Path('/run/openrc/softlevel').write_text('default\n')
# Docker supplies the container's local mounts; mark that OpenRC dependency ready.
run('rc-service', 'localmount', 'zap', check=False)
run('rc-service', 'localmount', 'start')
wait_http(base + '/test/ready')


def service(name, executable, args):
    path = Path('/etc/init.d', name)
    path.write_text('#!/sbin/openrc-run\ncommand="' + executable + '"\ncommand_args="' + args + '"\ncommand_background=yes\npidfile=/run/' + name + '.pid\noutput_log=/acceptance/' + name + '.log\nerror_log=/acceptance/' + name + '.log\ndepend() {\n need localmount\n}\n')
    path.chmod(0o755)


try:
    for kind in ('mihomo', 'mosdns'):
        target = Target(root, '', 'native', kind, uuid.uuid4().hex)
        target.base = 23600
        target.core_port = 23690 if kind == 'mihomo' else 23653
        target.write_initial()
        core = 'upgrade-test-' + kind
        args = '-d ' + str(target.live) if kind == 'mihomo' else 'start -c ' + str(target.live / 'config.yaml') + ' -d ' + str(target.live)
        service(core, '/usr/local/bin/' + kind, args)
        run('rc-service', core, 'start')
        identity.mkdir(exist_ok=True)
        config_path = identity / 'config.json'
        original = dict(server_url=base, agent_name='upgrade-test-' + kind, agent_host='127.0.0.1',
            agent_port=23680, service_type=kind, service_name=core, deployment_method='shell', config_path=str(target.live / 'config.yaml'),
            restart_command='rc-service ' + core + ' restart', heartbeat_interval=2,
            agent_id='upgrade-test-' + kind, token='upgrade-acceptance-token', enable_metrics=False,
            future_extension={'keep': ['untouched', 42]})
        config_path.write_text(json.dumps(original))
        config_path.chmod(0o600)
        binary.unlink(missing_ok=True)
        shutil.copy2(root / 'legacy108-agent', binary)
        binary.chmod(0o755)
        service('configflow-agent', str(binary), '-config ' + str(config_path))
        request(base + '/test/reset', {'service': kind})
        run('rc-service', 'configflow-agent', 'start')
        wait_http('http://127.0.0.1:23680/health')

        def update(label, expected, fault='none', interrupt=False):
            before = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in target.live.rglob('*') if p.is_file() and '.configflow-deployments' not in p.parts}
            request(base + '/test/fault', {'fault': fault})
            code, result = request(base + '/api/agents/upgrade-test-' + kind + '/update', {})
            assert code == 202, result
            ident = result['update_id']
            killed = False
            stages = []
            for _ in range(500):
                _, result = request(base + '/api/agents/upgrade-test-' + kind + '/upgrade')
                if not stages or stages[-1] != result['status']:
                    stages.append(result['status'])
                run('rc-service', core, 'status')
                if interrupt and not killed and (updates / 'current.json').exists():
                    job = json.loads((updates / 'current.json').read_text())
                    if job.get('update_id') == ident and job.get('status') == 'checking':
                        # Kill the real worker child, leaving OpenRC's supervisor alive.
                        for proc in Path('/proc').iterdir():
                            if not proc.name.isdigit():
                                continue
                            try:
                                cmd = (proc / 'cmdline').read_bytes().split(b'\0')
                                if cmd and cmd[0].decode() == job['worker']:
                                    os.kill(int(proc.name), signal.SIGKILL)
                                    killed = True
                            except (OSError, UnicodeError):
                                pass
                if result['status'] in ('succeeded', 'failed', 'rolled_back', 'rollback_failed'):
                    break
                time.sleep(.2)
            assert result['status'] == expected, (label, result, stages)
            if interrupt:
                assert killed and 'interrupted Agent update' in result.get('error', ''), result
            assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == digest for p,digest in before.items())
            current = json.loads(config_path.read_text())
            for key in ('agent_id','token','server_url','agent_port','config_path','future_extension'):
                assert current[key] == original[key], key
            report['cases'].append({'service': kind, 'case': label, 'status': expected, 'stages': stages})
            print('PASS', kind, label, expected, flush=True)
            request(base + '/test/fault', {'fault': 'none'})

        update('legacy108_one_click_success', 'succeeded')
        update('new_protocol_success', 'succeeded')
        update('download_http503', 'failed', 'http503')
        update('download_integrity', 'failed', 'corrupt')
        update('start_failure', 'rolled_back', 'bad_start')
        update('worker_interruption', 'rolled_back', 'bad_start', True)
        deployment_id = uuid.uuid4().hex
        code, result = request(base + '/api/agents/upgrade-test-' + kind + '/push-config', {'deployment_id':deployment_id,'restart':True})
        assert code == 202, result
        for _ in range(200):
            _, result = request(base + '/api/agents/upgrade-test-' + kind + '/deployments/' + deployment_id)
            if result['status'] == 'succeeded':
                break
            assert result['status'] not in ('failed', 'rolled_back', 'rollback_failed'), result
            time.sleep(.25)
        assert result['status'] == 'succeeded', result
        if kind == 'mihomo':
            wait_http('http://127.0.0.1:23690/version')
        else:
            assert dns_answer(23653) == '192.0.2.20'
        report['cases'].append({'service':kind,'case':'publish_after_upgrade','status':'succeeded'})
        run('rc-service', 'configflow-agent', 'stop')
        run('rc-service', core, 'stop')
        run('rc-service', 'configflow-recover-' + kind, 'stop')
        run('rc-service', core, 'start')
        run('rc-service', 'configflow-recover-' + kind, 'status')
        report['cases'].append({'service':kind,'case':'recovery_gate_before_core','status':'succeeded'})
        run('rc-service', core, 'stop')
        shutil.rmtree(updates)
    report['success'] = True
except Exception as exc:
    report['error'] = repr(exc)
    raise
finally:
    (root / 'openrc-report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'success':report['success'],'cases':len(report['cases'])}), flush=True)
