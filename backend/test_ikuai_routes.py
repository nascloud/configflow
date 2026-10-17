"""Manual iKuai static route edits preserve full forms and foreign changes."""
import copy

import pytest
import requests

from backend.routers.base import RouterConflictError, RouterError, RouterRollbackError
from backend.routers.ikuai import IKuaiDriver, ROUTE_FIELDS, decode_value, encode_value
from backend.routers.ikuai_routes import mutate_route, read_routes


def row(row_id=1, cidr='198.18.0.0', **changes):
    return {'id': row_id, 'enabled': 'yes', 'tagname': f'RT_{row_id}', 'ip_type': '4',
            'interface': 'lan4', 'dst_addr': cidr, 'gateway': '192.168.1.4',
            'netmask': '255.255.0.0', 'prio': 7, 'comment': '手工 路由', **changes}


def values(**changes):
    return {'cidr': '198.19.0.0/16', 'gateway': '192.168.1.5',
            'enabled': False, 'comment': '手工修改', **changes}


class Response:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class RouteDevice:
    def __init__(self, rows):
        self.rows = encode_value(copy.deepcopy(rows))
        self.next_id = max([item['id'] for item in rows] + [0]) + 1
        self.writes = []
        self.reads = []
        self.before_read = None
        self.after_write = None
        self.fail_before = None
        self.fail_after = None
        self.show_override = None
        self.fail_reads = set()
        self.fail_all_reads = False
        self.trust_env = False
        self.verify = True

    def close(self):
        pass

    @property
    def records(self):
        return decode_value(copy.deepcopy(self.rows))

    def post(self, url, json=None, **kwargs):
        if url.endswith('/Action/login'):
            return Response({'code': 0})
        assert json['func_name'] == 'static_rt'
        action, param = json['action'], json['param']
        if action == 'show':
            self.reads.append(copy.deepcopy(param))
            if self.before_read:
                self.before_read(len(self.reads), self)
            if self.fail_all_reads or len(self.reads) in self.fail_reads:
                raise requests.Timeout('read timeout')
            if self.show_override:
                return Response({'code': 0, 'results': self.show_override(len(self.reads), self)})
            offset, size = map(int, param['limit'].split(','))
            return Response({'code': 0, 'results': {
                'total': len(self.rows), 'data': copy.deepcopy(self.rows[offset:offset + size])}})
        self.writes.append((action, decode_value(copy.deepcopy(param))))
        if self.fail_before == action:
            self.fail_before = None
            return Response({'code': 2, 'message': 'rejected'})
        if action in ('add', 'edit'):
            assert set(param) == set(ROUTE_FIELDS) - ({'id'} if action == 'add' else set())
            assert 1 <= len(param['tagname']) <= 15
        if action == 'add':
            self.rows.append({**copy.deepcopy(param), 'id': self.next_id})
            self.next_id += 1
        elif action == 'edit':
            index = next(i for i, item in enumerate(self.rows) if item['id'] == param['id'])
            self.rows[index] = copy.deepcopy(param)
        elif action == 'del':
            self.rows[:] = [item for item in self.rows if item['id'] != param['id']]
        else:
            raise AssertionError(action)
        if self.after_write:
            self.after_write(action, self)
        if self.fail_after == action:
            self.fail_after = None
            raise requests.Timeout('response lost after mutation')
        return Response({'code': 0, 'results': {}})


def driver_for(rows):
    driver = IKuaiDriver('http://router.invalid', 'admin', 'test')
    device = RouteDevice(rows)
    driver.session = device
    return driver, device


