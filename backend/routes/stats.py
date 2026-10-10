"""统计数据路由"""
from datetime import datetime, timedelta, timezone

from flask import jsonify
from backend.routes import Blueprint
from backend.common.config import get_config, get_repository, get_shared_config
from backend.common.config_repository import ProfileRepositoryError
from backend.common.profile_context import resolve_profile_id

stats_bp = Blueprint('stats', __name__, url_prefix='/api/stats')


def get_data_count(data_type):
    """获取各类数据的数量"""
    config = get_shared_config() if data_type in ('subscriptions', 'nodes') else get_config()

    if data_type == 'subscriptions':
        return len(config.get('subscriptions', []))
    elif data_type == 'nodes':
        return len(config.get('nodes', []))
    elif data_type == 'rules':
        # 规则存储在 rule_configs 字段中
        return len(config.get('rule_configs', []))
    elif data_type == 'proxy_groups':
        return len(config.get('proxy_groups', []))

    return 0


STATS_HISTORY_FILE = 'stats_history.json'
STATS_HISTORY_SIZE = 48
# 两次采样的最小间隔：总览每 30 秒刷新一次，按小时级粒度留存即可
STATS_SAMPLE_INTERVAL = timedelta(minutes=30)
STATS_KEYS = ('subscriptions', 'nodes', 'proxyGroups', 'rules')


def _record_history(counts):
    """按配置空间留存计数快照，返回各项的历史序列（含本次）。"""
    profile_id = resolve_profile_id()
    repository = get_repository()
    try:
        data = repository.read_profile_json(profile_id, STATS_HISTORY_FILE)
        samples = data.get('samples') if isinstance(data, dict) else None
        samples = samples if isinstance(samples, list) else []
    except (OSError, ValueError, ProfileRepositoryError):
        samples = []

    now = datetime.now(timezone.utc)
    last_at = None
    if samples:
        try:
            last_at = datetime.fromisoformat(str(samples[-1].get('at', '')).replace('Z', '+00:00'))
        except ValueError:
            last_at = None

    current = {'at': now.isoformat().replace('+00:00', 'Z'), **counts}
    if last_at is None or now - last_at >= STATS_SAMPLE_INTERVAL:
        samples = (samples + [current])[-STATS_HISTORY_SIZE:]
        try:
            repository.write_profile_json(profile_id, STATS_HISTORY_FILE, {'samples': samples})
        except (OSError, ProfileRepositoryError):
            pass
        series = samples
    else:
        # 未到采样间隔：历史不落盘，但末尾用最新值，保证曲线终点与卡片数字一致
        series = samples[:-1] + [current]

    return {key: [int(sample.get(key) or 0) for sample in series] for key in STATS_KEYS}


def _profile_revision():
    try:
        profile_id = resolve_profile_id()
        repository = get_repository()
        metadata = next((p for p in repository.list_profiles() if p.get('id') == profile_id), None)
        if metadata:
            return repository.config_revision(profile_id), metadata.get('updated_at')
    except (ProfileRepositoryError, TypeError, ValueError):
        pass
    return 0, None


@stats_bp.route('/overview', methods=['GET'])
def get_overview():
    """获取总览统计数据"""
    try:
        counts = {
            'subscriptions': get_data_count('subscriptions'),
            'nodes': get_data_count('nodes'),
            'proxyGroups': get_data_count('proxy_groups'),
            'rules': get_data_count('rules'),
        }
        history = _record_history(counts)
        revision, updated_at = _profile_revision()
        overview_data = {
            key: {'total': counts[key], 'history': history[key]}
            for key in STATS_KEYS
        }
        overview_data['profile'] = {'revision': revision, 'updated_at': updated_at}

        return jsonify({
            'success': True,
            'data': overview_data
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500
