"""Read-only OxiDNS monitoring through an authenticated, registered Agent."""
from datetime import datetime, timezone
import ipaddress
import json
import math
import re

import requests

from .version import compare_versions
from .oxidns_metrics import summarize_metrics

MIN_AGENT_VERSION = '1.7.0-go'
MIN_CLIENTS_AGENT_VERSION = '1.7.1-go'
CLIENT_SAMPLE_LIMIT = 10000
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_INTEGER = 2 ** 53 - 1

# OxiDNS v1.6.1 query_recorder stores Rcode's Display text and matches it
# case-insensitively, without invoking Rcode::from_token for RFC mnemonics.
# Source: svenshi/oxidns v1.6.1 crates/proto/src/types.rs, Rcode::to_str.
RCODE_FILTER_TEXT = {
    'NOERROR': 'No Error',
    'FORMERR': 'Form Error',
    'SERVFAIL': 'Server Failure',
    'NXDOMAIN': 'Non-Existent Domain',
    'NOTIMP': 'Not Implemented',
    'REFUSED': 'Query Refused',
    'YXDOMAIN': 'Name should not exist',
    'YXRRSET': 'RR Set should not exist',
    'NXRRSET': 'RR Set does not exist',
    'NOTAUTH': 'Not authorized',
    'NOTZONE': 'Name not in zone',
    'BADVERS': 'Bad OPT Version',
    'BADSIG': 'TSIG Signature Failure',
    'BADKEY': 'Key not recognized',
    'BADTIME': 'Signature out of time window',
    'BADMODE': 'Bad TKEY mode',
    'BADNAME': 'Duplicate key name',
    'BADALG': 'Algorithm not supported',
    'BADTRUNC': 'Bad truncation',
    'BADCOOKIE': 'Bad server cookie',
}

MESSAGES = {
    'UPGRADE_REQUIRED': '请将此 Agent 升级到 1.7.0-go 或更新版本后使用 DNS 监控。',
    'API_NOT_CONFIGURED': 'OxiDNS 管理 API 未启用，请在 OxiDNS 配置中启用后推送。',
    'RECORDER_NOT_CONFIGURED': '查询记录未启用，请在 OxiDNS 设置中开启查询记录并推送配置。',
    'TLS_UNSUPPORTED': '此监控连接暂不支持当前 OxiDNS API 的 TLS 配置。',
    'CORE_UNREACHABLE': '暂时无法连接 OxiDNS 管理 API，请检查核心运行状态。',
    'CORE_AUTH_FAILED': 'OxiDNS 管理 API 认证失败，请检查核心配置中的认证设置。',
    'AGENT_UNREACHABLE': '暂时无法连接 Agent，请检查网络和 Agent 运行状态。',
    'AGENT_AUTH_FAILED': 'Agent 认证失败，请检查注册状态。',
    'INVALID_RESPONSE': '监控数据格式异常，请检查 Agent 和 OxiDNS 版本。',
    'INVALID_QUERY': '查询条件无效，请检查筛选内容。',
    'RECORD_NOT_FOUND': '这条查询记录已不存在，可能已超过保留期限。',
    'CONFIG_UNSUPPORTED': '当前核心 API 配置无法用于监控，请检查监听地址与认证设置。',
}


class MonitorError(Exception):
    def __init__(self, code, status=502, message=None):
        self.code, self.status = code, status
        self.message = message or MESSAGES.get(code, MESSAGES['INVALID_RESPONSE'])
        super().__init__(self.message)


def _upgrade_required(min_version):
    feature = '客户端 IP 选择与筛选' if min_version == MIN_CLIENTS_AGENT_VERSION else 'DNS 监控'
    return MonitorError('UPGRADE_REQUIRED', 409,
                        f'请将此 Agent 升级到 {min_version} 或更新版本后使用{feature}。')


def _agent(manager, agent_id, min_version=MIN_AGENT_VERSION):
    agent = manager.get_agent_by_id(agent_id)
    if not agent:
        raise MonitorError('AGENT_NOT_FOUND', 404, '未找到这台 Agent。')
    if agent.get('service_type') != 'oxidns':
        raise MonitorError('WRONG_SERVICE', 400, 'DNS 监控仅适用于 OxiDNS Agent。')
    try:
        supported = compare_versions(agent.get('version', '0'), min_version) >= 0
    except (ValueError, TypeError, AttributeError):
        supported = False
    if not supported:
        raise _upgrade_required(min_version)
    return agent


