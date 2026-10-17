"""Unmarked traffic, legacy manifests and asynchronous ROS rule activation."""
import copy

import pytest

from backend.routers.release import execute
from backend.routers.ros_policy import ADDRESSES, MANGLE, POLICY_VERSION, config_row, stable
from backend.routers.ros_policy_view import read_policy_objects
from backend.test_ros_policy import MemoryROS, configurations, desired_for, inspect, release


def legacy_bundle(driver):
    release(driver)
    driver.ros_policy_manifest['spec'].pop('policy_version')
    for entry in driver.ros_policy_manifest['resources']:
        if entry['path'] != MANGLE:
            continue
        row = next(row for row in driver.rows[MANGLE] if row['.id'] == entry['id'])
        if row['action'] == 'jump':
            row['routing-mark'] = 'main'
        row['invalid'] = 'false'
        entry['config'] = {**config_row(row), 'invalid': 'true'}


@pytest.mark.parametrize('action', ['upgrade', 'withdraw', 'rollback'])
def test_legacy_runtime_flags_allow_upgrade_withdrawal_and_rollback(action):
    driver = MemoryROS()
    legacy_bundle(driver)
    before = configurations(driver)
    old_ids = {entry['id'] for entry in driver.ros_policy_manifest['resources']}
    assert all(row['status'] == 'matched' for row in read_policy_objects(driver, driver.ros_policy_manifest))
    if action == 'rollback':
        driver.fail = ('DELETE', ADDRESSES, 1)
    _, plan = inspect(driver, desired_for(driver, selected=action != 'withdraw'))
    assert not plan['empty'] and not plan['blockers'], plan
    result = execute(plan, driver, None)
    assert result['ok'] is (action != 'rollback'), result
    if action == 'rollback':
        assert configurations(driver) == before
        assert not any(row['status'] == 'failed' for row in result['rollback'])
    elif action == 'withdraw':
        assert driver.ros_policy_manifest is None
        assert not driver.rows[MANGLE]
    else:
        assert not old_ids & {entry['id'] for entry in driver.ros_policy_manifest['resources']}
        assert driver.ros_policy_manifest['spec']['policy_version'] == POLICY_VERSION
        jump = next(row for row in driver.rows[MANGLE] if row['action'] == 'jump')
        # Keep source/destination restrictions, but accept initially unmarked traffic.
        assert 'routing-mark' not in jump
        assert jump['src-address-list'] and jump['dst-address-list']
        assert jump['dst-address-type'] == '!local'
        assert any(row['action'] == 'return' and row['src-address-list'] for row in driver.rows[MANGLE])
        _, unchanged = inspect(driver)
        assert unchanged['empty'] and not unchanged['blockers']


@pytest.mark.parametrize('field,value', [('disabled', 'true'), ('routing-mark', 'other'),
                                       ('src-address-list', 'foreign'), ('unknown-matcher', 'changed')])
def test_runtime_normalization_still_blocks_actual_external_edits(field, value):
    driver = MemoryROS()
    legacy_bundle(driver)
    driver.rows[MANGLE][-1][field] = value
    before = len(driver.writes)
    _, plan = inspect(driver)
    assert any('外部修改' in reason for reason in plan['blockers'])
    assert len(driver.writes) == before


class ActivatingROS(MemoryROS):
    def __init__(self, *, ready=True, edit=False):
        super().__init__()
        self.ready, self.edit, self.activation_reads = ready, edit, 0

    def _request(self, method, path, values=None):
        result = super()._request(method, path, values)
        if method == 'PUT' and path == MANGLE:
            self.rows[MANGLE][-1]['invalid'] = 'true'
            result['invalid'] = 'true'
        if method == 'GET' and path == MANGLE and len(self.rows[MANGLE]) == 3:
            self.activation_reads += 1
            if self.activation_reads >= 4:
                for row in self.rows[MANGLE]:
                    if self.ready:
                        row['invalid'] = 'false'
                if self.edit:
                    self.rows[MANGLE][-1]['src-address-list'] = 'foreign-edit'
                result = copy.deepcopy(self.rows[MANGLE])
        return result


def test_short_invalid_transition_is_waited_for_and_not_recorded_as_config(monkeypatch):
    sleeps = []
    monkeypatch.setattr('backend.routers.ros_policy_write.time.sleep', sleeps.append)
    driver = ActivatingROS()
    release(driver)
    assert sleeps and sum(sleeps) <= 2
    assert all(row['invalid'] == 'false' for row in driver.rows[MANGLE])
    assert all('invalid' not in entry['config'] for entry in driver.ros_policy_manifest['resources'])


def test_persistently_invalid_bundle_fails_and_rolls_back(monkeypatch):
    monkeypatch.setattr('backend.routers.ros_policy_write.time.sleep', lambda _: None)
    driver = ActivatingROS(ready=False)
    before = configurations(driver)
    _, plan = inspect(driver)
    result = execute(plan, driver, None)
    assert not result['ok'] and '尚未生效' in str(result), result
    assert configurations(driver) == before
    assert not any(row['status'] == 'failed' for row in result['rollback'])


def test_external_edit_during_activation_is_preserved(monkeypatch):
    monkeypatch.setattr('backend.routers.ros_policy_write.time.sleep', lambda _: None)
    driver = ActivatingROS(edit=True)
    _, plan = inspect(driver)
    result = execute(plan, driver, None)
    assert not result['ok'] and '外部修改' in str(result), result
    assert any(row.get('src-address-list') == 'foreign-edit' for row in driver.rows[MANGLE])


def test_preview_ignores_invalid_but_checks_runtime_health():
    driver = MemoryROS()
    release(driver)
    before = stable(driver.rows)
    driver.rows[MANGLE][-1]['invalid'] = 'true'
    assert stable(driver.rows) == before
    _, plan = inspect(driver)
    assert any('尚未生效' in reason for reason in plan['blockers'])
