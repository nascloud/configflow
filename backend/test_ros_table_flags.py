"""RouterOS REST's present-empty FIB flag is not an unset or false boolean."""
import copy

import pytest

from backend.routers.release import execute
from backend.routers.ros_policy_write import _matches_values
from backend.test_ros_policy import (
    ADDRESSES, MANGLE, ROUTES, TABLES, MemoryROS, configurations, desired_for,
    inspect, release,
)


class FlagTableROS(MemoryROS):
    """Model observed ROS table serialization, including uncertain PUT results."""
    def _request(self, method, path, values=None):
        result = None
        try:
            result = super()._request(method, path, values)
            return result
        finally:
            for row in self.rows[TABLES]:
                if row.get('fib') == 'true':
                    row['fib'] = ''
            if path == TABLES and isinstance(result, dict) and result.get('fib') == 'true':
                result['fib'] = ''


@pytest.mark.parametrize('actual', ['', 'true', 'yes', True])
def test_present_fib_flag_matches_enabled(actual):
    assert _matches_values(TABLES, {'name': 'cf', 'fib': actual}, {'name': 'cf', 'fib': 'true'})


@pytest.mark.parametrize('row', [{}, {'fib': 'false'}, {'fib': False}, {'fib': 'no'},
                               {'fib': None}, {'fib': 'unknown'}])
def test_missing_disabled_or_unknown_fib_cannot_pass(row):
    assert not _matches_values(TABLES, {'name': 'cf', **row}, {'name': 'cf', 'fib': 'true'})


def test_normalization_is_scoped_to_table_fib_and_keeps_other_fields_strict():
    assert not _matches_values(TABLES, {'name': '', 'fib': ''}, {'name': 'cf', 'fib': 'true'})
    assert not _matches_values(MANGLE, {'fib': ''}, {'fib': 'true'})
    assert not _matches_values(MANGLE, {'disabled': ''}, {'disabled': 'true'})
    assert not _matches_values(TABLES, {'name': 'cf', 'fib': ''}, {'name': 'cf', 'fib': 'false'})


def test_real_table_flag_can_publish_verify_replace_and_withdraw():
    driver = FlagTableROS()
    # Perform an initial read so the existing main table has the real wire form.
    driver._list(TABLES)
    before = configurations(driver)
    release(driver)
    table = next(item for item in driver.ros_policy_manifest['resources'] if item['path'] == TABLES)
    assert table['config']['fib'] == ''
    assert inspect(driver)[1]['empty']
    _, plan = inspect(driver, desired_for(driver, cidrs=['149.154.160.0/20']))
    result = execute(plan, driver, None)
    assert result['ok'], result
    assert not any(row['.id'] == table['id'] for row in driver.rows[TABLES])
    _, plan = inspect(driver, desired_for(driver, selected=False))
    assert execute(plan, driver, None)['ok']
    assert configurations(driver)[TABLES] == before[TABLES]
    assert not driver.rows[MANGLE] and not driver.rows[ADDRESSES]


@pytest.mark.parametrize('failure', ['table_response_lost', 'later_create', 'old_bundle_delete'])
def test_flag_table_failure_compensates_and_preserves_original_bundle(failure):
    driver = FlagTableROS()
    driver._list(TABLES)
    if failure == 'old_bundle_delete':
        release(driver)
    before = configurations(driver)
    if failure == 'table_response_lost':
        driver.lost = ('PUT', TABLES, 1)
    elif failure == 'later_create':
        driver.fail = ('PUT', ROUTES, 1)
    else:
        driver.fail = ('DELETE', ADDRESSES, 1)
    _, plan = inspect(driver, desired_for(driver, cidrs=['149.154.160.0/20']))
    result = execute(plan, driver, None)
    assert not result['ok'], result
    assert not any(row['status'] == 'failed' for row in result['rollback']), result
    assert configurations(driver) == before


