"""认证相关工具模块"""
import os
import jwt
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify

# JWT 配置
JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'your-secret-key-change-this-in-production')
JWT_ALGORITHM = 'HS256'
JWT_EXPIRATION_HOURS = 24

# 登录配置（从环境变量读取）
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', '')  # 默认为空表示不需要登录
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '')


def is_auth_enabled():
    """检查是否启用了认证"""
    return bool(ADMIN_USERNAME and ADMIN_PASSWORD)


def generate_token(username):
    """生成 JWT token"""
    payload = {
        'username': username,
        # Use UTC timestamps to keep PyJWT numeric dates consistent across timezones
        'exp': datetime.utcnow() + timedelta(hours=JWT_EXPIRATION_HOURS),
        'iat': datetime.utcnow()
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def verify_token(token):
    """验证 JWT token"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return {'error': 'expired'}
    except jwt.InvalidTokenError as e:
        return {'error': 'invalid', 'detail': str(e)}


def validate_token_or_jwt(request_obj):
    """验证 JWT token（前端）或 URL query token（外部客户端）

    Args:
        request_obj: Flask request 对象

    Returns:
        dict: {'valid': bool, 'message': str}
    """
    # 2. 检查 URL query token（用于外部客户端）
    from backend.common.config import config_data
    config_token = config_data.get('system_config', {}).get('config_token', '')

    # 如果没有启用认证，直接通过
    if not is_auth_enabled() and not config_token:
        return {'valid': True}

    # 1. 先检查 Authorization header (JWT token)
    auth_header = request_obj.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        token = auth_header.split(' ')[1]
        payload = verify_token(token)
        # 如果 payload 不为 None 且不包含 error 键，说明验证成功
        if payload and not (isinstance(payload, dict) and 'error' in payload):
            return {'valid': True}

    # 如果没有配置 config_token，允许无 token 访问（外部客户端）
    if not config_token:
        return {'valid': True}

    # 如果配置了 config_token，检查 URL query 参数中的 token
    url_token = request_obj.args.get('token', '')
    if url_token and url_token == config_token:
        return {'valid': True}

    return {'valid': False, 'message': 'Invalid or missing authentication'}


def require_auth(f):
    """认证装饰器 - 只有在启用认证时才检查 token"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 如果没有设置用户名和密码，则不需要认证（直接放行，忽略任何 token）
        if not is_auth_enabled():
            return f(*args, **kwargs)

        # 认证已启用，检查 token
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'success': False, 'message': 'Unauthorized: Missing or invalid Authorization header'}), 401

        token = auth_header.split(' ')[1]
        payload = verify_token(token)

        # 检查验证结果
        if not payload:
            return jsonify({'success': False, 'message': 'Invalid or expired token'}), 401

        # 如果返回的是错误信息（字典包含 'error' 键）
        if isinstance(payload, dict) and 'error' in payload:
            if payload['error'] == 'expired':
                return jsonify({'success': False, 'message': 'Token expired', 'error': 'token_expired'}), 401
            else:
                return jsonify({'success': False, 'message': f"Invalid token: {payload.get('detail', 'unknown error')}", 'error': 'token_invalid'}), 401

        return f(*args, **kwargs)
    return decorated_function


def validate_required_env_vars():
    """验证必需的环境变量是否已设置"""
    # 开发环境检测：检查 DATA_DIR 环境变量
    # 如果 DATA_DIR 未设置或设置为 /data 但目录不存在，则认为是开发环境
    data_dir = os.environ.get('DATA_DIR', '/data')
    is_dev_mode = not os.path.exists(data_dir) if data_dir == '/data' else False

    # 也可以通过 SKIP_AUTH_CHECK 环境变量跳过验证（用于本地开发）
    if os.environ.get('SKIP_AUTH_CHECK', '').lower() == 'true':
        is_dev_mode = True

    if is_dev_mode:
        # 开发模式：如果没有设置认证信息，给出提示但不强制退出
        if not ADMIN_USERNAME or not ADMIN_PASSWORD:
            print('\n' + '=' * 80)
            print('INFO: Running in DEVELOPMENT mode without authentication')
            print('=' * 80)
            print('⚠️  Authentication is DISABLED. Anyone can access the application.')
            print('')
            print('To enable authentication in development, set:')
            print('  export ADMIN_USERNAME=admin')
            print('  export ADMIN_PASSWORD=your-password')
            print('  export JWT_SECRET_KEY=your-secret-key')
            print('')
            print('Production environments (Docker) REQUIRE authentication.')
            print('=' * 80 + '\n')
        return

    # 生产环境：强制要求配置
    missing_vars = []
    invalid_vars = []

    # 检查 ADMIN_USERNAME
    if not ADMIN_USERNAME or ADMIN_USERNAME.strip() == '':
        missing_vars.append('ADMIN_USERNAME')

    # 检查 ADMIN_PASSWORD
    if not ADMIN_PASSWORD or ADMIN_PASSWORD.strip() == '':
        missing_vars.append('ADMIN_PASSWORD')

    # 检查 JWT_SECRET_KEY
    if not JWT_SECRET_KEY or JWT_SECRET_KEY.strip() == '':
        missing_vars.append('JWT_SECRET_KEY')
    elif len(JWT_SECRET_KEY.strip()) < 32 or 'your-secret-key' in JWT_SECRET_KEY.lower():
        invalid_vars.append('JWT_SECRET_KEY (must be at least 32 characters and not use default value)')

    if missing_vars or invalid_vars:
        error_msg = '\n' + '=' * 80 + '\n'
        error_msg += '❌ ERROR: Authentication configuration is REQUIRED in production!\n'
        error_msg += '=' * 80 + '\n\n'

        if missing_vars:
            error_msg += '❌ Missing required environment variables:\n'
            for var in missing_vars:
                error_msg += f'  - {var}\n'
            error_msg += '\n'

        if invalid_vars:
            error_msg += '❌ Invalid environment variables:\n'
            for var in invalid_vars:
                error_msg += f'  - {var}\n'
            error_msg += '\n'

        error_msg += 'Please set the following environment variables:\n'
        error_msg += '  - ADMIN_USERNAME: Admin username for login (e.g., admin)\n'
        error_msg += '  - ADMIN_PASSWORD: Admin password for login (e.g., admin123)\n'
        error_msg += '  - JWT_SECRET_KEY: Secret key for JWT token (must be unique and secure)\n'
        error_msg += '\n📝 Example (docker-compose.yml):\n'
        error_msg += '  environment:\n'
        error_msg += '    - ADMIN_USERNAME=admin\n'
        error_msg += '    - ADMIN_PASSWORD=your-secure-password\n'
        error_msg += '    - JWT_SECRET_KEY=your-unique-secret-key-here\n'
        error_msg += '\n📝 Example (Docker run):\n'
        error_msg += '  docker run -e ADMIN_USERNAME=admin \\\n'
        error_msg += '             -e ADMIN_PASSWORD=your-secure-password \\\n'
        error_msg += '             -e JWT_SECRET_KEY=your-unique-secret-key-here \\\n'
        error_msg += '             ...\n'
        error_msg += '\n💡 For local development without auth:\n'
        error_msg += '  export SKIP_AUTH_CHECK=true\n'
        error_msg += '=' * 80 + '\n'

        print(error_msg, flush=True)
        import sys
        sys.exit(1)
