"""Request-local provider delivery validation, before any durable side effect."""
from dataclasses import dataclass
import json

from backend.utils.dialer_references import DialerReferenceError, validate_emitted


@dataclass(frozen=True)
class DeliverySnapshot:
    profile_id: str
    main_json: str

    @classmethod
    def capture(cls, profile_id, main):
        return cls(profile_id, json.dumps(main))

    @property
    def has_chains(self):
        main = json.loads(self.main_json)
        return any(p.get('dialer-proxy') is not None for p in main.get('proxies', []))

    def validate(self, proxies, *, force_chains=False):
        validate_proxy_shapes(proxies)
        main = json.loads(self.main_json)
        # Suppress only cross-artifact overlap, never duplicates inside a provider.
        names = {p['name'] for p in proxies}
        # Cross-artifact overlap can remove the last visible main dialer. Strict
        # activation belongs to the immutable original snapshot, not that filtered
        # graph; an explicit False from a caller must never deactivate it.
        validate_emitted({
            'proxies': [p for p in main.get('proxies', []) if p['name'] not in names] + proxies,
            'proxy-groups': main.get('proxy-groups', []),
        }, force_chains=self.has_chains or force_chains)


def validate_proxy_shapes(proxies):
    if not isinstance(proxies, list):
        raise DialerReferenceError('Provider proxies 必须是数组')
    for proxy in proxies:
        if (not isinstance(proxy, dict) or not isinstance(proxy.get('name'), str)
                or not proxy['name'].strip()):
            raise DialerReferenceError('Provider 代理必须是含非空 name 的对象')
        if 'proxies' in proxy and (not isinstance(proxy['proxies'], list) or
                any(not isinstance(name, str) or not name.strip() for name in proxy['proxies'])):
            raise DialerReferenceError('Provider proxies 引用必须是字符串数组')
        if 'type' in proxy and not isinstance(proxy['type'], str):
            raise DialerReferenceError('Provider type 必须是字符串')


def parse_provider_proxies(text):
    """Reject malformed metadata before legacy field normalization can mask it."""
    import yaml
    from backend.utils.sub_store_client import parse_proxies_from_yaml
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # Content errors must propagate through the transport-fallback boundary.
        # Do not include provider bytes or parser excerpts in the public error.
        raise DialerReferenceError('Provider YAML 内容无效') from exc
    if not isinstance(data, dict):
        raise DialerReferenceError('Provider 必须是 YAML 对象')
    validate_proxy_shapes(data.get('proxies', []))
    return parse_proxies_from_yaml(text)


def prepare_subscription_provider(sub, config, snapshot, *, fetch=None,
                                  allow_transport_fallback=False):
    """Return exact validated output and staged cache update; never write here."""
    import uuid
    import yaml
    from backend.utils.sub_store_client import (get_subscription_proxies_yaml,
                                               parse_proxies_from_yaml, proxies_to_nodes)
    from backend.utils.subscription_cache import load_subscription_cache
    from backend.utils.strategy_references import StrategyReferenceError
    from backend.utils.dialer_references import validate_shapes
    from backend.converters.mihomo import convert_node_to_mihomo, IndentDumper

    fetch = fetch or get_subscription_proxies_yaml
    update = None
    proxies = None
    if sub.get('url'):
        try:
            text, _source = fetch(sub['id'], sub['url'])
            proxies = parse_provider_proxies(text)
            snapshot.validate(proxies)
            if proxies:
                nodes = proxies_to_nodes(proxies)
                for node in nodes:
                    node['subscription_id'] = sub['id']
                    node['subscription_name'] = sub['name']
                    node.setdefault('id', f"node_{uuid.uuid4().hex[:8]}")
                update = (sub['id'], nodes, {'subscription_name': sub['name'], 'url': sub['url']})
        except StrategyReferenceError:
            raise
        except Exception:
            proxies = None
    if proxies is None:
        cache = load_subscription_cache(sub['id'], profile_id=snapshot.profile_id)
        if not cache or not cache.get('nodes'):
            if allow_transport_fallback and not snapshot.has_chains:
                return {'content': '', 'cache_updates': []}
            raise DialerReferenceError('订阅没有可用缓存或远程代理')
        validate_shapes({'nodes': cache['nodes']}, require_ids=False)
        proxies = [p for n in cache['nodes'] if (p := convert_node_to_mihomo(n))]
    snapshot.validate(proxies)
    content = yaml.dump({'proxies': proxies}, Dumper=IndentDumper, allow_unicode=True,
                        default_flow_style=False, sort_keys=False, indent=2)
    return {'content': content, 'cache_updates': [update] if update else []}


def validate_rendered_bundle(snapshot, providers):
    """Validate all exact provider collections with one monotonic activation.

    Providers are already materialized and their cache updates remain staged.
    Never fetch/render again here: discovery and final validation must inspect
    the same bytes, including providers rendered before a late chain appeared.
    """
    import yaml
    collections = []
    for provider in providers:
        content = provider['content']
        if content:
            try:
                data = yaml.safe_load(content)
            except yaml.YAMLError as exc:
                raise DialerReferenceError('Provider YAML 内容无效') from exc
            if not isinstance(data, dict):
                raise DialerReferenceError('Provider 必须是 YAML 对象')
            proxies = data.get('proxies', [])
            validate_proxy_shapes(proxies)
        else:
            proxies = None
        collections.append(proxies)
    chains = snapshot.has_chains or any(
        p.get('dialer-proxy') is not None
        for proxies in collections if proxies is not None for p in proxies)
    if chains and any(proxies is None for proxies in collections):
        raise DialerReferenceError('拨号拓扑不能使用未验证的 Provider URL fallback')
    # Provider-only activation also applies to the original main namespace.
    snapshot.validate([], force_chains=chains)
    for proxies in collections:
        if proxies is not None:
            snapshot.validate(proxies, force_chains=chains)
    return chains


def commit_cache_updates(updates, profile_id):
    from backend.utils.subscription_cache import save_subscription_nodes
    for sub_id, nodes, metadata in updates:
        save_subscription_nodes(sub_id, nodes, metadata, profile_id=profile_id)
