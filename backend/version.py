"""版本信息模块"""

# 此版本号在 Docker 构建时自动替换为 Git tag
# 本地开发时显示 dev
__version__ = "dev"

def get_version_info():
    """获取版本信息"""
    return {
        'version': __version__
    }
