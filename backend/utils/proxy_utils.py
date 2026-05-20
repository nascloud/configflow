"""代理工具函数"""


def fix_proxy_fields(proxy: dict) -> dict:
    """补全代理节点的必需字段（如 VLESS 的 encryption: none）。

    某些来源（Sub-Store、手动 YAML）可能缺少必需字段导致 Mihomo 报错。
    """
    if proxy and proxy.get('type') == 'vless':
        if proxy.get('encryption', '') in ('', 'zero', None):
            proxy['encryption'] = 'none'
        reality_opts = proxy.get('reality-opts')
        if isinstance(reality_opts, dict) and 'short-id' not in reality_opts:
            reality_opts['short-id'] = ''
    return proxy
