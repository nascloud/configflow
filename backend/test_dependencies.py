"""内置 Sub-Store 的版本选择、模式判定与在线更新流程。"""
import hashlib
import os
from types import SimpleNamespace

import pytest

from backend.utils import dependencies as deps


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    builtin = tmp_path / 'opt'
    runtime = tmp_path / 'data' / 'sub-store' / 'runtime'
    builtin.mkdir()
    (builtin / deps.SUB_STORE_ASSET).write_text('builtin')
    (builtin / 'VERSION').write_text('2.40.0\n')
    monkeypatch.setattr(deps, 'SUB_STORE_BUILTIN_DIR', str(builtin))
    monkeypatch.setattr(deps, 'sub_store_runtime_dir', lambda: str(runtime))
    monkeypatch.delenv('SUB_STORE_URL', raising=False)
    monkeypatch.setattr('backend.common.config.get_system_config', lambda: {'system_config': {}})
    deps._update_state.update(state='idle', message='', target_version='', finished_at=0.0)
    return builtin, runtime


def _write_runtime(runtime, version):
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / deps.SUB_STORE_ASSET).write_text('runtime')
    (runtime / 'VERSION').write_text(version + '\n')


def test_version_compare():
    assert deps.is_newer('2.42.3', '2.42.2')
    assert deps.is_newer('v2.43.0', '2.42.10')
    assert not deps.is_newer('2.42.3', '2.42.3')
    assert not deps.is_newer('bad', '2.42.3')
    assert deps.is_newer('2.42.3', '')


def test_active_bundle_prefers_newer_runtime(dirs):
    builtin, runtime = dirs
    assert deps.active_bundle() == (str(builtin), '2.40.0')
    _write_runtime(runtime, '2.41.0')
    assert deps.active_bundle() == (str(runtime), '2.41.0')


def test_image_upgrade_overrides_older_online_update(dirs):
    builtin, runtime = dirs
    _write_runtime(runtime, '2.39.9')
    assert deps.active_bundle() == (str(builtin), '2.40.0')


def test_mode_builtin_by_default(dirs):
    assert deps.sub_store_mode() == ('builtin', 'builtin')


def test_mode_external_via_env_or_settings(dirs, monkeypatch):
    monkeypatch.setenv('SUB_STORE_URL', 'http://127.0.0.1:3001')
    assert deps.sub_store_mode() == ('builtin', 'builtin')
    monkeypatch.setenv('SUB_STORE_URL', 'http://sub-store:3001')
    assert deps.sub_store_mode() == ('external', 'env')
    monkeypatch.setattr('backend.common.config.get_system_config',
                        lambda: {'system_config': {'sub_store_url': 'http://10.0.0.2:3001'}})
    assert deps.sub_store_mode() == ('external', 'settings')


def test_update_refused_for_external(dirs, monkeypatch):
    monkeypatch.setenv('SUB_STORE_URL', 'http://sub-store:3001')
    ok, message = deps.start_sub_store_update()
    assert not ok and '外部' in message


def _release(content, version='2.41.0'):
    return {'version': version, 'download_url': 'https://github.com/x/sub-store.bundle.js',
            'sha256': hashlib.sha256(content).hexdigest(), 'published_at': '', 'release_url': '',
            'notes': '', 'size': len(content)}


class _FakeResp:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size):
        yield self.content


def _patch_io(monkeypatch, content, running_versions):
    monkeypatch.setattr(deps, '_MIN_BUNDLE_SIZE', 1)
    monkeypatch.setattr(deps.requests, 'get', lambda *a, **k: _FakeResp(content))
    monkeypatch.setattr(deps.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='', stderr=''))
    restarts = []
    monkeypatch.setattr(deps, '_restart_sub_store', lambda: restarts.append(1))
    versions = iter(running_versions)
    monkeypatch.setattr(deps, '_wait_for_version', lambda expected, timeout=40: next(versions))
    return restarts


def test_update_success_writes_runtime_bundle(dirs, monkeypatch):
    _builtin, runtime = dirs
    content = b'console.log("sub-store")'
    restarts = _patch_io(monkeypatch, content, ['2.41.0'])
    deps._update_lock.acquire()
    deps._run_update(_release(content))

    assert deps.get_update_state()['state'] == 'success'
    assert (runtime / deps.SUB_STORE_ASSET).read_bytes() == content
    assert (runtime / 'VERSION').read_text().strip() == '2.41.0'
    assert restarts == [1]
    assert not deps._update_lock.locked()


def test_update_rolls_back_when_new_version_does_not_start(dirs, monkeypatch):
    builtin, runtime = dirs
    _write_runtime(runtime, '2.40.5')
    content = b'broken bundle'
    restarts = _patch_io(monkeypatch, content, ['', '2.40.5'])
    deps._update_lock.acquire()
    deps._run_update(_release(content))

    state = deps.get_update_state()
    assert state['state'] == 'failed' and '回滚' in state['message']
    assert (runtime / 'VERSION').read_text().strip() == '2.40.5'
    assert (runtime / deps.SUB_STORE_ASSET).read_text() == 'runtime'
    assert len(restarts) == 2
    assert not os.path.exists(str(runtime) + '.bak')


def test_update_rejects_checksum_mismatch(dirs, monkeypatch):
    _builtin, runtime = dirs
    restarts = _patch_io(monkeypatch, b'tampered', ['2.41.0'])
    release = _release(b'original')
    deps._update_lock.acquire()
    deps._run_update(release)

    state = deps.get_update_state()
    assert state['state'] == 'failed' and 'SHA256' in state['message']
    assert not (runtime / deps.SUB_STORE_ASSET).exists()
    assert restarts == []
