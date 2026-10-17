"""iKuai DHCP DNS uses the complete form verified from official 4.0.120 firmware."""
import copy

import pytest
import requests

from backend.routers.base import RouterError, RouterRollbackError
from backend.routers.ikuai import DHCP_FIELDS, DHCP_INT_FIELDS, IKuaiDriver, decode_value, encode_value
from backend.test_routers import FakeResponse

DNS = '10.0.0.53'


def server(**changes):
    # Defaults and field types from the original etc/defaults/config_db.conf.
    row = {key: 0 if key in DHCP_INT_FIELDS else '' for key in DHCP_FIELDS}
    row.update(id=1, enabled='yes', tagname='DHS_1', interface='lan1', phy_ifnames='all',
               addr_pool='10.0.0.100-10.0.0.200', netmask='255.255.255.0', gateway='10.0.0.1',
               dns1='223.5.5.5', dns2='223.6.6.6', lease=120, check_addr_valid=1,
               status=1, available=73)
    row.update(changes)
    return row


def binding(**changes):
    row = dict(id=5, enabled='yes', tagname='PC', interface='auto', mac='00:11:22:33:44:55',
               gateway='', dns1='', dns2='', ip_addr='10.0.0.20', comment='',
               termname='Computer', hostname='Computer', ip_addr_int=167772180)
    row.update(changes)
    return row


class DHCPServer:
    def __init__(self, rows=None, bindings=None, effects=None):
        self.rows = copy.deepcopy(rows if rows is not None else [server()])
        self.bindings = copy.deepcopy(bindings or [])
        self.effects = list(effects or [])
        self.writes = []
        self.shows = []
        self.incomplete = None
        self.read_failures = 0
        self.trust_env = False
        self.verify = True

    def close(self):
        pass

    def post(self, url, json=None, **_):
        if url.endswith('/Action/login'):
            return FakeResponse({'code': 0})
        func, action, param = json['func_name'], json['action'], json['param']
        if action == 'show':
            self.shows.append((func, param))
            if self.read_failures:
                self.read_failures -= 1
                raise requests.Timeout('read temporarily unavailable')
            if func == self.incomplete:
                return FakeResponse({'code': 0, 'results': {'data': [], 'total': 1}})
            if func == 'dhcp_server':
                assert param['TYPE'] == 'total,data'
                return FakeResponse({'code': 0, 'results': {'data': encode_value(self.rows), 'total': len(self.rows)}})
            assert func == 'dhcp_static' and param['TYPE'] == 'static_total,static_data'
            return FakeResponse({'code': 0, 'results': {'static_data': encode_value(self.bindings),
                                                       'static_total': len(self.bindings)}})
        assert func == 'dhcp_server' and action == 'edit'
        assert set(param) == set(DHCP_FIELDS), 'must submit all and only vendor edit fields'
        for key in DHCP_INT_FIELDS:
            assert type(param[key]) is int
        self.writes.append(copy.deepcopy(param))
        effect = self.effects.pop(0) if self.effects else None
        if effect == 'reject':
            return FakeResponse({'code': 2, 'message': '请求参数不合法'})
        row = next(row for row in self.rows if row['id'] == param['id'])
        if effect != 'drop':
            row.update(decode_value(param))
        if effect == 'corrupt':
            row['lease'] = 1
        if effect == 'timeout':
            raise requests.Timeout('response lost after mutation')
        if effect == 'read_failure':
            self.read_failures = 2
        if effect == 'reads_down':
            self.read_failures = 100
        return FakeResponse({'code': 0, 'results': {}})


def driver(session=None):
    value = IKuaiDriver('http://10.0.0.1', 'admin', 'test')
    value.session = session or DHCPServer()
    return value


def operation(value):
    entry = value.read_dhcp_dns().entries[0]
    return {'kind': 'dhcp_dns_update', 'id': entry.id, 'dns': [DNS], 'expected': entry.attributes,
            'scope': {'id': entry.id, 'network': entry.network, 'gateway': '10.0.0.4'}}


