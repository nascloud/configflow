#!/usr/bin/env python3
"""Generate/run a reproducible loopback smoke test against a real OxiDNS binary.

From the repository: python scripts/verify_oxidns_native.py --prepare /tmp/oxi-test
On a host with PyYAML and OxiDNS: python verify_oxidns_native.py --run /tmp/oxi-test --binary /opt/oxidns/oxidns
Only generated fixtures are used. No production config, services or RouterOS calls.
"""
import argparse
import collections
import json
import os
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import yaml


def prepare(root):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.common.oxidns_settings import get_default_oxidns_settings
    from backend.converters.oxidns import generate_oxidns_config, get_oxidns_custom_files
    settings = get_default_oxidns_settings()
    settings.update(prefer_ipv4=False, cache_lazy_ttl=8)
    settings['api'].update(enabled=True, username='smoke', password='test-only')
    settings['query_recorder']['enabled'] = True
    settings['dynamic_domain_set']['enabled'] = True
    settings['dynamic_domain_set']['args']['bootstrap_rules'] = ['full:dynamic.test']
    settings['adguard'].update(enabled=True, content='||ads.test^')
    rules = []
    for identity, kind, value, route in (
        ('direct', 'DOMAIN', 'direct.test', 'direct'),
        ('proxy', 'DOMAIN', 'proxy.test', 'proxy'),
        ('overlap-first', 'DOMAIN', 'overlap.test', 'proxy'),
        ('overlap-last', 'DOMAIN', 'overlap.test', 'direct'),
        ('direct-ip', 'IP-CIDR', '10.0.0.0/8', 'direct'),
        ('proxy-ip', 'IP-CIDR', '203.0.113.0/24', 'proxy'),
    ):
        rules.append({'id': identity, 'itemType': 'rule', 'rule_type': kind, 'value': value})
        settings[route + '_rules'].append(identity)
    config = {'oxidns': settings, 'rule_configs': rules}
    root.mkdir(parents=True, exist_ok=True)
    (root / 'config.yaml').write_text(generate_oxidns_config(config))
    for item in get_oxidns_custom_files(config):
        destination = root / item['path']
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(item['content'])
    print(json.dumps({'prepared': str(root)}))


def read_name(data, offset):
    labels = []
    while data[offset]:
        size = data[offset]
        labels.append(data[offset + 1:offset + 1 + size].decode())
        offset += size + 1
    return '.'.join(labels), offset + 1


class MockDNS(socketserver.ThreadingUDPServer):
    daemon_threads = True

    def __init__(self, default_ip):
        self.default_ip = default_ip
        self.answers = {}
        self.counts = collections.Counter()
        self.lock = threading.Lock()
        super().__init__(('127.0.0.1', 0), MockHandler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def set(self, name, ip, ttl=2):
        with self.lock:
            self.answers[name] = (ip, ttl)

    def count(self, name):
        with self.lock:
            return self.counts[name]

    def close(self):
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=2)


class MockHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data, sock = self.request
        name, end = read_name(data, 12)
        qtype, qclass = struct.unpack('!HH', data[end:end + 4])
        with self.server.lock:
            self.server.counts[name] += 1
            ip, ttl = self.server.answers.get(name, (self.server.default_ip, 60))
        answer = b''
        if qtype == 1:
            answer = b'\xc0\x0c' + struct.pack('!HHIH', 1, 1, ttl, 4) + socket.inet_aton(ip)
        response = data[:2] + struct.pack('!HHHHH', 0x8180, 1, int(bool(answer)), 0, 0) + data[12:end + 4] + answer
        sock.sendto(response, self.client_address)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def query(port, name, tcp=False, qtype=1):
    wire_name = b''.join(bytes([len(label)]) + label.encode() for label in name.split('.')) + b'\0'
    request = struct.pack('!HHHHHH', 0x2345, 0x0100, 1, 0, 0, 0) + wire_name + struct.pack('!HH', qtype, 1)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as sock:
        sock.settimeout(3)
        if tcp:
            sock.connect(('127.0.0.1', port))
            sock.sendall(struct.pack('!H', len(request)) + request)
            size = struct.unpack('!H', sock.recv(2))[0]
            reply = b''
            while len(reply) < size:
                block = sock.recv(size - len(reply))
                if not block:
                    raise AssertionError('truncated TCP response')
                reply += block
        else:
            sock.sendto(request, ('127.0.0.1', port))
            reply = sock.recv(4096)
    header = struct.unpack('!HHHHHH', reply[:12])
    assert header[0] == 0x2345
    _, offset = read_name(reply, 12)
    offset += 4
    ips, ttls = [], []
    for _ in range(header[3]):
        if reply[offset] & 0xc0 == 0xc0:
            offset += 2
        else:
            _, offset = read_name(reply, offset)
        typ, cls, ttl, size = struct.unpack('!HHIH', reply[offset:offset + 10])
        offset += 10
        if typ == 1:
            ips.append(socket.inet_ntoa(reply[offset:offset + size]))
            ttls.append(ttl)
        offset += size
    return {'rcode': header[1] & 15, 'ips': ips, 'ttls': ttls}


