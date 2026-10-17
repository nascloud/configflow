"""Manual route CRUD preserves advanced settings and concurrent operators' work."""
import copy

import pytest
import requests

from backend.routers.base import RouterConflictError, RouterError, RouterRollbackError
from backend.routers.routeros import UncertainWrite
from backend.routers.routeros_routes import mutate_route, read_routes
from backend.routers.route_editor import public_route
from backend.test_routeros_release import RESTResponse, ROUTES, StatefulRouterOS, driver_for


ROUTE = {
    '.id': '*10', 'dst-address': '203.0.113.0/24', 'gateway': '192.0.2.1',
    'routing-table': 'main', 'disabled': 'false', 'comment': 'office',
    'distance': '17', 'scope': '20', 'target-scope': '12', 'check-gateway': 'ping',
    'pref-src': '192.0.2.2', 'suppress-hw-offload': 'true',
    'dynamic': 'false', 'static': 'true', 'active': 'true', 'blackhole': 'false',
    'immediate-gw': '192.0.2.1%ether1', 'hw-offloaded': 'false',
}
VALUES = {'cidr': '198.51.100.0/24', 'gateway': '192.0.2.3', 'enabled': True, 'comment': 'manual'}


def setup(routes=None, session_type=StatefulRouterOS, **kwargs):
    session = session_type(routes=copy.deepcopy([ROUTE] if routes is None else routes), **kwargs)
    driver = driver_for(session)
    return driver, session


def original_values():
    return {'cidr': ROUTE['dst-address'], 'gateway': ROUTE['gateway'],
            'enabled': True, 'comment': ROUTE['comment']}


@pytest.mark.parametrize(('field', 'value', 'reason'), [
    ('dynamic', 'true', '动态'), ('connect', 'true', '直连'), ('static', 'false', '非静态'),
    ('ecmp', 'true', 'ECMP'), ('blackhole', 'true', '特殊'), ('invalid', 'true', '无效'),
    ('routing-table', 'backup', 'main'), ('dst-address', '2001:db8::/32', 'IPv4'),
    ('gateway', 'ether1', '下一跳'), ('gateway', '192.0.2.1%ether1', '下一跳'),
    ('gateway', '192.0.2.1,192.0.2.2', '下一跳'), ('gateway', '2001:db8::1', '下一跳'),
    ('vrf-interface', 'vrf1', 'VRF'), ('future-route-policy', 'special', '无法安全恢复'),
    ('disabled', 'maybe', '无法识别'), ('comment', {'unexpected': 'object'}, '格式'),
    ('.id', '*not-an-id', 'ID'),
])
def test_readonly_routes_remain_visible_and_cannot_be_mutated(field, value, reason):
    driver, session = setup([{**ROUTE, field: value}])
    [entry] = read_routes(driver)
    assert not entry['editable'] and reason in entry['reason']
    for action in ('update', 'delete'):
        with pytest.raises(RouterConflictError):
            mutate_route(driver, action, entry, VALUES if action == 'update' else None)
    assert session.writes == []


def test_duplicate_ids_are_readonly():
    driver, session = setup([ROUTE, {**ROUTE, 'dst-address': '192.0.2.0/24'}])
    entries = read_routes(driver)
    assert all(not entry['editable'] and 'ID 不唯一' in entry['reason'] for entry in entries)
    with pytest.raises(RouterConflictError):
        mutate_route(driver, 'delete', entries[0], None)
    assert not session.writes


def test_snapshot_excludes_runtime_state_but_keeps_editability_and_advanced_config():
    driver, session = setup()
    [before] = read_routes(driver)
    assert before['editable'] and not before['managed'] and before['reason'] == ''
    assert all(key not in before['snapshot'] for key in ('active', 'immediate-gw', 'hw-offloaded'))
    assert all(key in before['snapshot'] for key in ('dynamic', 'static', 'blackhole', 'distance', 'scope'))
    session.rows[ROUTES][0].update({'active': 'false', 'immediate-gw': '', 'hw-offloaded': 'true'})
    assert read_routes(driver) == [before]
    result = mutate_route(driver, 'update', before, {**original_values(), 'comment': 'new note'})
    assert result['entry']['comment'] == 'new note'


