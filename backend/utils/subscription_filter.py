"""订阅级节点名正则筛选

缓存始终保存订阅原始节点；筛选只在下发、统计、聚合等消费环节按订阅配置的
regex_filter 生效，这样修改正则后无需重新拉取即可看到新结果。
"""
import re
from typing import Any, Iterable, List, Optional, Pattern

from backend.utils.logger import get_logger

logger = get_logger(__name__)


def validate_regex_filter(value: Any) -> Optional[str]:
    """校验 regex_filter 字段，合法返回 None，否则返回错误信息。"""
    if value is None:
        return None
    if not isinstance(value, str):
        return '节点筛选正则必须是字符串'
    if not value.strip():
        return None
    try:
        re.compile(value.strip())
    except re.error as exc:
        return f'节点筛选正则无效: {exc}'
    return None


def compile_subscription_filter(sub: Any) -> Optional[Pattern]:
    """编译订阅的筛选正则；未配置或非法时返回 None（即不筛选）。"""
    if not isinstance(sub, dict):
        return None
    pattern = sub.get('regex_filter')
    if not isinstance(pattern, str) or not pattern.strip():
        return None
    try:
        return re.compile(pattern.strip())
    except re.error as exc:
        logger.warning("订阅 '%s' 的节点筛选正则无效，已忽略: %s", sub.get('name'), exc)
        return None


def filter_subscription_items(sub: Any, items: Optional[Iterable[Any]]) -> List[Any]:
    """按订阅的 regex_filter 筛选节点 / proxy（均以 name 字段匹配）。"""
    items = list(items or [])
    regex = compile_subscription_filter(sub)
    if regex is None:
        return items
    return [item for item in items
            if isinstance(item, dict) and regex.search(str(item.get('name', '')))]