def test_read_all_pages_and_runtime_stable_snapshot():
    driver, device = driver_for([row(i, status=i % 2) for i in range(1, 1002)])
    entries = read_routes(driver)
    assert len(entries) == 1001
    assert [call['limit'] for call in device.reads] == ['0,1000', '1000,1000']
    first = entries[0]
    assert first == {'id': '1', 'cidr': '198.18.0.0/16', 'gateway': '192.168.1.4',
                     'enabled': True, 'comment': '手工 路由', 'table': 'main',
                     'editable': True, 'reason': '', 'managed': False, 'snapshot': row()}
    device.rows[0]['status'] = 99
    assert read_routes(driver)[0]['snapshot'] == first['snapshot']
    assert device.writes == []


@pytest.mark.parametrize('result', [
    {'total': 1, 'data': []}, {'total': 0, 'data': {}}, {'total': True, 'data': [row()]},
    {'total': '1', 'data': [row()]}, {'data': [row()]}, {'total': 1, 'data': [None]},
    {'total': 2, 'data': [row(), row()]}, {'total': 1, 'data': [row(row_id=True)]},
])
def test_read_rejects_incomplete_or_ambiguous_list(result):
    driver, device = driver_for([])
    device.show_override = lambda *_: result
    with pytest.raises(RouterError):
        read_routes(driver)
    assert device.writes == []


def test_read_rejects_changed_total_between_pages():
    driver, device = driver_for([row(i) for i in range(1, 1002)])
    device.before_read = lambda number, current: current.rows.pop() if number == 2 else None
    with pytest.raises(RouterError, match='发生变化'):
        read_routes(driver)


@pytest.mark.parametrize('changes', [
    {'unknown_form_field': 'preserve me'}, {'ip_type': '6'}, {'gateway': ''},
    {'prio': '7'}, {'enabled': 'maybe'}, {'interface': ''}, {'tagname': ''},
    {'gateway': True}, {'dst_addr': 'blackhole'}, {'comment': []},
])
def test_unsupported_route_remains_visible_but_readonly(changes):
    driver, device = driver_for([row(**changes)])
    entry = read_routes(driver)[0]
    assert entry['editable'] is False
    assert entry['reason']
    with pytest.raises(RouterConflictError):
        mutate_route(driver, 'update', entry, values())
    with pytest.raises(RouterConflictError):
        mutate_route(driver, 'delete', entry, None)
    assert device.writes == []


def test_missing_form_field_is_readonly():
    original = row()
    original.pop('prio')
    driver, _ = driver_for([original])
    assert not read_routes(driver)[0]['editable']


def test_add_uses_manual_comment_and_precise_unique_identity():
    driver, device = driver_for([row()])
    result = mutate_route(driver, 'add', None, values(comment='  DNS & backup\n路由  '))
    assert result['changed'] is True
    entry = result['entry']
    assert entry['cidr'] == '198.19.0.0/16'
    assert entry['comment'] == 'DNS & backup\n路由'
    assert entry['managed'] is False
    assert entry['enabled'] is False
    assert entry['id'] == '2'
    assert entry['snapshot']['tagname'].startswith('RT')
    assert len(entry['snapshot']['tagname']) == 15
    assert entry['snapshot']['interface'] == 'auto'
    assert device.records[0] == row()


def test_update_preserves_interface_priority_and_tag_full_form():
    driver, device = driver_for([row(comment='cf-custom', status=1)])
    expected = read_routes(driver)[0]
    result = mutate_route(driver, 'update', expected, values(comment='用户手工备注'))
    assert result['entry']['snapshot'] == row(
        cidr='198.19.0.0', gateway='192.168.1.5', enabled='no', comment='用户手工备注')
    assert result['entry']['managed'] is False
    assert device.writes == [('edit', result['entry']['snapshot'])]


def test_noop_update_rechecks_snapshot_without_writing():
    driver, device = driver_for([row()])
    expected = read_routes(driver)[0]
    result = mutate_route(driver, 'update', expected,
                          {key: expected[key] for key in ('cidr', 'gateway', 'enabled', 'comment')})
    assert result == {'entry': expected, 'changed': False}
    assert len(device.reads) == 2
    assert not device.writes