@pytest.mark.parametrize('comment', [None, 'office'])
def test_real_routeros_static_route_shape_is_editable_and_inactive_does_not_change_revision(comment):
    row = {
        '.id': '*10', 'active': 'true', 'inactive': 'false', 'static': 'true',
        'dynamic': 'false', 'disabled': 'false', 'distance': '1',
        'dst-address': '203.0.113.0/24', 'gateway': '192.0.2.1',
        'immediate-gw': '192.0.2.1%ether1', 'routing-table': 'main',
        'scope': '30', 'target-scope': '10',
    }
    if comment is not None:
        row['comment'] = comment
    driver, session = setup([row])
    [before] = read_routes(driver)
    assert before['editable'] and not before['reason']
    assert 'active' not in before['snapshot'] and 'inactive' not in before['snapshot']
    revision = public_route(before)['revision']
    session.rows[ROUTES][0].update({'active': 'false', 'inactive': 'true', 'immediate-gw': ''})
    [after] = read_routes(driver)
    assert after == before and public_route(after)['revision'] == revision
    result = mutate_route(driver, 'update', before, {**VALUES, 'comment': 'updated'})
    assert result['changed'] and result['entry']['editable']


def test_add_keeps_user_comment_and_does_not_automatically_manage_route():
    driver, session = setup([])
    result = mutate_route(driver, 'add', None, VALUES)
    entry = result['entry']
    assert result['changed'] and entry['cidr'] == VALUES['cidr']
    assert entry['comment'] == 'manual' and not entry['managed'] and entry['editable']
    assert session.writes == [('PUT', ROUTES, {
        'dst-address': VALUES['cidr'], 'gateway': VALUES['gateway'],
        'disabled': 'false', 'comment': 'manual', 'routing-table': 'main'})]
    result = mutate_route(driver, 'update', entry, {**VALUES, 'comment': 'cf-user-note'})
    assert result['entry']['managed'] and result['entry']['comment'] == 'cf-user-note'


def test_update_changes_only_four_fields_and_preserves_advanced_settings():
    driver, session = setup()
    [entry] = read_routes(driver)
    result = mutate_route(driver, 'update', entry, {**VALUES, 'enabled': False})
    assert result['changed'] and not result['entry']['enabled']
    assert session.writes == [('PATCH', ROUTES + '/*10', {
        'dst-address': VALUES['cidr'], 'gateway': VALUES['gateway'], 'disabled': 'true', 'comment': 'manual'})]
    changed = session.rows[ROUTES][0]
    for key in ('distance', 'scope', 'target-scope', 'check-gateway', 'pref-src', 'suppress-hw-offload'):
        assert changed[key] == ROUTE[key]


def test_noop_update_makes_no_write():
    driver, session = setup()
    [entry] = read_routes(driver)
    assert mutate_route(driver, 'update', entry, original_values()) == {'entry': entry, 'changed': False}
    assert not session.writes


def test_delete_removes_exact_selected_route():
    other = {**ROUTE, '.id': '*11', 'dst-address': '198.51.100.0/24'}
    driver, session = setup([ROUTE, other])
    entry = read_routes(driver)[0]
    assert mutate_route(driver, 'delete', entry, None) == {'entry': None, 'changed': True}
    assert session.rows[ROUTES] == [other]
    assert session.writes == [('DELETE', ROUTES + '/*10', None)]


@pytest.mark.parametrize('action', ['add', 'update'])
@pytest.mark.parametrize('disabled', ['true', 'false'])
def test_same_main_destination_is_rejected_even_when_disabled(action, disabled):
    duplicate = {**ROUTE, '.id': '*11', 'dst-address': VALUES['cidr'], 'disabled': disabled}
    driver, session = setup([ROUTE, duplicate])
    expected = read_routes(driver)[0] if action == 'update' else None
    with pytest.raises(RouterConflictError, match='已存在目标'):
        mutate_route(driver, action, expected, VALUES)
    assert not session.writes


def test_same_destination_in_another_table_does_not_block_main_add():
    driver, _ = setup([{**ROUTE, 'dst-address': VALUES['cidr'], 'routing-table': 'backup'}])
    assert mutate_route(driver, 'add', None, VALUES)['changed']


@pytest.mark.parametrize('change', ['comment', 'distance', 'dynamic', 'id', 'expected_identity'])
@pytest.mark.parametrize('action', ['update', 'delete'])
def test_stale_snapshot_or_identity_stops_before_writing(change, action):
    driver, session = setup()
    [entry] = read_routes(driver)
    if change == 'expected_identity':
        entry['cidr'] = '198.51.100.0/24'
    else:
        field, value = { 'comment': ('comment', 'external'), 'distance': ('distance', '99'),
                         'dynamic': ('dynamic', 'true'), 'id': ('.id', '*12')}[change]
        session.rows[ROUTES][0][field] = value
    with pytest.raises(RouterConflictError, match='刷新'):
        mutate_route(driver, action, entry, VALUES if action == 'update' else None)
    assert not session.writes


