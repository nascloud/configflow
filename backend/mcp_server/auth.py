"""MCP 端点的认证

复用 ConfigFlow 既有的凭证，不引入新的证书体系。

MCP 工具可执行导出整份配置、重置系统、卸载 Agent 等管理操作，因此凭证的
接受范围必须与「管理员」对齐，而不是与「订阅链接」对齐：

- 启用了账号密码登录时，只接受前端签发的 JWT。
  配置令牌是分发给代理客户端设备、用于拉取配置的只读凭证，在 REST 上也
  取不到订阅列表和配置导出，不能让它经由 MCP 获得管理员权限。
- 未启用账号密码登录时，配置令牌是此时唯一的凭证，接受它；
  支持 Authorization: Bearer <token> 和 ?token=<token> 两种带法，
  因为多数 MCP 客户端只能配其中一种。
- 两者都没有配置时放行，与现有「认证完全可选」的行为一致。
"""
from flask import request

from backend.common.auth import is_auth_enabled, verify_token


def _config_token() -> str:
    from backend.common.config import get_config

    return (get_config().get('system_config', {}) or {}).get('config_token', '') or ''


def _bearer() -> str:
    header = request.headers.get('Authorization', '')
    if header.startswith('Bearer '):
        return header[len('Bearer '):].strip()
    return ''


def _is_valid_jwt(token: str) -> bool:
    if not token:
        return False
    payload = verify_token(token)
    return bool(payload) and not (isinstance(payload, dict) and 'error' in payload)


def authenticate() -> bool:
    """校验当前 MCP 请求，返回是否放行"""
    config_token = _config_token()
    bearer = _bearer()

    # 启用了账号密码登录：只有管理员 JWT 能调用 MCP
    if is_auth_enabled():
        return _is_valid_jwt(bearer)

    # 未启用登录，但设置了配置令牌：此时它是唯一凭证
    if config_token:
        return bearer == config_token or request.args.get('token', '') == config_token

    # 未配置任何认证 —— 与现有 REST 行为保持一致
    return True