def test_actual_fib_disable_after_publish_is_not_hidden_by_normalization():
    driver = FlagTableROS()
    release(driver)
    driver.rows[TABLES][-1]['fib'] = 'false'
    writes = copy.deepcopy(driver.writes)
    _, plan = inspect(driver)
    assert any('外部修改' in blocker for blocker in plan['blockers'])
    assert not execute(plan, driver, None)['ok']
    assert driver.writes == writes


def test_policy_view_shows_enabled_fib_without_rewriting_ownership():
    from backend.routers.ros_policy_view import read_policy_objects
    driver = FlagTableROS()
    release(driver)
    manifest = copy.deepcopy(driver.ros_policy_manifest)
    table = next(row for row in read_policy_objects(driver, manifest) if row['kind'] == 'tables')
    assert table['status'] == 'matched' and table['fields']['fib'] == 'true'
    assert manifest == driver.ros_policy_manifest
    assert next(row for row in manifest['resources'] if row['path'] == TABLES)['config']['fib'] == ''


class WireValuesROS(FlagTableROS):
    def _request(self, method, path, values=None):
        result = None
        def normalize(row, resource):
            if resource == ROUTES and isinstance(row.get('gateway'), str):
                row['gateway'] = row['gateway'].removesuffix('@main')
            if resource == MANGLE and row.get('passthrough') == 'false':
                row['passthrough'] = 'no'
        try:
            result = super()._request(method, path, values)
            return result
        finally:
            for resource in (ROUTES, MANGLE):
                for row in self.rows[resource]:
                    normalize(row, resource)
            if isinstance(result, dict):
                normalize(result, path)


@pytest.mark.parametrize('failure', [None, 'route_response_lost', 'mangle_response_lost', 'old_bundle_delete'])
def test_equivalent_gateway_and_boolean_forms_work_during_release_and_rollback(failure):
    driver = WireValuesROS()
    driver._list(TABLES)
    if failure == 'old_bundle_delete':
        release(driver)
    before = configurations(driver)
    if failure == 'route_response_lost':
        driver.lost = ('PUT', ROUTES, 1)
    elif failure == 'mangle_response_lost':
        driver.lost = ('PUT', MANGLE, 2)
    elif failure == 'old_bundle_delete':
        driver.fail = ('DELETE', ADDRESSES, 1)
    _, plan = inspect(driver, desired_for(driver, cidrs=['149.154.160.0/20']))
    result = execute(plan, driver, None)
    assert result['ok'] is (failure is None), result
    if failure:
        assert configurations(driver) == before
        assert not any(row['status'] == 'failed' for row in result['rollback']), result


@pytest.mark.parametrize('actual', ['192.168.0.3', '192.168.0.3@main'])
def test_only_default_gateway_lookup_alias_is_equivalent(actual):
    assert _matches_values(ROUTES, {'gateway': actual}, {'gateway': '192.168.0.3@main'})


@pytest.mark.parametrize('actual', ['', '192.168.0.4', '192.168.0.3@other',
                                   '192.168.0.3%other-lan', '192.168.0.3,192.168.0.4'])
def test_different_gateway_lookup_interface_or_multiple_next_hops_are_rejected(actual):
    assert not _matches_values(ROUTES, {'gateway': actual}, {'gateway': '192.168.0.3@main'})


@pytest.mark.parametrize('actual', ['', None, 'unknown', 'true', 'yes', True])
def test_missing_or_enabled_passthrough_cannot_match_disabled(actual):
    assert not _matches_values(MANGLE, {'passthrough': actual}, {'passthrough': 'false'})


def test_unset_fib_stops_before_mangle_and_reports_specific_field():
    class NoFibROS(MemoryROS):
        def _request(self, method, path, values=None):
            result = super()._request(method, path, values)
            if method == 'PUT' and path == TABLES:
                result.pop('fib', None)
                self.rows[TABLES][-1].pop('fib', None)
            return result
    driver = NoFibROS()
    before = configurations(driver)
    _, plan = inspect(driver)
    result = execute(plan, driver, None)
    assert not result['ok'], result
    assert '字段：fib' in str(result)
    assert configurations(driver) == before
    assert not any(path == MANGLE for _, path, _ in driver.writes)