@pytest.mark.parametrize('action', ['update', 'delete'])
def test_lost_response_with_known_id_restores_exact_previous_configuration(action):
    method = 'PATCH' if action == 'update' else 'DELETE'
    driver, session = setup(timeout_after=(method, ROUTES, 1))
    [entry] = read_routes(driver)
    with pytest.raises(UncertainWrite):
        mutate_route(driver, action, entry, VALUES if action == 'update' else None)
    [restored] = read_routes(driver)
    for key in ('cidr', 'gateway', 'enabled', 'comment', 'table'):
        assert restored[key] == entry[key]
    for key in ('distance', 'scope', 'target-scope', 'check-gateway', 'pref-src', 'suppress-hw-offload'):
        assert restored['snapshot'][key] == ROUTE[key]
    if action == 'delete':
        assert session.writes[-1][0] == 'PUT'
        assert 'static' not in session.writes[-1][2] and 'active' not in session.writes[-1][2]
    else:
        assert session.writes[-1][0] == 'PATCH'
        assert set(session.writes[-1][2]) == {'dst-address', 'gateway', 'disabled', 'comment'}


def test_lost_create_response_does_not_guess_ownership_and_delete_matching_candidate():
    driver, session = setup([], timeout_after=('PUT', ROUTES, 1))
    with pytest.raises(RouterRollbackError, match='没有可信对象 ID'):
        mutate_route(driver, 'add', None, VALUES)
    assert len(session.rows[ROUTES]) == 1
    assert [write[0] for write in session.writes] == ['PUT']


@pytest.mark.parametrize('action', ['add', 'update', 'delete'])
def test_rejected_write_leaves_unchanged_state_without_compensation(action):
    method = {'add': 'PUT', 'update': 'PATCH', 'delete': 'DELETE'}[action]
    driver, session = setup(fail=(method, ROUTES, 1))
    expected = read_routes(driver)[0] if action != 'add' else None
    with pytest.raises(RouterError) as caught:
        mutate_route(driver, action, expected, VALUES if action != 'delete' else None)
    assert not isinstance(caught.value, RouterRollbackError)
    assert session.rows[ROUTES] == [ROUTE] and len(session.writes) == 1


class ReadFailureSession(StatefulRouterOS):
    fail_reads = 1

    def _written_response(self, method, collection, response):
        result = super()._written_response(method, collection, response)
        if len(self.writes) == 1:
            self.remaining_read_failures = self.fail_reads
        return result

    def request(self, method, url, **kwargs):
        if method == 'GET' and getattr(self, 'remaining_read_failures', 0):
            self.remaining_read_failures -= 1
            return RESTResponse({'message': 'Temporary read failure'}, 503)
        return super().request(method, url, **kwargs)


@pytest.mark.parametrize('action', ['add', 'update', 'delete'])
def test_transient_readback_failure_compensates_after_fresh_identity_check(action):
    driver, session = setup(session_type=ReadFailureSession)
    expected = read_routes(driver)[0] if action != 'add' else None
    with pytest.raises(RouterError) as caught:
        mutate_route(driver, action, expected, VALUES if action != 'delete' else None)
    assert not isinstance(caught.value, RouterRollbackError)
    [restored] = read_routes(driver)
    assert restored['cidr'] == ROUTE['dst-address'] and restored['comment'] == ROUTE['comment']
    assert len(session.writes) == 2


def test_persistent_read_failure_reports_unverified_state_without_blind_patch():
    class UnreadableSession(ReadFailureSession):
        fail_reads = 20

    driver, session = setup(session_type=UnreadableSession)
    [entry] = read_routes(driver)
    with pytest.raises(RouterRollbackError, match='回滚未完成'):
        mutate_route(driver, 'update', entry, VALUES)
    assert len(session.writes) == 1 and session.rows[ROUTES][0]['comment'] == 'manual'


def test_lost_undo_patch_response_is_verified_as_restored():
    driver, session = setup(session_type=ReadFailureSession, timeout_after=('PATCH', ROUTES, 2))
    [entry] = read_routes(driver)
    with pytest.raises(RouterError) as caught:
        mutate_route(driver, 'update', entry, VALUES)
    assert not isinstance(caught.value, RouterRollbackError)
    assert len(session.writes) == 2
    assert session.rows[ROUTES][0]['comment'] == ROUTE['comment']


