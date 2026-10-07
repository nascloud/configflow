"""Profile-local Mihomo dialer metadata. Names are resolved only at emission."""
from backend.utils.strategy_references import StrategyReferenceError


class DialerReferenceError(StrategyReferenceError):
    pass


def validate_shapes(profile, *, require_ids=True):
    """Validate metadata before attribute access, hashing or string parsing."""
    for key in ('nodes', 'proxy_groups'):
        items = profile.get(key, [])
        if not isinstance(items, list):
            raise DialerReferenceError(f'{key} 必须是数组')
        for obj in items:
            if not isinstance(obj, dict):
                raise DialerReferenceError('资源必须是 JSON 对象')
            for field in ('id', 'name'):
                # Legacy subscription caches may omit generated resource IDs.
                if field == 'id' and not require_ids and field not in obj:
                    continue
                value = obj.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise DialerReferenceError(f'资源 {field} 必须是非空字符串')
            if key == 'nodes':
                if not isinstance(obj.get('params', {}), dict):
                    raise DialerReferenceError('节点 params 必须是对象，不能为 null')
                if obj.get('proxy_string') is not None and not isinstance(obj['proxy_string'], str):
                    raise DialerReferenceError('节点 proxy_string 必须是字符串')
            else:
                for field in ('manual_nodes', 'include_groups', 'proxies', 'subscriptions', 'aggregations', 'use'):
                    if field in obj and (not isinstance(obj[field], list) or
                            any(not isinstance(v, str) or not v.strip() for v in obj[field])):
                        raise DialerReferenceError(f'策略组 {field} 必须是字符串数组')
                order = obj.get('proxies_order', [])
                if not isinstance(order, list) or any(not isinstance(i, dict) or
                        not isinstance(i.get('type'), str) or not isinstance(i.get('id'), str)
                        for i in order):
                    raise DialerReferenceError('策略组 proxies_order 必须是含 type/id 字符串的对象数组')