def test_delete_only_selected_id():
    driver, device = driver_for([row(), row(2, '203.0.0.0')])
    expected = read_routes(driver)[0]
    assert mutate_route(driver, 'delete', expected, None) == {'entry': None, 'changed': True, 'id': '1'}
    assert device.records == [row(2, '203.0.0.0')]
    assert device.writes == [('del', {'id': 1})]


@pytest.mark.parametrize('action', ['add', 'update'])
def test_duplicate_destination_rejected_even_if_disabled_or_different_gateway(action):
    driver, device = driver_for([row(), row(2, '198.19.0.0', enabled='no', gateway='192.168.1.99')])
    expected = read_routes(driver)[0] if action == 'update' else None
    with pytest.raises(RouterConflictError, match='重复配置'):
        mutate_route(driver, action, expected, values())
    assert not device.writes


@pytest.mark.parametrize('action', ['update', 'delete'])
def test_snapshot_drift_rejected_before_any_write(action):
    driver, device = driver_for([row()])
    expected = read_routes(driver)[0]
    device.rows[0]['prio'] = 9
    with pytest.raises(RouterConflictError, match='发生变化'):
        mutate_route(driver, action, expected, values())
    assert not device.writes
    assert device.records[0]['prio'] == 9


@pytest.mark.parametrize('action', ['add', 'update', 'delete'])
def test_failed_read_before_mutation_is_zero_write(action):
    driver, device = driver_for([row()])
    expected = read_routes(driver)[0] if action != 'add' else None
    device.fail_all_reads = True
    with pytest.raises(RouterError):
        mutate_route(driver, action, expected, values())
    assert not device.writes


@pytest.mark.parametrize('action,api_action', [('add', 'add'), ('update', 'edit'), ('delete', 'del')])
def test_lost_response_after_mutation_restores_original_configuration(action, api_action):
    driver, device = driver_for([row(), row(2, '203.0.0.0')])
    expected = read_routes(driver)[0] if action != 'add' else None
    device.fail_after = api_action
    with pytest.raises(RouterError, match='未保留本次变更'):
        mutate_route(driver, action, expected, values())
    restored = [{key: value for key, value in item.items() if key != 'id'} for item in device.records]
    originals = [{key: value for key, value in item.items() if key != 'id'}
                 for item in [row(), row(2, '203.0.0.0')]]
    assert sorted(restored, key=lambda item: item['tagname']) == sorted(originals, key=lambda item: item['tagname'])
    assert [item[0] for item in device.writes] == {
        'add': ['add', 'del'], 'update': ['edit', 'edit'], 'delete': ['del', 'add']}[action]


@pytest.mark.parametrize('action,api_action', [('add', 'add'), ('update', 'edit'), ('delete', 'del')])
def test_vendor_rejection_leaves_original_configuration(action, api_action):
    driver, device = driver_for([row()])
    expected = read_routes(driver)[0] if action != 'add' else None
    device.fail_before = api_action
    with pytest.raises(RouterError, match='rejected'):
        mutate_route(driver, action, expected, values())
    assert device.records == [row()]
    assert len(device.writes) == 1


def test_failed_readback_then_recovery_allows_exact_compensation():
    driver, device = driver_for([row()])
    expected = read_routes(driver)[0]
    device.fail_reads = {3}  # expected read, write preflight, failed readback
    with pytest.raises(RouterError, match='未保留本次变更'):
        mutate_route(driver, 'update', expected, values())
    assert device.records == [row()]
    assert [item[0] for item in device.writes] == ['edit', 'edit']


@pytest.mark.parametrize('action', ['add', 'update', 'delete'])
def test_continuous_read_failure_never_compensates_without_current_evidence(action):
    driver, device = driver_for([row()])
    expected = read_routes(driver)[0] if action != 'add' else None
    device.after_write = lambda _, current: setattr(current, 'fail_all_reads', True)
    with pytest.raises(RouterRollbackError, match='安全补偿未完成'):
        mutate_route(driver, action, expected, values())
    assert len(device.writes) == 1


