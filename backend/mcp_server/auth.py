"""MCP 端点的认证

复用 ConfigFlow 既有的凭证，不引入新的证书体系：
- 未启用登录认证且未设置配置令牌时放行（与现有「认证完全可选」的行为一致）
- 前端签发的 JWT（Authorization: Bearer <jwt>）
- 系统设置里的配置令牌，支持 Authorization: Bearer <token> 和 ?token=<token>
  两种带法，因为多数 MCP 客户端只能配其中一种
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


def authenticate() -> bool:
    """校验当前 MCP 请求，返回是否放行"""
    config_token = _config_token()

    # 既没开登录认证也没设配置令牌 —— 与现有 REST 行为保持一致，直接放行
    if not is_auth_enabled() and not config_token:
        return True

    bearer = _bearer()

    if config_token:
        if bearer == config_token:
            return True
        if request.args.get('token', '') == config_token:
            return True

    if bearer:
        payload = verify_token(bearer)
        if payload and not (isinstance(payload, dict) and 'error' in payload):
            return True

    return False