def validate_dialers(profile):
    validate_shapes(profile)
    nodes = profile.get('nodes', [])
    if any(n.get('dialer_ref') is not None or raw_dialer(n) is not None for n in nodes):
        names = []
        for items in (nodes, profile.get('proxy_groups', [])):
            ids = [obj.get('id') for obj in items]
            if len(set(ids)) != len(ids):
                raise DialerReferenceError('拨号引用要求资源 ID 唯一')
            names.extend(obj.get('name') for obj in items if obj.get('enabled', True))
        if (any(not isinstance(name, str) or not name.strip() for name in names) or
                len(set(names)) != len(names) or
                any(name in {'DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'PASS-RULE', 'COMPATIBLE'} for name in names)):
            raise DialerReferenceError('拨号代理要求代理/策略组名称非空、唯一且不与内置策略冲突')
    for node in nodes:
        ref = node.get('dialer_ref')
        if ref is None:
            continue
        if (not isinstance(ref, dict) or set(ref) != {'type', 'id'} or
                ref.get('type') not in ('node', 'group') or
                not isinstance(ref.get('id'), str) or not ref['id'].strip()):
            raise DialerReferenceError('dialer_ref 必须为 {type: node|group, id: 非空字符串}')
        targets = profile.get('nodes' if ref['type'] == 'node' else 'proxy_groups', [])
        target = next((t for t in targets if t.get('id') == ref['id']), None)
        if target is None or not target.get('enabled', True):
            raise DialerReferenceError('拨号引用不存在、未启用或不在当前配置空间')
        if ref['type'] == 'node' and target.get('id') == node.get('id'):
            raise DialerReferenceError('节点不能引用自身作为拨号代理')
        if ref['type'] == 'group' and any(target.get(k) for k in
                ('subscriptions', 'aggregations', 'use', 'follow_group', 'include_all', 'include-all')):
            raise DialerReferenceError('第一阶段拨号代理只支持静态策略组，不支持订阅、聚合、use 或跟随模式')


    graph = dependency_graph(profile)
    # Managed references have a manual-only contract. Legacy raw name references
    # remain a separate compatibility mode and are not rewritten into managed refs.
    objects = {('node', n['id']): n for n in nodes}
    objects.update({('group', g['id']): g for g in profile.get('proxy_groups', [])})
    pending = [('node', n['id']) for n in nodes if n.get('dialer_ref') is not None]
    visited = set()
    while pending:
        key = pending.pop()
        if key in visited or key[0] == 'builtin':
            continue
        visited.add(key)
        obj = objects.get(key)
        if obj is None:
            raise DialerReferenceError('拨号依赖不存在')
        if key[0] == 'node' and obj.get('subscription_id'):
            raise DialerReferenceError('稳定拨号引用仅支持手动节点，不能使用订阅节点')
        if key in graph:
            pending.extend(graph[key])
        elif key[0] == 'node':
            # Disabled managed sources still retain the manual-only metadata
            # contract, although they are not roots of the emitted topology.
            target = dialer_target(profile, obj)
            if target:
                pending.append(target)
        else:
            pending.extend(group_members(obj))


def group_members(group):
    if group.get('proxies_order'):
        members = [(('node' if i.get('type') == 'node' else 'group'), i.get('id'))
                   for i in group['proxies_order'] if i.get('type') in ('node', 'strategy')]
    else:
        members = [('node', id) for id in group.get('manual_nodes', [])]
        members += [('group', id) for id in group.get('include_groups', [])]
        if not members and group.get('source') in ('node', 'strategy'):
            members = [('node' if group['source'] == 'node' else 'group', id) for id in group.get('proxies', [])]
    return [('builtin' if kind == 'node' and id in ('DIRECT', 'REJECT') else kind, id)
            for kind, id in members]


def raw_dialer(node):
    # Never contact Sub-Store while validating a write. URI results are checked
    # again after conversion, before artifacts or agent requests are produced.
    from backend.converters.mihomo import _parse_structured_proxy_string
    if node.get('proxy_string'):
        parsed = _parse_structured_proxy_string(node['proxy_string'])
        return parsed.get('dialer-proxy') if parsed else None
    return node.get('params', {}).get('dialer-proxy')


def dialer_target(profile, node):
    ref = node.get('dialer_ref')
    if ref is not None:
        return (ref['type'], ref['id'])
    name = raw_dialer(node)
    if name is None:
        return None
    if not isinstance(name, str) or not name.strip():
        raise DialerReferenceError('原始 dialer-proxy 必须是非空名称')
    if name in ('DIRECT', 'REJECT'):
        return ('builtin', name)
    matches = [(kind, obj['id']) for kind, items in
               (('node', profile.get('nodes', [])), ('group', profile.get('proxy_groups', [])))
               for obj in items if obj.get('name') == name and obj.get('enabled', True)]
    if len(matches) != 1:
        raise DialerReferenceError(f"原始 dialer-proxy '{name}' 不存在、未启用或名称不唯一，请改用稳定引用")
    return matches[0]


def dependency_graph(profile):
    validate_shapes(profile)
    objects = {('node', n['id']): n for n in profile.get('nodes', [])}
    objects.update({('group', g['id']): g for g in profile.get('proxy_groups', [])})
    graph = {}
    pending = [key for key, obj in objects.items()
               if key[0] == 'node' and obj.get('enabled', True) and
               (obj.get('dialer_ref') is not None or raw_dialer(obj) is not None)]
    while pending:
        key = pending.pop()
        if key in graph or key[0] == 'builtin':
            continue
        obj = objects.get(key)
        if not obj or not obj.get('enabled', True):
            raise DialerReferenceError('拨号依赖不存在或未启用')
        if key[0] == 'group':
            if (any(obj.get(k) for k in ('subscriptions', 'aggregations', 'use', 'follow_group', 'include_all', 'include-all')) or
                    any(i.get('type') not in ('node', 'strategy') for i in obj.get('proxies_order', []))):
                raise DialerReferenceError('第一阶段拨号代理只支持静态策略组及静态子组')
            children = group_members(obj)
        else:
            target = dialer_target(profile, obj)
            children = [target] if target else []
        graph[key] = children
        pending.extend(children)
    check_cycles(graph)
    return graph


def check_cycles(graph):
    """Iterative DFS with explicit enter/exit frames; no process recursion limit."""
    visiting, done = set(), set()
    for root in graph:
        pending = [(root, False)]
        while pending:
            key, exiting = pending.pop()
            if exiting:
                visiting.remove(key)
                done.add(key)
                continue
            if key in visiting:
                raise DialerReferenceError('拨号代理依赖存在循环')
            if key in done:
                continue
            visiting.add(key)
            pending.append((key, True))
            pending.extend((child, False) for child in reversed(graph.get(key, [])))


def incoming_dialers(profile, type, id):
    target = (type, id)
    direct = [('node', n['id']) for n in profile.get('nodes', [])
              if n.get('dialer_ref') == {'type': type, 'id': id}]
    graph = dependency_graph(profile)
    return direct + [key for key, children in graph.items() if target in children]


def resolve_dialer(profile, node):
    ref = node.get('dialer_ref')
    if ref is None:
        return None
    targets = profile.get('nodes' if ref['type'] == 'node' else 'proxy_groups', [])
    return next(t['name'] for t in targets if t.get('id') == ref['id'])


def overlay_dialer(profile, node, proxy):
    if not proxy and ('node', node.get('id')) in dependency_graph(profile):
        raise DialerReferenceError('拨号拓扑参与节点转换失败，不能省略节点')
    if proxy and node.get('dialer_ref') is not None:
        proxy['dialer-proxy'] = resolve_dialer(profile, node)
    return proxy


def validate_emitted(config, *, force_chains=False):
    objects = config.get('proxies', []) + config.get('proxy-groups', [])
    # Legacy no-chain exports intentionally retain their historical duplicate
    # name behavior. Strict graph identity applies only to dialer topologies.
    chains_active = force_chains or any(obj.get('dialer-proxy') is not None for obj in objects)
    if not chains_active:
        return
    names = [obj.get('name') for obj in objects]
    builtins = {'DIRECT', 'REJECT', 'REJECT-DROP', 'PASS', 'PASS-RULE', 'COMPATIBLE'}
    if len(set(names)) != len(names) or any(n in builtins for n in names):
        raise DialerReferenceError('生成的代理/策略组名称重复或与内置策略冲突')
    available = set(names) | builtins
    graph = {}
    for obj in objects:
        if chains_active and any(n not in available for n in obj.get('proxies', [])):
            raise DialerReferenceError('拨号拓扑中的策略组引用了未生成的代理或策略组')
        target = obj.get('dialer-proxy')
        if target is not None and (not isinstance(target, str) or target not in available):
            raise DialerReferenceError('生成的 dialer-proxy 引用了不存在的代理或策略组')
        graph[obj['name']] = ([target] if target is not None else []) + [n for n in obj.get('proxies', []) if n in names]
    # A raw/provider name must obey the same phase-one static scope as a
    # managed reference. Only inspect the dialer's dependency closure: unrelated
    # dynamic groups remain supported, including groups consuming this provider.
    groups = {g['name']: g for g in config.get('proxy-groups', [])}
    pending = [obj['dialer-proxy'] for obj in objects if obj.get('dialer-proxy') is not None]
    visited = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        group = groups.get(name)
        if group and any(group.get(k) for k in
                ('use', 'subscriptions', 'aggregations', 'follow_group', 'include_all', 'include-all')):
            raise DialerReferenceError('第一阶段拨号代理只支持静态策略组及静态子组')
        pending.extend(graph.get(name, []))
    check_cycles(graph)


def reject_surge_dialers(profile):
    from backend.converters.mihomo import convert_node_to_mihomo
    for node in profile.get('nodes', []):
        if not node.get('enabled', True):
            continue
        if node.get('dialer_ref') is not None or raw_dialer(node) is not None:
            raise DialerReferenceError('Surge 暂不支持 Mihomo 拨号代理拓扑，请使用 Mihomo 或显式移除拨号配置')
        if node.get('proxy_string'):
            proxy = convert_node_to_mihomo(node)
            if proxy and proxy.get('dialer-proxy') is not None:
                raise DialerReferenceError('Surge 暂不支持转换结果中的 dialer-proxy')


def dependency_node_ids(profile, selected, include_roots=True):
    """Resolve node/group dependency closure from manual node roots."""
    graph = dependency_graph(profile)
    roots = [('node', id) for id in selected]
    pending = roots if include_roots else [child for key in roots for child in graph.get(key, [])]
    selected = set(selected) if include_roots else set()
    visited = set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        if key[0] == 'node':
            selected.add(key[1])
        pending.extend(graph.get(key, []))
    return selected
