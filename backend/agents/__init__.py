"""Agent 管理模块"""
from .manager import AgentManager
from .config_generator import generate_agent_config
from .install_script import generate_lightweight_install_script  # Shell 版本
from .go_install_script import generate_go_agent_install_script  # Go 版本（默认）

__all__ = [
    'AgentManager',
    'generate_agent_config',
    'generate_lightweight_install_script',  # Shell 版本
    'generate_go_agent_install_script'       # Go 版本
]