def _base_url(agent):
    host = agent.get('host', '')
    if not isinstance(host, str) or not host or any(c in host for c in '/?#@\\'):
        raise MonitorError('AGENT_UNREACHABLE')
    host = host.removeprefix('[').removesuffix(']')
    try:
        address = ipaddress.ip_address(host)
        host = '[' + str(address) + ']' if address.version == 6 else str(address)
    except ValueError:
        if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?', host):
            raise MonitorError('AGENT_UNREACHABLE')
    port = agent.get('port')
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise MonitorError('AGENT_UNREACHABLE')
    return f'http://{host}:{port}/api/oxidns/'


def _request(agent, endpoint, params=None, *, min_version=MIN_AGENT_VERSION):
    # Endpoints are chosen by the functions below, never taken from a URL argument.
    url = _base_url(agent) + endpoint
    try:
        with requests.Session() as session:
            session.trust_env = False
            with session.get(url, params=params or {}, headers={
                'Authorization': 'Bearer ' + agent['token'], 'Accept': 'application/json',
            }, timeout=(3, 12), allow_redirects=False, stream=True) as response:
                if response.status_code in (401, 403):
                    raise MonitorError('AGENT_AUTH_FAILED')
                if 300 <= response.status_code < 400:
                    raise MonitorError('INVALID_RESPONSE')
                chunks, size = [], 0
                for chunk in response.iter_content(64 * 1024):
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise MonitorError('INVALID_RESPONSE')
                    chunks.append(chunk)
                try:
                    data = json.loads(b''.join(chunks))
                except (ValueError, UnicodeError):
                    if response.status_code == 404:
                        raise _upgrade_required(min_version) from None
                    raise MonitorError('INVALID_RESPONSE') from None
                if not isinstance(data, dict):
                    raise MonitorError('INVALID_RESPONSE')
                if response.status_code >= 400 or data.get('success') is False or data.get('ok') is False:
                    code = data.get('code')
                    if code not in MESSAGES:
                        code = 'UPGRADE_REQUIRED' if response.status_code == 404 else 'CORE_UNREACHABLE'
                    status = 404 if code == 'RECORD_NOT_FOUND' else 400 if code == 'INVALID_QUERY' else 409 if code in (
                        'UPGRADE_REQUIRED', 'API_NOT_CONFIGURED', 'RECORDER_NOT_CONFIGURED',
                        'TLS_UNSUPPORTED', 'CONFIG_UNSUPPORTED') else 502
                    if code == 'UPGRADE_REQUIRED':
                        raise _upgrade_required(min_version)
                    raise MonitorError(code, status)
                return data
    except (requests.RequestException, KeyError, TypeError, ValueError):
        # Exceptions and upstream error bodies can contain credentials or query data.
        raise MonitorError('AGENT_UNREACHABLE') from None


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # Bound integers before math.isfinite converts them to float. JSON numbers
    # outside the frontend's exact integer range cannot be represented safely.
    if value < 0 or value > MAX_INTEGER:
        return None
    return value if math.isfinite(value) else None


def _text(value, limit=512):
    return value[:limit] if isinstance(value, str) else ''


def overview(manager, agent_id):
    agent = _agent(manager, agent_id)
    data = _request(agent, 'overview')
    health, capabilities, metrics = data.get('health'), data.get('capabilities'), data.get('metrics')
    if not isinstance(health, dict) or not isinstance(capabilities, dict) or not isinstance(metrics, str):
        raise MonitorError('INVALID_RESPONSE')
    recorders = capabilities.get('query_recorders', [])
    if not isinstance(recorders, list) or any(not isinstance(x, str) for x in recorders):
        raise MonitorError('INVALID_RESPONSE')
    try:
        summary = summarize_metrics(metrics)
    except (ValueError, TypeError):
        raise MonitorError('INVALID_RESPONSE') from None
    return {'success': True, 'agent': {k: agent.get(k) for k in (
        'id', 'name', 'host', 'profile_id', 'version', 'status')},
        'collected_at': datetime.now(timezone.utc).isoformat(),
        'core': {'version': _text(health.get('version'), 64), 'status': _text(health.get('status'), 32),
                 'uptime_ms': _number(health.get('uptime_ms')), 'instance_id': _text(health.get('instance_id'), 128)},
        'recorders': recorders[:64], **summary}


