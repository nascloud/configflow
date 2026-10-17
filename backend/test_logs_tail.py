"""Tail reads stay bounded without changing filtering or response sanitization."""
import pytest
from flask import Flask

from backend.common import config as config_module
from backend.common.config_repository import ProfileRepository
from backend.common.profile_context import install_profile_context
from backend.routes import logs


@pytest.fixture
def log_client(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path / 'repository')
    config_module.set_repository(repository)
    path = tmp_path / 'app.log'
    monkeypatch.setattr(logs, 'get_log_file_path', lambda: path)
    app = Flask(__name__)
    app.register_blueprint(logs.logs_bp)
    install_profile_context(app)
    yield app.test_client(), path, repository
    config_module.reset_config_context()


@pytest.mark.parametrize(('query', 'expected'), [
    ({'lines': 2}, ['INFO: needle four', 'ERROR: final']),
    ({'lines': 2, 'search': 'NEEDLE'}, ['INFO: needle four']),
    ({'lines': 2, 'level': 'warning'}, []),
    ({'lines': 3, 'level': 'warning'}, ['WARNING: three']),
    ({'lines': 2, 'search': 'needle', 'level': 'error'}, []),
    ({'lines': 0}, ['ERROR: old needle', 'INFO: two', 'WARNING: three', 'INFO: needle four', 'ERROR: final']),
    ({'lines': -2}, ['ERROR: old needle', 'INFO: two', 'WARNING: three', 'INFO: needle four', 'ERROR: final']),
])
def test_tail_precedes_filters_and_nonpositive_limits_return_all(log_client, query, expected):
    client, path, _ = log_client
    path.write_text('ERROR: old needle\nINFO: two\nWARNING: three\nINFO: needle four\nERROR: final')
    response = client.get('/api/logs/tail', query_string=query)
    assert response.status_code == 200
    assert response.get_json() == {
        'success': True, 'logs': expected, 'total_lines': 5,
        'filtered_lines': len(expected), 'log_file': str(path),
    }


@pytest.mark.parametrize(('query', 'count'), [({}, 100), ({'lines': 'invalid'}, 100), ({'lines': 10001}, 10000)])
def test_tail_default_invalid_limit_and_maximum(log_client, query, count):
    client, path, _ = log_client
    path.write_text(''.join(f'line {index}\n' for index in range(10003)))
    data = client.get('/api/logs/tail', query_string=query).get_json()
    assert data['total_lines'] == 10003
    assert data['filtered_lines'] == count
    assert data['logs'] == [f'line {index}' for index in range(10003 - count, 10003)]


def test_tail_counts_blank_lines_ignores_invalid_utf8_and_normalizes_newlines(log_client):
    client, path, _ = log_client
    path.write_bytes(b'first\r\n\nINFO: \xffvalid\rfinal')
    data = client.get('/api/logs/tail', query_string={'lines': 3}).get_json()
    assert data['total_lines'] == 4
    assert data['logs'] == ['', 'INFO: valid', 'final']


def test_tail_reads_full_count_without_reading_file_into_a_list(log_client, monkeypatch):
    client, path, _ = log_client
    path.touch()
    opened = []

    class IteratedFile:
        def __init__(self):
            self.count = 0
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def __iter__(self):
            for index in range(50000):
                self.count += 1
                yield f'INFO: generated {index}\n'

        def read(self, *_args):
            pytest.fail('a bounded tail must not read the entire log')

        def readlines(self, *_args):
            pytest.fail('a bounded tail must not materialize every line')

    def open_log(filename, mode, **kwargs):
        assert filename == path and mode == 'r'
        assert kwargs == {'encoding': 'utf-8', 'errors': 'ignore'}
        handle = IteratedFile()
        opened.append(handle)
        return handle

    monkeypatch.setattr(logs, 'open', open_log, raising=False)
    data = client.get('/api/logs/tail', query_string={'lines': 60}).get_json()
    assert data['success'] is True
    assert data['total_lines'] == 50000
    assert data['logs'] == [f'INFO: generated {index}' for index in range(49940, 50000)]
    assert len(opened) == 1 and opened[0].closed and opened[0].count == 50000


@pytest.mark.parametrize('present', [False, True])
def test_tail_missing_or_empty_file(log_client, present):
    client, path, _ = log_client
    if present:
        path.touch()
    data = client.get('/api/logs/tail', query_string={'lines': 60}).get_json()
    assert data['success'] is True and data['logs'] == [] and data['total_lines'] == 0
    if present:
        assert data['filtered_lines'] == 0
    else:
        assert data['message'] == f'日志文件不存在: {path}'


def test_tail_read_error_and_invalid_level_keep_existing_error_response(log_client, monkeypatch):
    client, path, _ = log_client
    path.write_text('INFO: one\n')
    response = client.get('/api/logs/tail', query_string={'level': '['})
    assert response.status_code == 500 and response.get_json()['success'] is False

    def fail_open(*_args, **_kwargs):
        raise OSError('synthetic read failure')

    monkeypatch.setattr(logs, 'open', fail_open, raising=False)
    response = client.get('/api/logs/tail')
    assert response.status_code == 500
    assert response.get_json() == {
        'success': False, 'error': 'synthetic read failure', 'logs': [], 'total_lines': 0,
    }


def test_tail_response_still_redacts_repository_credentials(log_client):
    client, path, repository = log_client
    secret = 'log-tail-private-capability'
    repository.save_system({'system_config': {'rule_proxy_token': secret}})
    path.write_text(f'INFO: safe\nERROR: url=https://invalid/{secret}\n')
    response = client.get('/api/logs/tail', query_string={'lines': 2})
    assert response.get_json()['logs'] == ['INFO: safe', '[REDACTED]']
    assert secret not in response.get_data(as_text=True)
    assert secret in path.read_text()