def test_update_does_not_overwrite_concurrent_change_during_failed_readback():
    driver, device = driver_for([row()])
    expected = read_routes(driver)[0]
    device.after_write = lambda _, current: current.rows[0].update(prio=99)
    with pytest.raises(RouterRollbackError, match='其他操作修改'):
        mutate_route(driver, 'update', expected, values())
    assert device.records[0]['prio'] == 99
    assert len(device.writes) == 1


def test_delete_compensation_refuses_reused_id_and_preserves_foreign_row():
    driver, device = driver_for([row()])
    expected = read_routes(driver)[0]

    def replace(action, current):
        if action == 'del':
            current.rows.append(encode_value(row(comment='someone else', gateway='192.168.1.99')))

    device.after_write = replace
    device.fail_after = 'del'
    with pytest.raises(RouterRollbackError, match='ID 已发生变化'):
        mutate_route(driver, 'delete', expected, None)
    assert device.records == [row(comment='someone else', gateway='192.168.1.99')]
    assert len(device.writes) == 1


def test_delete_compensation_refuses_new_duplicate_destination():
    driver, device = driver_for([row()])
    expected = read_routes(driver)[0]
    device.after_write = lambda _, current: current.rows.append(encode_value(row(20, comment='foreign')))
    device.fail_after = 'del'
    with pytest.raises(RouterRollbackError, match='重复配置'):
        mutate_route(driver, 'delete', expected, None)
    assert device.records == [row(20, comment='foreign')]
    assert len(device.writes) == 1


def test_add_never_deletes_similar_but_unprovable_route():
    driver, device = driver_for([row()])
    device.after_write = lambda _, current: current.rows[-1].update(prio=99)
    device.fail_after = 'add'
    with pytest.raises(RouterRollbackError, match='无法唯一确认'):
        mutate_route(driver, 'add', None, values())
    assert len(device.records) == 2
    assert device.records[-1]['prio'] == 99
    assert [item[0] for item in device.writes] == ['add']


def test_add_ambiguous_identical_new_objects_are_not_deleted():
    driver, device = driver_for([])

    def duplicate(_, current):
        current.rows.append({**current.rows[0], 'id': 99})

    device.after_write = duplicate
    with pytest.raises(RouterRollbackError, match='无法唯一确认'):
        mutate_route(driver, 'add', None, values())
    assert len(device.records) == 2
    assert [item[0] for item in device.writes] == ['add']


def test_new_foreign_duplicate_after_add_only_exact_new_object_is_compensated():
    driver, device = driver_for([row()])

    def foreign(action, current):
        if action == 'add':
            current.rows.append(encode_value(row(99, '198.19.0.0', comment='foreign')))

    device.after_write = foreign
    with pytest.raises(RouterError, match='重复配置'):
        mutate_route(driver, 'add', None, values())
    assert device.records == [row(), row(99, '198.19.0.0', comment='foreign')]
    assert device.writes[-1] == ('del', {'id': 2})


def test_rollback_failure_is_explicit_and_does_not_claim_success():
    driver, device = driver_for([])
    device.fail_after = 'add'
    device.fail_before = 'del'
    with pytest.raises(RouterRollbackError, match='安全补偿未完成'):
        mutate_route(driver, 'add', None, values())
    assert len(device.records) == 1


@pytest.mark.parametrize('change', [
    {'enabled': 1}, {'gateway': True}, {'gateway': 123}, {'cidr': '2001:db8::/32'},
    {'gateway': '2001:db8::1'}, {'comment': None}, {'unexpected': 'x'},
])
def test_invalid_values_are_rejected_before_writing(change):
    driver, device = driver_for([])
    with pytest.raises(RouterError):
        mutate_route(driver, 'add', None, values(**change))
    assert not device.reads
    assert not device.writes
