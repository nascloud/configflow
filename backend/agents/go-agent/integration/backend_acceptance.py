#!/usr/bin/env python3
"""Exercise real Flask publication endpoints against an isolated running Agent.

Run inside the normal ConfigFlow image with source mounted at /src and host
networking. No production repository, Agent registration, converters, transports
or service actions are mocked. The caller owns the target Agent/core lifetime.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import urllib.request
import uuid

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
TERMINAL = {'succeeded', 'failed', 'rolled_back', 'rollback_failed'}


def dns_answer(port):
    ident = os.urandom(2)
    packet = ident + b'\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'
    packet += b'\x0aacceptance\x04test\x00\x00\x01\x00\x01'
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        sock.sendto(packet, ('127.0.0.1', port))
        answer = sock.recv(4096)
    assert answer[:2] == ident and answer[3] & 15 == 0 and struct.unpack('!H', answer[6:8])[0] > 0
    return socket.inet_ntoa(answer[-4:])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--agent-url', required=True)
    parser.add_argument('--agent-id', required=True)
    parser.add_argument('--agent-token', required=True)
    parser.add_argument('--service', choices=['mihomo', 'mosdns'], required=True)
    parser.add_argument('--core-port', type=int, required=True)
    parser.add_argument('--mixed-port', type=int, default=23089)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    run_root = args.root / (args.service + '-' + uuid.uuid4().hex[:8])
    run_root.mkdir()
    # Import configuration only after selecting the isolated repository location.
    os.environ['DATA_DIR'] = str(run_root)
    os.environ['ADMIN_USERNAME'] = ''
    os.environ['ADMIN_PASSWORD'] = ''
    os.environ['NO_PROXY'] = '127.0.0.1,localhost'
    os.environ['no_proxy'] = '127.0.0.1,localhost'
    import requests
    from flask import Flask
    from werkzeug.serving import make_server
    from backend.common.config_repository import ProfileRepository
    from backend.common import config as config_module
    from backend.common.agent_manager import init_agent_manager
    from backend.routes import register_blueprints

    state = {'fail': False, 'origin_requests': 0, 'proxy_requests': 0}
    class Origin(BaseHTTPRequestHandler):
        def do_GET(self):
            state['origin_requests'] += 1
            body = b'fixture unavailable' if state['fail'] else b'+.backend-acceptance.test\n'
            self.send_response(503 if state['fail'] else 200)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass
    origin = ThreadingHTTPServer(('127.0.0.1', 0), Origin)
    class Proxy(BaseHTTPRequestHandler):
        def do_GET(self):
            state['proxy_requests'] += 1
            target = urlsplit(self.path)
            assert target.hostname == 'rules.acceptance.invalid', self.path
            req = urllib.request.Request(f'http://127.0.0.1:{origin.server_port}{target.path}')
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            try:
                reply = opener.open(req, timeout=5)
            except urllib.error.HTTPError as exc:
                reply = exc
            with reply:
                body = reply.read()
                self.send_response(reply.code)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(body)
        def log_message(self, *_args):
            pass
    proxy = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
    for server in (origin, proxy):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    repository = ProfileRepository(run_root / 'data')
    config_module.set_repository(repository)
    manager = init_agent_manager()
    agent_target = urlsplit(args.agent_url)
    repository.update_system_transaction(lambda system: system.update(agents=[{
        'id': args.agent_id, 'name': args.agent_id, 'host': agent_target.hostname,
        'port': agent_target.port, 'token': args.agent_token, 'service_type': args.service,
        'profile_id': 'default', 'enabled': True, 'config_version': 'before-backend-test'}]))
    repository.update_system_transaction(lambda system: system['system_config'].update({
        'rule_fetch_proxy': f'http://127.0.0.1:{proxy.server_port}'}))
    repository.update_shared_transaction(lambda shared: shared['rule_library'].append({
        'id': 'acceptance-library-rule', 'name': 'backend_acceptance', 'enabled': True,
        'source_type': 'url', 'url': 'http://rules.acceptance.invalid/managed.list', 'behavior': 'domain'}))
    profile = repository.get_profile('default')
    profile['rule_configs'] = [{
        'id': 'acceptance-profile-rule', 'library_rule_id': 'acceptance-library-rule',
        'itemType': 'ruleset', 'enabled': True, 'policy': 'DIRECT'}]
    profile['proxy_groups'] = []
    profile['mihomo']['custom_config'] = json.dumps({
        'mixed-port': args.mixed_port, 'external-controller': f'127.0.0.1:{args.core_port}',
        'mode': 'rule', 'log-level': 'warning', 'ipv6': False,
        'dns': {'enable': False}, 'rules': ['MATCH,DIRECT']})
    profile['mosdns'].update({
        'listen_address': f'127.0.0.1:{args.core_port}', 'api_enabled': False, 'log_enabled': False,
        'direct_rulesets': ['acceptance-profile-rule'], 'proxy_rulesets': [], 'direct_rules': [], 'proxy_rules': [],
        'custom_hosts': 'acceptance.test 192.0.2.20',
        'local_dns': 'udp://127.0.0.1:9', 'remote_dns': 'udp://127.0.0.1:9', 'fallback_dns': 'udp://127.0.0.1:9',
        'cache_enabled': False, 'cache_dump_enabled': False,
    })
    repository.save_profile('default', profile)
    app = Flask('configflow-real-backend-acceptance')
    register_blueprints(app)
    backend = make_server('127.0.0.1', 0, app, threaded=True)
    backend_url = f'http://127.0.0.1:{backend.server_port}'
    repository.update_system_transaction(lambda system: system['system_config'].update({'server_domain': backend_url}))
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    session = requests.Session()
    session.trust_env = False
    report = {'service': args.service, 'agent_id': args.agent_id, 'tests': [], 'deployments': []}

    def api(path, method='get', **kwargs):
        result = getattr(session, method)(backend_url + '/api/agents/' + args.agent_id + path, timeout=90, **kwargs)
        return result.status_code, result.json()

    def poll(deployment_id, expected):
        stages = []
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            code, result = api('/deployments/' + deployment_id)
            assert code == 200, (code, result)
            status = result['status']
            if not stages or stages[-1] != status:
                stages.append(status)
            if status in expected:
                report['deployments'].append({'deployment_id': deployment_id, 'stages': stages, 'status': status})
                return result
            assert status not in TERMINAL, result
            time.sleep(.1)
        raise AssertionError(('backend deployment timed out', stages))

    try:
        staged_id = uuid.uuid4().hex
        code, result = api('/push-config', 'post', json={'deployment_id': staged_id, 'restart': False})
        assert code == 202, (code, result)
        poll(staged_id, {'ready'})
        assert manager.get_agent_by_id(args.agent_id)['config_version'] == 'before-backend-test'
        assert state['proxy_requests'] > 0 and state['origin_requests'] > 0
        report['tests'].append('real generated config and proxied rule packaged; staged without version change')
        code, result = api('/deployments/' + staged_id + '/activate', 'post')
        assert code in (200, 202), (code, result)
        completed = poll(staged_id, {'succeeded'})
        version = manager.get_agent_by_id(args.agent_id)['config_version']
        assert version == completed['config_version'] and version != 'before-backend-test'
        if args.service == 'mihomo':
            core = session.get(f'http://127.0.0.1:{args.core_port}/version', timeout=5)
            assert core.status_code == 200 and core.json()['version']
        else:
            assert dns_answer(args.core_port) == '192.0.2.20'
        report['tests'].append('activated through real HTTP; core health verified; confirmed version persisted')
        manager = init_agent_manager()
        code, reloaded = api('/deployments/' + staged_id)
        assert code == 200 and reloaded['status'] == 'succeeded'
        assert manager.get_agent_by_id(args.agent_id)['config_version'] == version
        report['tests'].append('backend manager reload retains deployment ownership and confirmed version')
        state['fail'] = True
        failed_id = uuid.uuid4().hex
        code, failed = api('/push-config', 'post', json={'deployment_id': failed_id, 'restart': True})
        assert code == 400 and failed['status'] == 'failed', (code, failed)
        assert manager.get_agent_by_id(args.agent_id)['config_version'] == version
        absent = session.get(args.agent_url + '/api/deployments/' + failed_id,
                             headers={'Authorization': 'Bearer ' + args.agent_token}, timeout=5)
        assert absent.status_code == 404, absent.text
        if args.service == 'mihomo':
            assert session.get(f'http://127.0.0.1:{args.core_port}/version', timeout=5).status_code == 200
        else:
            assert dns_answer(args.core_port) == '192.0.2.20'
        report['tests'].append('HTTP 503 required rule aborts before Agent receives deployment; running service/version preserved')
        report['proxy_requests'] = state['proxy_requests']
        report['origin_requests'] = state['origin_requests']
        report['success'] = True
    except Exception as exc:
        report['success'] = False
        report['error'] = repr(exc)
        raise
    finally:
        (run_root / 'report.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2), flush=True)
        for server in (backend, proxy, origin):
            server.shutdown()
            server.server_close()
        session.close()


if __name__ == '__main__':
    main()
