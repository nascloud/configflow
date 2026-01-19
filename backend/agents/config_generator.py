"""Agent 配置生成器"""
import hashlib
from typing import Dict, Any
from backend.converters.mihomo import generate_mihomo_config
from backend.converters.mosdns import generate_mosdns_config


def generate_agent_config(config_data: Dict[str, Any], agent: Dict[str, Any]) -> Dict[str, Any]:
    """
    为指定 Agent 生成配置文件

    Args:
        config_data: 全局配置数据
        agent: Agent 信息

    Returns:
        Dict: 包含配置内容和 MD5 的字典
    """
    service_type = agent.get('service_type', 'mihomo')

    # 根据服务类型生成配置
    if service_type == 'mihomo':
        config_content = generate_mihomo_config(config_data)
    elif service_type == 'mosdns':
        config_content = generate_mosdns_config(config_data)
    else:
        raise ValueError(f"Unsupported service type: {service_type}")

    # 计算配置 MD5
    config_md5 = hashlib.md5(config_content.encode('utf-8')).hexdigest()

    return {
        'content': config_content,
        'md5': config_md5,
        'version': config_md5[:8],  # 使用 MD5 前 8 位作为版本号
        'service_type': service_type
    }
