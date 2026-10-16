"""Cross-field rules from OxiDNS 1.6.1's config and network deserializers.

This module reports errors without rewriting the document. Option<T> nulls,
explicit empty route lists, unknown extension fields, and ignored legacy
bootstrap settings therefore retain their original meaning and spelling.
File availability, TLS contents and build features remain target-core checks.
"""
import ipaddress
import re
from urllib.parse import urlsplit


_ENV = re.compile(r'\$\{[^}]+\}')


def _deferred(value):
    return isinstance(value, str) and bool(_ENV.search(value))


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _endpoint(value):
    """Inspect an already schema-validated upstream without DNS lookups."""
    if not isinstance(value, str) or _deferred(value):
        return None, None
    try:
        result = urlsplit(value if '//' in value else 'udp://' + value)
        return result.scheme.lower(), result.hostname
    except (ValueError, AttributeError):
        return None, None


def _literal_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except (ValueError, TypeError):
        return False


def _bootstrap_socket(value):
    """Legacy bootstrap is SocketAddr, unlike listen's :port shorthand."""
    if not isinstance(value, str):
        return False
    value = value.strip()
    match = re.fullmatch(r'\[([^\]]+)\]:(\d+)', value)
    if match:
        host, port = match.groups()
        try:
            return ipaddress.ip_address(host).version == 6 and 0 <= int(port) <= 65535
        except ValueError:
            return False
    match = re.fullmatch(r'([^:]+):(\d+)', value)
    if not match:
        return False
    host, port = match.groups()
    try:
        return ipaddress.ip_address(host).version == 4 and 0 <= int(port) <= 65535
    except ValueError:
        return False