def test_read_dhcp_full_semantics_and_no_writes():
    session = DHCPServer(rows=[server(opt43='01:02', opt121='10.8.0.0/16 10.0.0.9', opt_type121=2)],
                         bindings=[binding()])
    state = driver(session).read_dhcp_dns()
    entry = state.entries[0]
    assert entry.network == '10.0.0.0/24'
    assert entry.dns == ['223.5.5.5', '223.6.6.6']
    assert entry.enabled and not entry.blockers
    assert entry.dns_explicit
    assert set(entry.attributes) == set(DHCP_FIELDS)
    assert entry.attributes['opt121'] == '10.8.0.0/16 10.0.0.9'
    assert not session.writes
    before = copy.deepcopy(state)
    session.rows[0]['available'] += 3
    session.bindings[0]['hostname'] = 'changed name'
    session.bindings[0]['termname'] = 'changed'
    assert driver(session).read_dhcp_dns() == before


def test_update_changes_only_dns_and_undo_restores_complete_form():
    original = server(exclude_pool='10.0.0.123', lease=360, delay=3, domain='home & office',
                      opt43='01:02', opt121='10.8.0.0/16 10.0.0.9', opt_type121=2)
    session = DHCPServer(rows=[original, server(id=2, addr_pool='10.1.0.100-10.1.0.200', gateway='10.1.0.1')])
    value = driver(session)
    undo = value.apply(operation(value))
    assert session.rows[0] == {**original, 'dns1': DNS, 'dns2': DNS}
    assert session.rows[1]['dns1'] == '223.5.5.5'
    assert value.read_dhcp_dns().entries[0].dns == [DNS]
    assert session.writes[0]['domain'] == 'home%20%26%20office'
    value.apply(undo)
    assert session.rows[0] == original


@pytest.mark.parametrize('values', [(DNS, ''), ('', DNS), ('', '')])
def test_blank_dns_slot_is_not_explicit_and_can_be_filled(values):
    value = driver(DHCPServer(rows=[server(dns1=values[0], dns2=values[1])]))
    entry = value.read_dhcp_dns().entries[0]
    assert not entry.dns_explicit and not entry.blockers
    value.apply(operation(value))
    entry = value.read_dhcp_dns().entries[0]
    assert entry.dns_explicit and entry.dns == [DNS]


@pytest.mark.parametrize('effect', ['drop', 'corrupt', 'timeout', 'reject'])
def test_failed_or_lost_response_restores_original(effect):
    session = DHCPServer(effects=[effect])
    value = driver(session)
    before = copy.deepcopy(session.rows)
    with pytest.raises(RouterError):
        value.apply(operation(value))
    assert session.rows == before
    assert len(session.writes) == (2 if effect in ('corrupt', 'timeout') else 1)


def test_local_rollback_failure_is_explicit():
    session = DHCPServer(effects=['timeout', 'reject'])
    value = driver(session)
    with pytest.raises(RouterRollbackError, match='局部撤销失败'):
        value.apply(operation(value))
    assert session.rows[0]['dns1'] == DNS


@pytest.mark.parametrize('effect', ['read_failure', 'reads_down'])
def test_failed_readback_still_attempts_full_snapshot_restoration(effect):
    session = DHCPServer(effects=[effect])
    value = driver(session)
    before = copy.deepcopy(session.rows)
    with pytest.raises(RouterError) as error:
        value.apply(operation(value))
    assert session.rows == before
    assert len(session.writes) == 2
    assert isinstance(error.value, RouterRollbackError) == (effect == 'reads_down')


def test_global_undo_attempts_known_snapshot_even_when_reads_are_down():
    session = DHCPServer()
    value = driver(session)
    before = copy.deepcopy(session.rows)
    undo = value.apply(operation(value))
    session.read_failures = 100
    with pytest.raises(RouterError):
        value.apply(undo)
    assert session.rows == before
    assert len(session.writes) == 2