def test_lost_recreation_response_can_verify_restored_configuration_without_more_writes():
    class LostRestoreSession(StatefulRouterOS):
        def _written_response(self, method, collection, response):
            if method == 'PUT':
                raise requests.Timeout('restoration response lost')
            return super()._written_response(method, collection, response)

    driver, session = setup(session_type=LostRestoreSession, timeout_after=('DELETE', ROUTES, 1))
    [entry] = read_routes(driver)
    with pytest.raises(UncertainWrite) as caught:
        mutate_route(driver, 'delete', entry, None)
    assert not isinstance(caught.value, RouterRollbackError)
    assert len(session.writes) == 2
    [restored] = read_routes(driver)
    assert restored['cidr'] == entry['cidr'] and restored['snapshot']['distance'] == '17'


def test_failed_readback_of_undo_never_claims_verified_recovery():
    class UnverifiedUndoSession(ReadFailureSession):
        def _written_response(self, method, collection, response):
            result = super()._written_response(method, collection, response)
            if len(self.writes) == 2:
                self.remaining_read_failures = 10
            return result

    driver, session = setup(session_type=UnverifiedUndoSession)
    [entry] = read_routes(driver)
    with pytest.raises(RouterRollbackError, match='回滚未完成'):
        mutate_route(driver, 'update', entry, VALUES)
    assert len(session.writes) == 2 and session.rows[ROUTES][0]['comment'] == ROUTE['comment']


class ConcurrentSession(StatefulRouterOS):
    external_change = None

    def _written_response(self, method, collection, response):
        result = super()._written_response(method, collection, response)
        if len(self.writes) == 1:
            self.change_on_read = True
        return result

    def request(self, method, url, **kwargs):
        if method == 'GET' and getattr(self, 'change_on_read', False):
            self.change_on_read = False
            self.external_change(self.rows[ROUTES])
        return super().request(method, url, **kwargs)


@pytest.mark.parametrize('action', ['add', 'update'])
def test_external_advanced_change_after_write_is_never_overwritten_or_deleted(action):
    driver, session = setup([] if action == 'add' else None, session_type=ConcurrentSession)
    session.external_change = lambda rows: rows[-1].update({'distance': '77'})
    expected = read_routes(driver)[0] if action == 'update' else None
    with pytest.raises(RouterRollbackError, match='外部变化'):
        mutate_route(driver, action, expected, VALUES)
    assert len(session.writes) == 1
    assert session.rows[ROUTES][-1]['distance'] == '77'


def test_external_replacement_after_delete_is_not_overwritten_by_restore():
    class ReplacedSession(StatefulRouterOS):
        def _written_response(self, method, collection, response):
            if method == 'DELETE':
                self.rows[ROUTES].append({**ROUTE, '.id': '*99', 'comment': 'other operator'})
            return super()._written_response(method, collection, response)

    driver, session = setup(session_type=ReplacedSession, timeout_after=('DELETE', ROUTES, 1))
    [entry] = read_routes(driver)
    with pytest.raises(RouterRollbackError, match='已存在目标'):
        mutate_route(driver, 'delete', entry, None)
    assert len(session.writes) == 1 and session.rows[ROUTES][0]['comment'] == 'other operator'


def test_duplicate_added_during_create_only_removes_our_known_id():
    driver, session = setup([], session_type=ConcurrentSession)
    external = {**ROUTE, '.id': '*99', 'dst-address': VALUES['cidr'], 'comment': 'external'}
    session.external_change = lambda rows: rows.append(copy.deepcopy(external))
    with pytest.raises(RouterError, match='已存在目标') as caught:
        mutate_route(driver, 'add', None, VALUES)
    assert not isinstance(caught.value, RouterRollbackError)
    assert not isinstance(caught.value, RouterConflictError)
    assert session.rows[ROUTES] == [external]
    assert session.writes[-1][:2] == ('DELETE', ROUTES + '/*65')


def test_restore_failure_reports_original_and_compensation_errors():
    driver, session = setup(timeout_after=('DELETE', ROUTES, 1), fail=('PUT', ROUTES, 1))
    [entry] = read_routes(driver)
    with pytest.raises(RouterRollbackError, match='恢复后回读不一致'):
        mutate_route(driver, 'delete', entry, None)
    assert session.rows[ROUTES] == []


@pytest.mark.parametrize('values', [None, {}, {**VALUES, 'enabled': 'false'},
                                    {**VALUES, 'gateway': 'ether1'},
                                    {**VALUES, 'extra': 'not permitted'}])
def test_invalid_values_are_rejected_before_writing(values):
    driver, session = setup()
    with pytest.raises(RouterError):
        mutate_route(driver, 'add', None, values)
    assert not session.writes
