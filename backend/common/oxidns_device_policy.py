"""Profile-owned device rules, patched into the native draft without regeneration."""
from copy import deepcopy
import hashlib
import ipaddress
import json

import yaml

from backend.common.oxidns_settings import OxidnsSettingsError

POLICY_TAG = 'cf_device_policy'
DIRECT_TAG = 'cf_device_direct'
HOOK = {'exec': f'jump {POLICY_TAG}'}
DIRECT = {'tag': DIRECT_TAG, 'type': 'sequence', 'args': [
    {'exec': '$forward_local'}, {'matches': ['!has_resp'], 'exec': 'reject 3'},
    {'exec': 'accept'},
]}


def normalize_policies(value):
    if not isinstance(value, list) or len(value) > 512:
        raise OxidnsSettingsError('设备策略必须为列表，最多 512 个设备。')
    result, seen = [], set()
    for row in value:
        if not isinstance(row, dict) or set(row) - {'ip', 'name', 'mode'}:
            raise OxidnsSettingsError('设备策略仅支持 IP、备注和策略。')
        raw = row.get('ip')
        try:
            if not isinstance(raw, str) or '%' in raw:
                raise ValueError()
            address = ipaddress.ip_address(raw.strip())
            # OxiDNS sees IPv4-mapped peers as the same IPv4 client.
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
                address = address.ipv4_mapped
            if address.is_unspecified or address.is_multicast:
                raise ValueError()
        except ValueError:
            raise OxidnsSettingsError('请输入单个有效的客户端 IPv4 或 IPv6 地址。') from None
        ip = str(address)
        if ip in seen:
            raise OxidnsSettingsError(f'客户端 IP 重复：{ip}')
        seen.add(ip)
        name, mode = row.get('name', ''), row.get('mode')
        if not isinstance(name, str) or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise OxidnsSettingsError('设备备注最多 80 个字符，不能包含换行或控制字符。')
        if mode not in ('smart', 'direct'):
            raise OxidnsSettingsError('设备策略必须为智能分流或直连。')
        result.append({'ip': ip, 'name': name.strip(), 'mode': mode})
    return result


def revision(settings):
    return hashlib.sha256(json.dumps(settings, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def _policy_plugin(rows):
    direct = [row['ip'] for row in rows if row['mode'] == 'direct']
    if not direct:
        return None
    return {'tag': POLICY_TAG, 'type': 'sequence', 'args': [
        {'matches': ['client_ip ' + ' '.join(direct)], 'exec': f'goto {DIRECT_TAG}'},
        {'exec': 'return'},
    ]}


def patch_document(document, previous, rows):
    """Own exactly two plugins and one hook; refuse altered or ambiguous chains."""
    from backend.common.oxidns_common_routing import _plugins, _prefix_end

    document = deepcopy(document)
    plugins = _plugins(document)
    main = plugins.get('sequence_main', {})
    args = main.get('args')
    forward = plugins.get('forward_local', {})
    servers = [p for p in plugins.values() if str(p.get('type', '')).endswith('_server')]
    if document.get('include') or main.get('type') != 'sequence' or not isinstance(args, list) or not args or \
            any(not isinstance(item, dict) or not isinstance(item.get('exec'), str) for item in args) or \
            args[-1] not in ({'exec': 'goto china_dns'}, {'exec': 'goto proxy_dns_seq'}) or \
            forward.get('type') != 'forward' or not isinstance(forward.get('args'), dict) or \
            not forward['args'].get('upstreams') or not servers or \
            any(not isinstance(p.get('args'), dict) or p['args'].get('entry') != 'sequence_main' for p in servers):
        raise OxidnsSettingsError('当前使用自定义处理链，无法安全插入设备策略；请先在 OxiDNS 完整参数中调整主处理链和本地上游。')

    old = _policy_plugin(previous)
    owned = {tag: plugins[tag] for tag in (POLICY_TAG, DIRECT_TAG) if tag in plugins}
    hooks = [i for i, item in enumerate(args) if item == HOOK]
    if old:
        if owned != {POLICY_TAG: old, DIRECT_TAG: DIRECT} or len(hooks) != 1:
            raise OxidnsSettingsError('设备策略执行链已被手动修改，请先在完整参数中恢复，避免覆盖自定义内容。')
        index = hooks[0]
        args.pop(index)
        if index != _prefix_end(args):
            raise OxidnsSettingsError('设备策略的位置已被修改，请先恢复到分流规则之前。')
    elif owned or hooks:
        raise OxidnsSettingsError('完整参数中存在同名设备策略插件，请先处理命名冲突。')
    # Extra calls to owned plugins could turn a per-client rule into a global rule.
    remaining = [p for p in document['plugins'] if p.get('tag') not in (POLICY_TAG, DIRECT_TAG)]
    text = json.dumps(remaining)
    if POLICY_TAG in text or DIRECT_TAG in text:
        raise OxidnsSettingsError('设备策略插件存在额外引用，请先在完整参数中处理。')
    document['plugins'] = remaining
    new = _policy_plugin(rows)
    if new:
        args.insert(_prefix_end(args), deepcopy(HOOK))
        document['plugins'].extend([new, deepcopy(DIRECT)])
    return document


def read_policies(settings, config):
    from backend.common.oxidns_editor import prepare_editor
    from backend.common.oxidns_native import load_native_yaml

    rows = normalize_policies(settings.get('device_policies', []))
    try:
        current = prepare_editor(settings, config)
        patch_document(load_native_yaml(current['native_config']), rows, rows)
        return {'policies': rows, 'editable': True, 'reason': ''}
    except OxidnsSettingsError as exc:
        return {'policies': rows, 'editable': False, 'reason': str(exc)}


def apply_policies(settings, config, rows, base_url=''):
    from backend.common.oxidns_editor import prepare_editor
    from backend.common.oxidns_native import load_native_yaml

    rows = normalize_policies(rows)
    current = prepare_editor(settings, config, base_url)
    previous = normalize_policies(current.get('device_policies', []))
    document = patch_document(load_native_yaml(current['native_config']), previous, rows)
    current['native_config'] = yaml.safe_dump(document, allow_unicode=True, sort_keys=False)
    current['device_policies'] = rows
    if isinstance(current.get('template_settings'), dict):
        current['template_settings']['device_policies'] = deepcopy(rows)
    return current
