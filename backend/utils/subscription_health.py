"""订阅健康：拉取历史与流量信息

- 每次拉取（成功 / 失败 / 回落缓存）记一条，保留最近 HISTORY_SIZE 次，供界面画健康条。
- 流量信息来自订阅服务商的 `subscription-userinfo` 响应头
  （upload / download / total / expire），只在拉取成功后尽力读取，读不到不影响拉取结果。
"""
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from backend.common.config import get_repository
from backend.common.config_repository import ProfileRepositoryError
from backend.utils.logger import get_logger

logger = get_logger(__name__)

HEALTH_FILE = 'subscription_health.json'
HISTORY_SIZE = 24
USERINFO_TIMEOUT = 6
# 大多数机场只对 Clash 系 UA 返回流量头
USERINFO_USER_AGENT = 'clash-verge/v2.0.0'
_USERINFO_FIELDS = ('upload', 'download', 'total', 'expire')


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def parse_userinfo(header: str) -> Optional[Dict[str, int]]:
    """解析 `upload=1; download=2; total=3; expire=4`，字段缺失时不返回该键。"""
    if not header:
        return None
    info: Dict[str, int] = {}
    for key, value in re.findall(r'(\w+)\s*=\s*([0-9.eE+]+)', header):
        key = key.lower()
        if key in _USERINFO_FIELDS:
            try:
                info[key] = int(float(value))
            except ValueError:
                continue
    return info or None


def fetch_userinfo(url: str) -> Optional[Dict[str, int]]:
    """只读响应头，不下载订阅正文；任何失败都返回 None。"""
    if not isinstance(url, str) or not url.lower().startswith(('http://', 'https://')):
        return None
    try:
        with requests.get(
            url,
            headers={'User-Agent': USERINFO_USER_AGENT},
            timeout=USERINFO_TIMEOUT,
            stream=True,
            allow_redirects=True,
        ) as resp:
            if resp.status_code >= 400:
                return None
            return parse_userinfo(resp.headers.get('subscription-userinfo', ''))
    except requests.RequestException as exc:
        logger.info('读取订阅流量信息失败: %s', type(exc).__name__)
        return None


def load_health() -> Dict[str, Any]:
    try:
        data = get_repository().read_shared_json(HEALTH_FILE)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, ProfileRepositoryError):
        return {}


def _save_health(data: Dict[str, Any]) -> None:
    try:
        get_repository().write_shared_json(HEALTH_FILE, data)
    except (OSError, ProfileRepositoryError) as exc:
        logger.error('写入订阅健康记录失败: %s', exc)


def record_fetch(
    sub_id: str,
    *,
    ok: bool,
    count: int = 0,
    from_cache: bool = False,
    traffic: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """追加一条拉取记录；traffic 非空时同时更新流量信息。返回该订阅的最新健康数据。"""
    if not sub_id:
        return {}
    data = load_health()
    entry = data.get(sub_id) if isinstance(data.get(sub_id), dict) else {}
    history = list(entry.get('history') or [])[-(HISTORY_SIZE - 1):]
    history.append({
        'at': _now(),
        # ok: 直接拉取成功；cache: 拉取失败但用了本地缓存；fail: 彻底失败
        'status': 'ok' if ok and not from_cache else ('cache' if from_cache else 'fail'),
        'count': int(count or 0),
    })
    entry['history'] = history
    if traffic:
        entry['traffic'] = traffic
        entry['traffic_at'] = _now()
    data[sub_id] = entry
    _save_health(data)
    return entry


def forget(sub_id: str) -> None:
    data = load_health()
    if data.pop(sub_id, None) is not None:
        _save_health(data)
