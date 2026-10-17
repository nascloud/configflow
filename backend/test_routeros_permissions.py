"""REST permission failures include actionable guidance without masking diagnostics."""
import json

import pytest
import requests

from backend.routers.base import RouterError
from backend.routers.routeros import RouterOSDriver


@pytest.fixture
def driver():
    instance = RouterOSDriver('http://192.0.2.1', 'configflow', 'private-test-password')
    yield instance
    instance.close()


def respond(monkeypatch, driver, status, body):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    monkeypatch.setattr(driver.session, 'request', lambda *args, **kwargs: response)


@pytest.mark.parametrize('status', [400, 500])
def test_permission_failure_explains_group_setup_and_keeps_original_error(monkeypatch, driver, status):
    respond(monkeypatch, driver, status, {
        'error': status, 'message': 'Internal Server Error', 'detail': 'not enough permissions (9)',
    })
    with pytest.raises(RouterError) as caught:
        driver._get('/ip/route')
    message = str(caught.value)
    for detail in ('账号权限不足', 'System → Users', 'Groups', 'read、write、api、rest-api',
                   '重点检查 write', '重新生成发布预览', '/ip/route',
                   f'HTTP {status}', 'not enough permissions (9)'):
        assert detail in message
    assert 'private-test-password' not in message


@pytest.mark.parametrize('status, expected', [
    (401, ('认证失败', '用户名、密码', '来源地址', 'Users / Groups', 'api、rest-api')),
    (403, ('拒绝访问', 'HTTP/HTTPS 服务允许 REST API', 'System → Users', 'read、write、api、rest-api')),
])
def test_access_failures_offer_relevant_checks(monkeypatch, driver, status, expected):
    respond(monkeypatch, driver, status, {})
    with pytest.raises(RouterError) as caught:
        driver._get('/system/resource')
    assert all(part in str(caught.value) for part in expected)
    assert 'private-test-password' not in str(caught.value)


def test_unrelated_server_error_does_not_suggest_permission_changes(monkeypatch, driver):
    respond(monkeypatch, driver, 500, {'message': 'Internal Server Error', 'detail': 'routing process failed'})
    with pytest.raises(RouterError) as caught:
        driver._get('/ip/route')
    assert 'routing process failed' in str(caught.value)
    assert '账号权限不足' not in str(caught.value)
    assert 'Groups' not in str(caught.value)


def test_non_json_error_keeps_http_status(monkeypatch, driver):
    response = requests.Response()
    response.status_code = 502
    response._content = b'<html>Bad Gateway</html>'
    monkeypatch.setattr(driver.session, 'request', lambda *args, **kwargs: response)
    with pytest.raises(RouterError, match='RouterOS GET /ip/route 返回 HTTP 502'):
        driver._get('/ip/route')