@pytest.mark.parametrize('change', [dict(gateway='10.0.0.2'), dict(dns2='8.8.8.8'), dict(opt43='01')])
def test_preview_drift_rejected_without_writing(change):
    session = DHCPServer()
    value = driver(session)
    op = operation(value)
    session.rows[0].update(change)
    with pytest.raises(RouterError, match='已变化'):
        value.apply(op)
    assert not session.writes


def test_latest_scope_overlap_is_rejected_without_writing():
    session = DHCPServer()
    value = driver(session)
    op = operation(value)
    session.rows.append(server(id=2, tagname='overlap'))
    with pytest.raises(RouterError, match='目标网段'):
        value.apply(op)
    assert not session.writes


@pytest.mark.parametrize('change', [dict(opt6='08:08:08:08'), dict(radius='yes'), dict(dns1='auto'),
                                  dict(check_relay_only=1), dict(phy_ifnames='eth1'), dict(status=0),
                                  dict(lease='120'), dict(enabled='unknown'),
                                  dict(addr_pool='10.0.0.100-10.1.0.200')])
def test_unverified_or_ambiguous_fields_block_writes(change):
    session = DHCPServer(rows=[server(**change)])
    value = driver(session)
    assert value.read_dhcp_dns().entries[0].blockers
    with pytest.raises(RouterError):
        value.apply(operation(value))
    assert not session.writes


def test_missing_edit_field_is_never_filled_with_a_default():
    session = DHCPServer()
    del session.rows[0]['opt119']
    value = driver(session)
    assert 'opt119' in ' '.join(value.read_dhcp_dns().entries[0].blockers)
    with pytest.raises(RouterError):
        value.apply(operation(value))
    assert not session.writes


@pytest.mark.parametrize('static', [binding(dns1='8.8.8.8'), binding(dns2='8.8.4.4'),
                                   binding(ip_addr=''), binding(ip_addr=1), binding(enabled='unknown'),
                                   binding(opt6='08:08:08:08')])
def test_static_dns_overrides_or_missing_evidence_block(static):
    session = DHCPServer(bindings=[static])
    value = driver(session)
    assert value.read_dhcp_dns().entries[0].blockers
    with pytest.raises(RouterError):
        value.apply(operation(value))
    assert not session.writes


@pytest.mark.parametrize('static', [binding(dns1='8.8.8.8', enabled='no'),
                                   binding(dns1='8.8.8.8', ip_addr='10.1.0.20'), binding()])
def test_disabled_other_network_and_inherited_static_dns_are_allowed(static):
    value = driver(DHCPServer(bindings=[static]))
    assert not value.read_dhcp_dns().entries[0].blockers
    value.apply(operation(value))
    assert value.read_dhcp_dns().entries[0].dns == [DNS]


@pytest.mark.parametrize('func', ['dhcp_server', 'dhcp_static'])
def test_truncated_or_malformed_response_never_means_empty_config(func):
    session = DHCPServer()
    session.incomplete = func
    with pytest.raises(RouterError, match='读取不完整'):
        driver(session).read_dhcp_dns()
    assert not session.writes


@pytest.mark.parametrize('dns', [[], [DNS, '8.8.8.8'], ['127.0.0.1'], ['0.0.0.0'], ['224.0.0.1'],
                                ['host.local'], [1], [True]])
def test_invalid_dns_target_is_rejected(dns):
    value = driver()
    op = operation(value)
    op['dns'] = dns
    with pytest.raises(RouterError):
        value.apply(op)
    assert not value.session.writes


@pytest.mark.parametrize('scope', [None, {}, {'id': '1'}, {'id': '1', 'gateway': '10.0.0.4', 'network': None}])
def test_missing_scope_rejected(scope):
    value = driver()
    op = operation(value)
    op['scope'] = scope
    with pytest.raises(RouterError, match='缺少已预览网段'):
        value.apply(op)
    assert not value.session.writes