def run(root, binary):
    import base64
    config = yaml.safe_load((root / 'config.yaml').read_text())
    dns_port, api_port = free_port(), free_port()
    assert dns_port not in (53, 9199) and api_port not in (53, 9199)
    local, remote = MockDNS('10.1.2.3'), MockDNS('198.18.0.1')
    local.set('probe.test', '10.1.2.3')
    local.set('direct.test', '10.2.3.4')
    config['api']['http']['listen'] = f'127.0.0.1:{api_port}'
    for plugin in config['plugins']:
        if plugin['type'] == 'forward':
            mock = remote if plugin['tag'] == 'forward_remote' else local
            plugin['args']['upstreams'] = [{'addr': f'udp://127.0.0.1:{mock.server_address[1]}'}]
        elif plugin['type'] in ('udp_server', 'tcp_server'):
            plugin['args']['listen'] = f'127.0.0.1:{dns_port}'
    (root / 'state').mkdir(exist_ok=True)
    runtime = root / 'runtime.yaml'
    runtime.write_text(yaml.safe_dump(config, sort_keys=False))
    process = None
    results = {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    api_url = f'http://127.0.0.1:{api_port}/api/readyz'
    try:
        subprocess.run([binary, 'check', '-c', str(runtime), '-d', str(root)], check=True, capture_output=True, timeout=30)
        with (root / 'runtime.log').open('w') as log:
            process = subprocess.Popen([binary, 'start', '-c', str(runtime), '-d', str(root)], stdout=log, stderr=subprocess.STDOUT)
            auth = 'Basic ' + base64.b64encode(b'smoke:test-only').decode()
            for _ in range(100):
                assert process.poll() is None, (root / 'runtime.log').read_text()[-3000:]
                try:
                    with opener.open(urllib.request.Request(api_url, headers={'Authorization': auth}), timeout=.3) as response:
                        assert response.status == 200
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(.05)
            else:
                raise AssertionError('temporary OxiDNS API did not become ready')
            try:
                opener.open(api_url, timeout=1)
                raise AssertionError('Basic authentication was not enforced')
            except urllib.error.HTTPError as error:
                assert error.code == 401
            results['api_basic_auth'] = True
            for tcp in (False, True):
                for _ in range(25):
                    for name, ip in (('direct.test', '10.2.3.4'), ('proxy.test', '198.18.0.1'),
                                     ('overlap.test', '198.18.0.1'), ('dynamic.test', '10.1.2.3')):
                        reply = query(dns_port, name, tcp)
                        assert reply['ips'] == [ip], (name, tcp, reply)
            results['udp_tcp_routing_queries'] = 200
            assert local.count('direct.test') == 1
            results['direct_cache_hit'] = True
            for _ in range(20):
                assert query(dns_port, 'probe.test')['ips'] == ['10.1.2.3']
            assert local.count('probe.test') == 1
            local.set('probe.test', '203.0.113.10')
            local.set('direct.test', '10.2.3.5')
            time.sleep(2.2)
            assert query(dns_port, 'probe.test')['ips'] == ['198.18.0.1']
            assert local.count('probe.test') == 2
            for _ in range(10):
                assert query(dns_port, 'probe.test')['ips'] == ['198.18.0.1']
            assert local.count('probe.test') == 2
            results['probe_cache_expiry_reclassifies_without_fakeip_pollution'] = True
            assert query(dns_port, 'direct.test')['ips'] == ['10.2.3.4']
            for _ in range(50):
                time.sleep(.05)
                if query(dns_port, 'direct.test')['ips'] == ['10.2.3.5']:
                    break
            else:
                raise AssertionError('lazy refresh did not install fresh response')
            results['lazy_stale_and_background_refresh'] = True
            assert query(dns_port, 'ads.test')['rcode'] == 3
            assert local.count('ads.test') == remote.count('ads.test') == 0
            results['adguard_blocks_before_upstream'] = True
            assert query(dns_port, 'type.test', qtype=65)['ips'] == []
            results['blocked_qtype'] = True
            time.sleep(.3)
            assert (root / 'state/queries.db').stat().st_size > 0
            results['query_recorder_database'] = True
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        local.close()
        remote.close()
    (root / 'result.json').write_text(json.dumps(results, indent=2) + '\n')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--prepare', type=Path)
    modes.add_argument('--run', type=Path)
    parser.add_argument('--binary', default='oxidns')
    args = parser.parse_args()
    if args.prepare:
        prepare(args.prepare.resolve())
    else:
        run(args.run.resolve(), args.binary)