def query_params(args, detail=False):
    allowed = {'recorder'} if detail else {'recorder', 'limit', 'cursor', 'qname', 'qtype', 'client_ip',
                'rcode', 'status', 'since_ms', 'until_ms', 'matcher_tag'}
    result = {}
    for key in args:
        values = args.getlist(key)
        if key not in allowed or len(values) != 1:
            raise MonitorError('INVALID_QUERY', 400)
        value = values[0]
        if not isinstance(value, str) or len(value.encode()) > 1024 or any(ord(c) < 32 for c in value):
            raise MonitorError('INVALID_QUERY', 400)
        value = value.strip()
        if not value:
            continue
        if key in ('limit', 'since_ms', 'until_ms'):
            if not re.fullmatch(r'[0-9]{1,16}', value):
                raise MonitorError('INVALID_QUERY', 400)
            n = int(value)
            if n > MAX_INTEGER or key == 'limit' and not 1 <= n <= 100:
                raise MonitorError('INVALID_QUERY', 400)
            value = str(n)
        elif key == 'cursor':
            if not re.fullmatch(r'[0-9]{1,16}:[1-9][0-9]{0,15}', value) or any(int(x) > MAX_INTEGER for x in value.split(':')):
                raise MonitorError('INVALID_QUERY', 400)
        elif key == 'status' and value not in ('all', 'error', 'has_response', 'no_response'):
            raise MonitorError('INVALID_QUERY', 400)
        elif key in ('recorder', 'matcher_tag') and (len(value) > 256 or any(c in value for c in '/?#\\')):
            raise MonitorError('INVALID_QUERY', 400)
        elif key == 'client_ip':
            try:
                if '%' in value:
                    raise ValueError
                value = str(ipaddress.ip_address(value))
            except ValueError:
                raise MonitorError('INVALID_QUERY', 400) from None
        elif key == 'qtype':
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', value):
                raise MonitorError('INVALID_QUERY', 400)
            value = value.upper()
        elif key == 'rcode':
            if not re.fullmatch(r'[A-Za-z0-9 _-]{1,64}', value):
                raise MonitorError('INVALID_QUERY', 400)
            value = RCODE_FILTER_TEXT.get(value.upper(), value)
        result[key] = value
    if 'since_ms' in result and 'until_ms' in result and int(result['since_ms']) > int(result['until_ms']):
        raise MonitorError('INVALID_QUERY', 400)
    if not detail:
        result.setdefault('limit', '50')
    return result


def _record(value, detail=False):
    if not isinstance(value, dict) or type(value.get('id')) is not int or not 0 < value['id'] <= MAX_INTEGER:
        raise MonitorError('INVALID_RESPONSE')
    fields = ('id', 'created_at_ms', 'elapsed_ms', 'client_ip', 'questions_json', 'error', 'has_response',
              'rcode', 'answer_count', 'authority_count', 'additional_count', 'answers_json',
              'authorities_json', 'additionals_json', 'signature_json', 'request_id', 'req_rd', 'req_cd',
              'req_ad', 'req_opcode', 'resp_aa', 'resp_tc', 'resp_ra', 'resp_ad', 'resp_cd')
    record = {k: value[k] for k in fields if k in value}
    for key in ('created_at_ms', 'elapsed_ms'):
        if _number(record.get(key)) is None:
            raise MonitorError('INVALID_RESPONSE')
    if not isinstance(record.get('questions_json'), list) or not isinstance(record.get('answers_json'), list):
        raise MonitorError('INVALID_RESPONSE')
    if detail:
        if not isinstance(value.get('steps'), list):
            raise MonitorError('INVALID_RESPONSE')
        record['steps'] = value['steps']
    return record