def collect_global_issues(document):
    """Return path-addressed issues; the shared validator applies types first."""
    issues = []

    def error(path, message, code='constraint'):
        issues.append({'path': path, 'message': message, 'code': code})

    def nonblank(value, path):
        if isinstance(value, str) and not _deferred(value) and not value.strip():
            error(path, '必须填写非空文本')

    def tls_pair(config, path, required=False):
        cert, key = config.get('cert'), config.get('key')
        # Null means absent. An explicitly configured path must be nonempty.
        if required or cert is not None or key is not None:
            if cert is None:
                error(path + ['cert'], '启用 TLS 时必须同时配置证书和私钥')
            else:
                nonblank(cert, path + ['cert'])
            if key is None:
                error(path + ['key'], '启用 TLS 时必须同时配置证书和私钥')
            else:
                nonblank(key, path + ['key'])

    if not isinstance(document, dict):
        return issues

    log = _mapping(document.get('log'))
    level = log.get('level')
    if isinstance(level, str) and not _deferred(level) and level.lower() not in (
            'off', 'trace', 'debug', 'info', 'warn', 'error'):
        error(['log', 'level'], '日志级别必须为 off、trace、debug、info、warn 或 error', 'enum')

    http = _mapping(_mapping(document.get('api')).get('http'))
    ssl = _mapping(http.get('ssl'))
    tls_pair(ssl, ['api', 'http', 'ssl'])
    if ssl.get('require_client_cert') is True and ssl.get('client_ca') is None:
        error(['api', 'http', 'ssl', 'client_ca'], '要求客户端证书时必须配置 client_ca')
    if ssl.get('client_ca') is not None:
        nonblank(ssl.get('client_ca'), ['api', 'http', 'ssl', 'client_ca'])

    outbound = _mapping(_mapping(document.get('network')).get('outbound'))
    profiles = _mapping(outbound.get('profiles'))

    def profile_reference(value, path, strict_name=False, allow_empty=False):
        if not isinstance(value, str) or _deferred(value):
            return
        name = value.strip()
        if not name:
            if not allow_empty:
                error(path, '出站配置名称不能为空')
            return
        if strict_name and name != value:
            error(path, '出站配置名称不能包含首尾空白')
        if name not in profiles:
            error(path, '引用的出站配置未在 network.outbound.profiles 中定义', 'reference')

    profile_reference(outbound.get('default'), ['network', 'outbound', 'default'], strict_name=True)
    for name, raw_profile in profiles.items():
        path = ['network', 'outbound', 'profiles', name]
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            error(path, '出站配置名称必须为不含首尾空白的非空文本')
        profile = _mapping(raw_profile)
        resolver, proxy = profile.get('resolver'), profile.get('proxy')
        if isinstance(resolver, str) and not _deferred(resolver) and resolver.strip().lower() != 'system':
            error(path + ['resolver'], '解析器简写只支持 system', 'enum')
        if isinstance(proxy, str) and not _deferred(proxy) and proxy.strip().lower() not in ('none', 'direct'):
            error(path + ['proxy'], '代理简写只支持 none 或 direct', 'enum')
        resolver = _mapping(resolver)
        uses_proxy = resolver.get('proxy') == 'profile'
        if uses_proxy and not isinstance(_mapping(proxy).get('socks5'), str):
            error(path + ['resolver', 'proxy'], '使用 profile 代理时，当前出站配置必须设置 socks5 代理')
        servers = resolver.get('nameservers')
        for index, server in enumerate(servers if isinstance(servers, list) else []):
            server = _mapping(server)
            item_path = path + ['resolver', 'nameservers', index]
            scheme, host = _endpoint(server.get('addr'))
            if host and not _literal_ip(host) and server.get('dial_addr') is None:
                error(item_path + ['dial_addr'], '域名型 nameserver 必须配置 dial_addr，避免引导解析循环')
            if uses_proxy and scheme in ('udp', 'quic', 'doq', 'h3'):
                error(item_path + ['addr'], 'UDP、DoQ 和 DoH3 nameserver 不支持 profile SOCKS5 代理')

    plugins = document.get('plugins')
    for index, plugin in enumerate(plugins if isinstance(plugins, list) else []):
        plugin = _mapping(plugin)
        kind, args = plugin.get('type'), _mapping(plugin.get('args'))
        path = ['plugins', index, 'args']
        if kind in ('tcp_server', 'http_server'):
            tls_pair(args, path, required=kind == 'http_server' and args.get('enable_http3') is True)
        if kind == 'http_server':
            entries = args.get('entries')
            for offset, entry in enumerate(entries if isinstance(entries, list) else []):
                route = _mapping(entry).get('path')
                if isinstance(route, str) and not _deferred(route) and not route.startswith('/'):
                    error(path + ['entries', offset, 'path'], 'HTTP 路径必须以 / 开头', 'format')
        if kind in ('download', 'http_request', 'upgrade', 'ip_selector'):
            # The shared HTTP client treats empty outbound as the default.
            # ip_selector rejects it in its own config validator.
            profile_reference(args.get('outbound'), path + ['outbound'], allow_empty=kind != 'ip_selector')
        if kind != 'forward':
            continue
        upstreams = args.get('upstreams')
        for offset, upstream in enumerate(upstreams if isinstance(upstreams, list) else []):
            upstream = _mapping(upstream)
            item_path = path + ['upstreams', offset]
            profile_reference(upstream.get('outbound'), item_path + ['outbound'])
            minimum, maximum = upstream.get('min_conns'), upstream.get('max_conns')
            if maximum is None:
                maximum = 64
            if _integer(minimum) and _integer(maximum) and minimum > maximum:
                error(item_path + ['min_conns'], '不能大于有效 max_conns（未配置时为 64）')
            _, host = _endpoint(upstream.get('addr'))
            bootstrap = upstream.get('bootstrap')
            # Core ignores bootstrap for literal upstream IPs or explicit
            # dial_addr. Preserve that legal ignored field, including "".
            if (host and not _literal_ip(host) and upstream.get('dial_addr') is None
                    and isinstance(bootstrap, str) and not _deferred(bootstrap)
                    and not _bootstrap_socket(bootstrap)):
                error(item_path + ['bootstrap'], '引导 DNS 必须为 IP:端口 或 [IPv6]:端口', 'format')
    return issues
