"""Runtime receipts stay outside configuration revisions and exported settings."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile
import threading

from backend.routers.rule_sync_rules import digest

_guard = threading.Lock()
_locks = {}


def directory():
    from backend.common.config import DATA_DIR
    return Path(DATA_DIR) / 'router-rule-sync'


def key(profile_id, router_id):
    return digest([profile_id, router_id])


def read(profile_id, router_id):
    path = directory() / (key(profile_id, router_id) + '.json')
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (ValueError, OSError):
        return {'status': 'paused', 'message': '同步记录无法读取，请手动预览发布后重新建立基准'}
    return value if isinstance(value, dict) else {}


def write(profile_id, router_id, value):
    directory().mkdir(parents=True, exist_ok=True)
    path = directory() / (key(profile_id, router_id) + '.json')
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory(), delete=False) as handle:
            name = handle.name
            json.dump(value, handle, ensure_ascii=False, separators=(',', ':'))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


@contextmanager
def job_lock(profile_id, router_id, connection=None):
    """The HTTP 'sync now' job and scheduler share this nonblocking process lock."""
    import fcntl
    token = digest([connection.get('type'), str(connection.get('endpoint') or '').rstrip('/')]) if connection else key(profile_id, router_id)
    with _guard:
        lock = _locks.setdefault(token, threading.Lock())
    acquired = lock.acquire(blocking=False)
    if not acquired:
        yield False
        return
    handle = None
    try:
        directory().mkdir(parents=True, exist_ok=True)
        handle = open(directory() / (token + '.lock'), 'a')
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True
    finally:
        if handle:
            handle.close()
        lock.release()


def public(value, enabled=False):
    baseline = value.get('baseline') or {}
    rules = value.get('rules') or baseline.get('rules') or {}
    return {'enabled': enabled, 'status': value.get('status', 'waiting'),
            'message': value.get('message', '请先手动预览并成功发布一次，建立同步基准'),
            'checked_at': value.get('checked_at'), 'synced_at': value.get('synced_at'),
            'updated_at': value.get('updated_at'), 'added': value.get('added', 0), 'removed': value.get('removed', 0),
            'ready': bool(baseline), 'log': value.get('log', [])[-20:],
            'rules': [{'id': rule_id, **{key: record.get(key) for key in
                       ('name', 'source_type', 'ip_count', 'domain_count', 'ignored_count', 'invalid_count')},
                       'version': record['version'][:12],
                       'synced_version': (baseline.get('rules', {}).get(rule_id, {}).get('version') or '')[:12]}
                      for rule_id, record in rules.items()]}
