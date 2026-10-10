"""节点延迟测试

从 ConfigFlow 服务端对节点的 server:port 发起 TCP 握手计时。
测的是「服务端 → 节点入口」的连通与握手延迟，不经过代理协议本身，
用于快速发现失联节点与粗排延迟；节点与订阅是共享资源，结果也全局保存最近若干次历史。
"""
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.common.config import get_repository, get_shared_config
from backend.common.config_repository import ProfileRepositoryError
from backend.utils.logger import get_logger
from backend.utils.subscription_cache import load_subscription_cache

logger = get_logger(__name__)

LATENCY_FILE = 'node_latency.json'
HISTORY_SIZE = 14
CONNECT_TIMEOUT = 3.0
MAX_WORKERS = 32
MAX_NODES = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _endpoint_from_proxy_string(proxy_string: str) -> Optional[Tuple[str, int]]:
    """从节点字符串解析 server/port，只用本地解析，不请求 Sub-Store。"""
    if not proxy_string:
        return None
    try:
        from backend.utils.subscription_parser import parse_uri_list

        parsed = parse_uri_list(proxy_string)
        if parsed:
            item = parsed[0]
            server, port = item.get('server'), item.get('port')
            if server and port:
                return str(server), int(port)
    except Exception as exc:  # 解析器对异常格式会抛各种错误，逐个节点隔离
        logger.debug('解析节点字符串失败: %s', exc)
    try:
        from backend.converters.mihomo import _parse_structured_proxy_string

        structured = _parse_structured_proxy_string(proxy_string)
        if structured and structured.get('server') and structured.get('port'):
            return str(structured['server']), int(structured['port'])
    except Exception as exc:
        logger.debug('解析结构化节点失败: %s', exc)
    return None


def _endpoint_of(node: Dict[str, Any]) -> Optional[Tuple[str, int]]:
    server, port = node.get('server'), node.get('port')
    if server and port:
        try:
            return str(server), int(port)
        except (TypeError, ValueError):
            return None
    return _endpoint_from_proxy_string(node.get('proxy_string') or '')


def collect_candidates() -> Dict[str, Tuple[str, int]]:
    """节点名 → (server, port)。节点库优先，其次是已启用订阅的缓存节点。"""
    config = get_shared_config()
    candidates: Dict[str, Tuple[str, int]] = {}

    for node in config.get('nodes', []):
        if node.get('enabled') is False or not node.get('name'):
            continue
        endpoint = _endpoint_of(node)
        if endpoint:
            candidates.setdefault(node['name'], endpoint)

    for sub in config.get('subscriptions', []):
        if sub.get('enabled') is False:
            continue
        cache = load_subscription_cache(sub.get('id', '')) or {}
        for node in cache.get('nodes', []) or []:
            if not isinstance(node, dict) or not node.get('name'):
                continue
            endpoint = _endpoint_of(node)
            if endpoint:
                candidates.setdefault(node['name'], endpoint)

    return candidates


def tcp_latency(server: str, port: int, timeout: float = CONNECT_TIMEOUT) -> Optional[int]:
    """TCP 握手耗时（毫秒），失败或超时返回 None。"""
    started = time.perf_counter()
    try:
        with socket.create_connection((server, port), timeout=timeout):
            return max(1, round((time.perf_counter() - started) * 1000))
    except (OSError, ValueError):
        return None


def load_results() -> Dict[str, Any]:
    try:
        data = get_repository().read_shared_json(LATENCY_FILE)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, ProfileRepositoryError):
        return {}


def _save_results(results: Dict[str, Any]) -> None:
    try:
        get_repository().write_shared_json(LATENCY_FILE, results)
    except (OSError, ProfileRepositoryError) as exc:
        logger.error('写入节点延迟结果失败: %s', exc)


def run_tests(names: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """测试指定节点（为空则全部候选），合并进历史并返回本次结果。"""
    candidates = collect_candidates()
    wanted: List[str] = list(candidates) if names is None else [n for n in names if n in candidates]
    wanted = wanted[:MAX_NODES]

    measured: Dict[str, Optional[int]] = {}
    if wanted:
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(wanted))) as pool:
            for name, ms in zip(wanted, pool.map(lambda n: tcp_latency(*candidates[n]), wanted)):
                measured[name] = ms

    stamp = _now()
    stored = load_results()
    for name, ms in measured.items():
        entry = stored.get(name) if isinstance(stored.get(name), dict) else {}
        history = list(entry.get('history') or [])[-(HISTORY_SIZE - 1):]
        history.append(ms)
        stored[name] = {'latency': ms, 'tested_at': stamp, 'history': history}

    # 已不存在的节点不再保留历史
    for name in list(stored):
        if name not in candidates:
            stored.pop(name, None)

    _save_results(stored)
    return {
        'tested_at': stamp,
        'results': {name: stored[name] for name in measured},
        'missing': [n for n in (names or []) if n not in candidates],
    }
