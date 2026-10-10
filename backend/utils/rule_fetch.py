"""Shared proxy settings for server-side rule downloads.

Rule sources deliberately use normal hostname resolution, including Fake-IP
networks. Only URL syntax is checked here; this is not a public-IP-only gate.
"""
from urllib.parse import urljoin, urlsplit
import requests


class _RuleSession(requests.Session):
    def resolve_redirects(self, *args, **kwargs):
        # Requests otherwise buffers redirect bodies to prepare response.next,
        # even with allow_redirects=False. request_rule owns all redirect hops.
        return iter(())


def _origin(parsed):
    return (parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == 'https' else 80))


def validate_rule_url(url: str):
    if not isinstance(url, str) or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise ValueError('Invalid HTTP/HTTPS URL')
    try:
        parsed = urlsplit(url)
        port = parsed.port
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        if any(c.isspace() for c in parsed.netloc) or '\\' in parsed.netloc:
            raise ValueError
    except (ValueError, UnicodeError) as exc:
        raise ValueError('Invalid HTTP/HTTPS URL') from exc
    return parsed


def normalize_rule_fetch_proxy(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError('规则下载代理必须是 HTTP 或 HTTPS 地址')
    value = value.strip()
    if not value:
        return ''
    try:
        parsed = validate_rule_url(value)
        if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
            raise ValueError
    except ValueError as exc:
        raise ValueError('规则下载代理必须是 HTTP 或 HTTPS 地址，不能包含路径、查询参数或片段') from exc
    return parsed._replace(path='').geturl()


def rule_fetch_settings(config_data=None):
    if config_data is None:
        # Import lazily: configuration validation also uses this module.
        from backend.common.config import get_system_config
        config_data = get_system_config()
    return config_data.get('system_config', {})


def is_internal_rule_url(url: str, config_data=None) -> bool:
    """Keep requests to ConfigFlow itself away from the download proxy."""
    settings = rule_fetch_settings(config_data)
    parsed = urlsplit(url)
    from flask import has_request_context, request
    bases = [settings.get('server_domain', '')]
    if has_request_context():
        bases.append(request.host_url)
    for base in bases:
        if not base:
            continue
        origin = urlsplit(base)
        if _origin(parsed) != _origin(origin):
            continue
        prefix = origin.path.rstrip('/') + '/api/'
        if parsed.path.startswith(prefix):
            return True
    return False


def get_rule_fetch_proxy(config_data=None) -> str:
    return normalize_rule_fetch_proxy(rule_fetch_settings(config_data).get('rule_fetch_proxy', ''))


def rule_request_options(url: str, config_data=None) -> dict:
    if is_internal_rule_url(url, config_data):
        return {'proxies': {'http': '', 'https': ''}}
    proxy = get_rule_fetch_proxy(config_data)
    return {'proxies': {'http': proxy, 'https': proxy}} if proxy else {}


def _request_with_https_proxy(url, method, options, kwargs):
    """Verify the proxy's TLS even when the rule URL itself uses plain HTTP."""
    import os
    import ssl
    import requests

    with _RuleSession() as session:
        settings = session.merge_environment_settings(
            url, dict(options['proxies']), kwargs.get('stream', False),
            kwargs.get('verify', True), kwargs.get('cert'),
        )
        verify = settings['verify']
        if verify is False:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        else:
            bundle = verify if isinstance(verify, (str, os.PathLike)) else requests.certs.where()
            context = ssl.create_default_context(**{
                'capath' if os.path.isdir(bundle) else 'cafile': bundle,
            })

        class HTTPSProxyAdapter(requests.adapters.HTTPAdapter):
            def proxy_manager_for(self, proxy, **proxy_kwargs):
                proxy_kwargs['proxy_ssl_context'] = context
                return super().proxy_manager_for(proxy, **proxy_kwargs)

        adapter = HTTPSProxyAdapter()
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return getattr(session, method)(url, **kwargs, **options)


def request_rule(url: str, *, method='get', config_data=None, max_redirects=3, **kwargs):
    """Fetch a rule by hostname and choose its proxy again on every redirect.

    An empty setting preserves Requests' normal environment behavior. Explicit
    proxies never fall back to a direct connection. Internal API requests are
    direct and may only redirect within that origin's API paths.
    """
    import requests
    if config_data is None:
        from backend.common.config import get_system_config
        config_data = get_system_config()
    if method not in ('get', 'head'):
        raise ValueError('Rule requests only support GET and HEAD')
    kwargs['allow_redirects'] = False
    kwargs.setdefault('verify', True)
    from backend.converters.mihomo import apply_github_proxy_domain
    current = url if is_internal_rule_url(url, config_data) else apply_github_proxy_domain(url, config_data)
    for hop in range(max_redirects + 1):
        parsed = validate_rule_url(current)
        internal = is_internal_rule_url(current, config_data)
        options = rule_request_options(current, config_data)
        proxy = options.get('proxies', {}).get(parsed.scheme, '')
        if urlsplit(proxy).scheme == 'https':
            response = _request_with_https_proxy(current, method, options, kwargs)
        elif kwargs.get('stream'):
            with _RuleSession() as session:
                response = getattr(session, method)(current, **kwargs, **options)
        else:
            response = getattr(requests, method)(current, **kwargs, **options)
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        try:
            location = response.headers.get('Location')
            if not location:
                raise ValueError('Redirect without Location')
            target = urljoin(current, location)
            target_parts = validate_rule_url(target)
            if internal and (_origin(parsed) != _origin(target_parts)
                             or not is_internal_rule_url(target, config_data)):
                raise ValueError('Internal API redirect leaves its origin')
            if hop == max_redirects:
                raise ValueError('Too many redirects')
            current = target
        finally:
            response.close()
