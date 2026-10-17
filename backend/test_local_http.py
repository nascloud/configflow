"""Internal rule HTTP callbacks preserve auth without occupying another worker."""
import gc
from concurrent.futures import ThreadPoolExecutor
import time
import weakref

from flask import Flask, Response, g, jsonify, request, stream_with_context
import pytest
import requests

from backend.common import auth, config
from backend.common.config_repository import ProfileRepository
from backend.common.internal_call import INTERNAL_CALL_HEADER, INTERNAL_CALL_TOKEN
from backend.common.profile_context import install_profile_context
from backend.routes.rule_library import rule_library_bp
from backend.test_http_server import running_server
from backend.utils import local_http
from backend.utils.rule_fetch import request_rule


@pytest.fixture
def local_app(tmp_path, monkeypatch):
    repository = ProfileRepository(tmp_path)
    for name in ('alpha', 'beta'):
        repository.create_profile({'id': name, 'name': name})
    monkeypatch.setattr(config, '_repository', repository)
    monkeypatch.setattr(local_http, '_application', None)
    monkeypatch.setattr(auth, 'is_auth_enabled', lambda: True)
    app = Flask(__name__)
    install_profile_context(app)
    app.register_blueprint(rule_library_bp)
    local_http.register_application(app)

    @app.get('/api/profiles/<profile_id>/inspect')
    @auth.require_auth
    def inspect(profile_id):
        return jsonify(profile=config.get_config()['profile_id'],
                       outer=getattr(g, 'outer', None), cookie=request.headers.get('Cookie'),
                       internal=request.headers.get(INTERNAL_CALL_HEADER),
                       profile_header=request.headers.get('X-ConfigFlow-Profile'),
                       prefix=request.script_root, query=request.args.get('q'))

    @app.route('/api/fixture-rule', methods=['GET', 'HEAD'])
    def rule():
        return 'DOMAIN-SUFFIX,example.com\n'

    return app, repository


def auth_headers():
    return {'Authorization': f'Bearer {auth.generate_token("admin")}'}


def source(base='http://config.test'):
    return {'system_config': {'server_domain': base}}


def test_internal_request_does_not_inherit_auth_profile_or_mutable_context(local_app):
    app, repository = local_app
    url = 'http://config.test/api/profiles/beta/inspect'
    outer_headers = {**auth_headers(), 'Cookie': 'secret=outer', 'X-ConfigFlow-Profile': 'alpha',
                     INTERNAL_CALL_HEADER: INTERNAL_CALL_TOKEN}
    with app.test_request_context('/api/profiles/alpha/outer', headers=outer_headers):
        g.outer = 'private'
        token = config._CONFIG_CACHE.set(None)
        baseline_token = config._CONFIG_BASELINES.set(None)
        outer_config = config.get_config('alpha')
        outer_cache = config._CONFIG_CACHE.get()
        outer_baseline = config._CONFIG_BASELINES.get()
        with repository._document() as outer_document:
            pass
        repository._local.document = outer_document
        try:
            for headers in ({}, {'Authorization': 'Bearer invalid'}):
                response = request_rule(url, config_data=source(), headers=headers)
                assert response.status_code == 401
            response = request_rule(url, config_data=source(), headers=auth_headers())
            assert response.status_code == 200
            assert response.json() == {'profile': 'beta', 'outer': None, 'cookie': None,
                                       'internal': None, 'profile_header': None, 'prefix': '', 'query': None}
            assert config._CONFIG_CACHE.get() is outer_cache
            assert config._CONFIG_BASELINES.get() is outer_baseline
            assert config.get_config('alpha') is outer_config
            assert repository._local.document is outer_document
            assert g.outer == 'private'
        finally:
            repository._local.document = None
            config._CONFIG_CACHE.reset(token)
            config._CONFIG_BASELINES.reset(baseline_token)


def test_prefix_params_explicit_auth_and_head_are_preserved(local_app):
    response = request_rule('https://config.test/cf/api/profiles/beta/inspect',
                            config_data=source('https://config.test/cf'),
                            headers=auth_headers(), params={'q': '中文 & value'})
    assert response.status_code == 200
    assert response.json()['prefix'] == '/cf'
    assert response.json()['query'] == '中文 & value'
    head = request_rule('http://config.test/api/fixture-rule', method='head', config_data=source())
    assert head.status_code == 200 and head.content == b''
    assert head.headers['Content-Length'] == '26'


