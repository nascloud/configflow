#!/usr/bin/env python3
"""Isolated real ConfigFlow backend with controlled binary-download faults."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, default=23601)
    args = parser.parse_args()
    os.environ.update(DATA_DIR=str(args.root / 'repository'), ADMIN_USERNAME='', ADMIN_PASSWORD='',
                      AGENTS_STATIC_DIR=str(args.root / 'artifacts'))
    from flask import Flask, request, jsonify
    from werkzeug.serving import make_server
    from backend.routes import register_blueprints
    from backend.common.config_repository import ProfileRepository
    from backend.common import config as config_module
    from backend.common.agent_manager import init_agent_manager
    repository = ProfileRepository(args.root / 'repository')
    config_module.set_repository(repository)
    manager = init_agent_manager()
    app = Flask('isolated-upgrade-acceptance')
    register_blueprints(app)
    state = {'fault': 'none'}
    binary = args.root / 'artifacts/configflow-agent-linux-amd64'
    original = args.root / 'new-agent'
    shutil.copy2(original, binary)

    @app.before_request
    def fault():
        if request.path.startswith('/api/agents/download/'):
            if state['fault'] == 'http503':
                return 'temporary download failure', 503
            if state['fault'] == 'corrupt':
                return b'\x7fELFcorrupt transfer', 200

    @app.post('/test/reset')
    def reset():
        value = request.get_json()
        kind = value['service']
        core_port = 23690 if kind == 'mihomo' else 23653
        agent = {'id': 'upgrade-test-' + kind, 'name': 'upgrade-test-' + kind,
                 'host': '127.0.0.1', 'port': 23680, 'token': 'upgrade-acceptance-token',
                 'service_type': kind, 'deployment_method': 'shell', 'profile_id': 'default',
                 'enabled': True, 'version': '1.1.0-go', 'config_version': 'initial'}
        repository.update_system_transaction(lambda system: system.update(agents=[agent]))
        repository.update_system_transaction(lambda system: system['system_config'].update(
            server_domain='http://127.0.0.1:' + str(args.port), rule_fetch_proxy=''))
        repository.update_shared_transaction(lambda shared: shared.update(rule_library=[{
            'id': 'upgrade-rule', 'name': 'upgrade_rule', 'source_type': 'url', 'behavior': 'domain',
            'url': f'http://127.0.0.1:{args.port}/test/rules', 'enabled': True}]))
        profile = repository.get_profile('default')
        profile['rule_configs'] = [{'id': 'upgrade-profile-rule', 'library_rule_id': 'upgrade-rule',
                                   'itemType': 'ruleset', 'enabled': True, 'policy': 'DIRECT'}]
        profile['proxy_groups'] = []
        profile['mihomo']['custom_config'] = json.dumps({'mixed-port': 23689,
            'external-controller': f'127.0.0.1:{core_port}', 'mode': 'rule', 'log-level': 'warning',
            'ipv6': False, 'dns': {'enable': False}, 'rules': ['MATCH,DIRECT']})
        profile['mosdns'].update({'listen_address': f'127.0.0.1:{core_port}', 'api_enabled': False,
            'log_enabled': False, 'direct_rulesets': ['upgrade-profile-rule'], 'proxy_rulesets': [],
            'direct_rules': [], 'proxy_rules': [], 'custom_hosts': 'example.com 192.0.2.20\nacceptance.test 192.0.2.20',
            'local_dns': 'udp://127.0.0.1:9', 'remote_dns': 'udp://127.0.0.1:9', 'fallback_dns': 'udp://127.0.0.1:9',
            'cache_enabled': False, 'cache_dump_enabled': False})
        repository.save_profile('default', profile)
        state['fault'] = 'none'
        shutil.copy2(original, binary)
        return jsonify(agent_id=agent['id'])

    @app.post('/test/fault')
    def set_fault():
        state['fault'] = request.get_json()['fault']
        shutil.copy2(args.root / 'bad-agent' if state['fault'] == 'bad_start' else original, binary)
        return jsonify(state)

    @app.get('/test/rules')
    def rules():
        return '+.upgrade-acceptance.test\n', 200, {'Content-Type': 'text/plain'}

    @app.get('/test/ready')
    def ready():
        return jsonify(ready=True)

    make_server('127.0.0.1', args.port, app, threaded=True).serve_forever()


if __name__ == '__main__':
    main()
