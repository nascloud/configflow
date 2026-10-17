"""Small status snapshots never retain full traffic histories or stale files."""
from collections import OrderedDict
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.agents import traffic_store
from backend.agents.traffic_store import TrafficStore


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(traffic_store, '_STATUS_CACHE', OrderedDict())


def write_data(store, agent='a', status=None):
    data = {'status': status if status is not None else {'value': 'running', 'at': 'now', 'mihomo_version': '1'},
            'days': {'today': {'hosts': {'synthetic.example': {'large': list(range(100))}}}}}
    store._write_json(agent, data)
    return data


def track_reads(store, monkeypatch):
    calls = []
    original = store._read_json

    def read(agent):
        calls.append(agent)
        return original(agent)

    monkeypatch.setattr(store, '_read_json', read)
    return calls


def test_status_hit_reuses_only_small_independent_metadata(tmp_path, monkeypatch):
    store = TrafficStore(tmp_path)
    write_data(store)
    calls = track_reads(store, monkeypatch)
    first = store.load_status('a')
    first['value'] = 'mutated'
    assert store.load_status('a')['value'] == 'running'
    assert calls == ['a']
    assert list(traffic_store._STATUS_CACHE.values())[0][1] == {
        'value': 'running', 'at': 'now', 'mihomo_version': '1'}


@pytest.mark.parametrize('atomic', [False, True])
def test_external_same_size_same_mtime_change_invalidates(tmp_path, atomic):
    store = TrafficStore(tmp_path)
    data = write_data(store)
    assert store.load_status('a')['value'] == 'running'
    path = Path(store._path('a'))
    previous = path.stat()
    data['status']['value'] = 'unknown'
    destination = path.with_suffix('.new') if atomic else path
    destination.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
    os.utime(destination, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    if atomic:
        os.replace(destination, path)
    assert path.stat().st_size == previous.st_size
    assert path.stat().st_mtime_ns == previous.st_mtime_ns
    assert store.load_status('a')['value'] == 'unknown'


def test_replacement_during_read_does_not_label_old_status_with_new_signature(tmp_path, monkeypatch):
    store = TrafficStore(tmp_path)
    write_data(store)
    original = store._read_json
    replaced = False

    def read(agent):
        nonlocal replaced
        data = original(agent)
        if not replaced:
            replaced = True
            write_data(store, status={'value': 'unreachable'})
        return data

    monkeypatch.setattr(store, '_read_json', read)
    assert store.load_status('a')['value'] == 'running'
    assert not traffic_store._STATUS_CACHE
    assert store.load_status('a')['value'] == 'unreachable'


def test_report_and_delete_invalidate_status(tmp_path):
    store = TrafficStore(tmp_path)
    store.add_report('a', {'status': 'running', 'items': []})
    assert store.load_status('a')['value'] == 'running'
    store.add_report('a', {'status': 'unreachable', 'items': []})
    assert not traffic_store._STATUS_CACHE
    assert store.load_status('a')['value'] == 'unreachable'
    store.delete('a')
    assert not traffic_store._STATUS_CACHE
    assert store.load_status('a') is None


@pytest.mark.parametrize('content', [None, '{', '[]', '{"status":{"value":"running"},"days":[]}'])
def test_read_errors_and_invalid_documents_are_not_cached(tmp_path, monkeypatch, content):
    store = TrafficStore(tmp_path)
    if content is not None:
        Path(store._path('a')).write_text(content)
    calls = track_reads(store, monkeypatch)
    assert store.load_status('a') is None
    assert store.load_status('a') is None
    assert calls == ['a', 'a']
    assert not traffic_store._STATUS_CACHE


@pytest.mark.parametrize('status', [
    {'value': 'x' * 1025},
    {'value': '界' * 400},
    {'value': {'nested': []}},
    {'value': 'running', 'unexpected': 'extra'},
])
def test_oversized_or_unexpected_status_is_returned_without_caching(tmp_path, monkeypatch, status):
    store = TrafficStore(tmp_path)
    write_data(store, status=status)
    calls = track_reads(store, monkeypatch)
    assert store.load_status('a') == status
    assert store.load_status('a') == status
    assert calls == ['a', 'a']
    assert not traffic_store._STATUS_CACHE


def test_status_cache_is_lru_bounded_and_scoped_by_path(tmp_path, monkeypatch):
    monkeypatch.setattr(traffic_store, '_STATUS_CACHE_LIMIT', 2)
    first, second = TrafficStore(tmp_path / 'one'), TrafficStore(tmp_path / 'two')
    write_data(first, 'a', {'value': 'running'})
    write_data(second, 'a', {'value': 'unreachable'})
    write_data(first, 'b', {'value': 'no_controller'})
    assert first.load_status('a')['value'] == 'running'
    assert second.load_status('a')['value'] == 'unreachable'
    first.load_status('a')
    first.load_status('b')
    assert len(traffic_store._STATUS_CACHE) == 2
    assert str(Path(second._path('a')).absolute()) not in traffic_store._STATUS_CACHE


def test_windows_disables_stat_cache(monkeypatch):
    def unexpected_stat(path):
        raise AssertionError('must not use Windows ctime')

    monkeypatch.setattr(traffic_store, 'os', SimpleNamespace(name='nt', stat=unexpected_stat))
    assert traffic_store._status_signature('unused') is None