def test_stream_is_lazy_and_close_releases_request_state(local_app):
    app, _ = local_app
    chunks, closed, refs = [], [], []

    class Payload:
        pass

    @app.get('/api/stream')
    def streamed():
        g.payload = Payload()
        refs.append(weakref.ref(g.payload))

        @stream_with_context
        def body():
            try:
                for index in range(100):
                    chunks.append(index)
                    yield b'x' * 8192
            finally:
                closed.append(True)
        return Response(body(), mimetype='application/octet-stream')

    response = request_rule('http://config.test/api/stream', config_data=source(), stream=True)
    assert chunks == [0], 'WSGI should only consume the initial response chunk'
    assert next(response.iter_content(1024)) == b'x' * 1024
    response.close()
    gc.collect()
    assert chunks == [0] and closed == [True]
    assert refs[0]() is None


@pytest.mark.parametrize('fail', [False, True])
def test_stream_completion_and_failure_release_inner_context_without_altering_outer(local_app, fail):
    app, repository = local_app
    refs, closed = [], []

    class Payload:
        pass

    @app.get('/api/context-stream')
    def streamed():
        g.payload = Payload()
        refs.append(weakref.ref(g.payload))

        @stream_with_context
        def body():
            try:
                yield b'one'
                assert request.path == '/api/context-stream' and getattr(g, 'outer', None) is None
                if fail:
                    raise RuntimeError('stream failure')
                yield b'two'
            finally:
                closed.append(True)
        return Response(body())

    with app.test_request_context('/outer'):
        g.outer = 'private'
        document = object()
        repository._local.document = document
        try:
            response = request_rule('http://config.test/api/context-stream', config_data=source(), stream=True)
            if fail:
                with pytest.raises(RuntimeError, match='stream failure'):
                    list(response.iter_content(3))
            else:
                assert list(response.iter_content(3)) == [b'one', b'two']
            assert g.outer == 'private' and request.path == '/outer'
            assert repository._local.document is document
        finally:
            repository._local.document = None
    gc.collect()
    assert closed == [True] and refs[0]() is None


def test_registered_app_handles_callbacks_from_background_threads(local_app):
    with ThreadPoolExecutor(max_workers=1) as executor:
        response = executor.submit(request_rule, 'http://config.test/api/profiles/beta/inspect',
                                   config_data=source(), headers=auth_headers()).result(timeout=2)
    assert response.status_code == 200 and response.json()['profile'] == 'beta'


def test_same_origin_redirects_keep_auth_boundary_and_external_redirects_are_rejected(local_app):
    app, _ = local_app

    @app.get('/api/redirect')
    def redirect():
        return '', 302, {'Location': request.args['target']}

    response = request_rule('http://config.test/api/redirect?target=/api/fixture-rule', config_data=source())
    assert response.status_code == 200 and response.text == 'DOMAIN-SUFFIX,example.com\n'
    with pytest.raises(ValueError, match='leaves its origin'):
        request_rule('http://config.test/api/redirect?target=https://other.test/api/rules', config_data=source())


@pytest.mark.parametrize('url', ['http://other.test/api/rules', 'http://config.test/elsewhere',
                               'http://config.test/api/../elsewhere'])
def test_external_non_api_and_normalized_escape_use_existing_network_transport(local_app, monkeypatch, url):
    calls = []

    def network(target, **kwargs):
        calls.append(target)
        response = requests.Response()
        response.status_code = 200
        response._content = b'network'
        return response

    monkeypatch.setattr(requests, 'get', network)
    assert request_rule(url, config_data=source()).text == 'network'
    assert calls == [url]


def test_slow_local_response_reports_timeout_and_releases_stream(local_app):
    app, _ = local_app
    closed = []

    @app.get('/api/slow')
    def slow():
        time.sleep(.02)
        response = Response('late')
        response.call_on_close(lambda: closed.append(True))
        return response

    with pytest.raises(requests.ReadTimeout):
        request_rule('http://config.test/api/slow', config_data=source(), timeout=.001)
    assert closed == [True]


def test_real_rule_url_check_works_with_one_http_worker(local_app):
    app, _ = local_app
    with running_server(app, threads=1) as (call, server):
        response = call('POST', '/api/rule-library/test-single', headers=auth_headers(),
                        json={'url': f'http://127.0.0.1:{server.effective_port}/api/fixture-rule'})
    assert response.status_code == 200
    assert response.json() == {'available': True, 'status_code': 200, 'success': True}


def test_registration_does_not_keep_app_alive(monkeypatch):
    monkeypatch.setattr(local_http, '_application', None)
    app = Flask(__name__)
    reference = weakref.ref(app)
    local_http.register_application(app)
    del app
    gc.collect()
    assert reference() is None
    assert local_http.request_local('http://config.test/api/rules', method='get') is None