def queries(manager, agent_id, args):
    params = query_params(args)
    min_version = MIN_CLIENTS_AGENT_VERSION if params.get('client_ip') else MIN_AGENT_VERSION
    agent = _agent(manager, agent_id, min_version)
    data = _request(agent, 'queries', params, min_version=min_version)
    records = data.get('records')
    cursor = data.get('next_cursor')
    if data.get('ok') is not True or not isinstance(records, list) or len(records) > 100:
        raise MonitorError('INVALID_RESPONSE')
    if cursor is not None and (not isinstance(cursor, str) or not re.fullmatch(r'[0-9]{1,16}:[1-9][0-9]{0,15}', cursor)):
        raise MonitorError('INVALID_RESPONSE')
    return {'success': True, 'recorder': params.get('recorder', ''),
            'records': [_record(row) for row in records], 'next_cursor': cursor}


def query(manager, agent_id, record_id, args):
    params = query_params(args, detail=True)
    if not re.fullmatch(r'[1-9][0-9]{0,15}', record_id) or int(record_id) > MAX_INTEGER:
        raise MonitorError('INVALID_QUERY', 400)
    agent = _agent(manager, agent_id)
    data = _request(agent, 'queries/' + record_id, params)
    if data.get('ok') is not True:
        raise MonitorError('INVALID_RESPONSE')
    return {'success': True, 'recorder': params.get('recorder', ''), 'record': _record(data.get('record'), detail=True)}


def client_params(args):
    """Client choices are independent of the current record filters."""
    allowed = {'recorder', 'since_ms', 'until_ms', 'search'}
    result = {}
    for key in args:
        values = args.getlist(key)
        if key not in allowed or len(values) != 1 or not isinstance(values[0], str):
            raise MonitorError('INVALID_QUERY', 400)
        value = values[0]
        try:
            encoded = value.encode('utf-8')
        except UnicodeError:
            raise MonitorError('INVALID_QUERY', 400) from None
        if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in value):
            raise MonitorError('INVALID_QUERY', 400)
        if key == 'search':
            if len(encoded) > 64:
                raise MonitorError('INVALID_QUERY', 400)
        elif key == 'recorder':
            if not _valid_recorder_tag(value):
                raise MonitorError('INVALID_QUERY', 400)
        elif not re.fullmatch(r'[0-9]{1,16}', value) or int(value) > MAX_INTEGER:
            raise MonitorError('INVALID_QUERY', 400)
        value = value.strip()
        if value:
            result[key] = value
    if 'since_ms' in result and 'until_ms' in result and int(result['since_ms']) > int(result['until_ms']):
        raise MonitorError('INVALID_QUERY', 400)
    return result


def _valid_recorder_tag(value):
    return isinstance(value, str) and 0 < len(value) <= 64 and re.fullmatch(
        r'[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9_-]*[A-Za-z0-9])?)*', value) is not None


def clients(manager, agent_id, args):
    params = client_params(args)
    agent = _agent(manager, agent_id, MIN_CLIENTS_AGENT_VERSION)
    data = _request(agent, 'clients', params, min_version=MIN_CLIENTS_AGENT_VERSION)
    recorder, rows, sample = data.get('recorder'), data.get('rows'), data.get('sample_size')
    if (data.get('ok') is not True or not _valid_recorder_tag(recorder)
            or params.get('recorder', recorder) != recorder
            or type(sample) is not int or not 0 <= sample <= CLIENT_SAMPLE_LIMIT
            or not isinstance(rows, list) or len(rows) > CLIENT_SAMPLE_LIMIT):
        raise MonitorError('INVALID_RESPONSE')
    result, seen, total = [], set(), 0
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('key'), str):
            raise MonitorError('INVALID_RESPONSE')
        ip, count = row['key'], row.get('count')
        try:
            if '%' in ip:
                raise ValueError
            ipaddress.ip_address(ip)
        except ValueError:
            raise MonitorError('INVALID_RESPONSE') from None
        if ip in seen or type(count) is not int or not 0 < count <= sample:
            raise MonitorError('INVALID_RESPONSE')
        seen.add(ip)
        total += count
        result.append({'ip': ip, 'count': count})
    if total != sample:
        raise MonitorError('INVALID_RESPONSE')
    return {'success': True, 'recorder': recorder, 'clients': result,
            'sample_size': sample, 'sample_limited': sample == CLIENT_SAMPLE_LIMIT}
