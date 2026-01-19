"""资源文件路径处理模块
处理 PyInstaller 打包后的资源文件路径问题
"""
import os
import sys


def get_resource_path(relative_path):
    """获取资源文件的绝对路径

    在开发环境和 PyInstaller 打包后都能正确找到资源文件

    Args:
        relative_path: 相对于项目根目录的路径，例如 'backend/VERSION'

    Returns:
        str: 资源文件的绝对路径
    """
    # 如果是 PyInstaller 打包后的环境
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # PyInstaller 创建的临时目录
        base_path = sys._MEIPASS
    else:
        # 开发环境：项目根目录
        # __file__ 是 backend/common/resource.py
        # 向上两级到项目根目录
        base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    # 拼接完整路径
    full_path = os.path.join(base_path, relative_path)

    return full_path


def get_backend_resource(filename):
    """获取 backend 目录下的资源文件路径

    Args:
        filename: backend 目录下的文件名，例如 'VERSION' 或 'config_template.json'

    Returns:
        str: 资源文件的绝对路径
    """
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        # PyInstaller 打包后，backend 目录下的资源文件在 _MEIPASS/backend/ 下
        return os.path.join(sys._MEIPASS, 'backend', filename)
    else:
        # 开发环境
        backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(backend_dir, filename)


def resource_exists(relative_path):
    """检查资源文件是否存在

    Args:
        relative_path: 相对路径

    Returns:
        bool: 文件是否存在
    """
    path = get_resource_path(relative_path)
    return os.path.exists(path) and os.path.isfile(path)
