"""Use the Agent's checked allocation and reject changes after preview."""
import copy

import pytest

from backend.routers.plan import return_safety_blockers
from backend.routers.release import execute
from backend.test_router_release import SAFE_RETURN_ALLOCATION, SAFE_RETURN_STATUS, FakeAgent
from backend.test_router_return_routing import NoAgentCalls, plan_for, return_stage, router_driver


def allocation_status(**changes):
    status = copy.deepcopy(SAFE_RETURN_STATUS)
    status['safety']['allocation'].update(changes)
    return status


def router(vendor='ikuai'):
    return {'type': vendor, 'endpoint': 'http://10.0.0.1', 'manage_return_routing': True}


@pytest.mark.parametrize('vendor', ['ikuai', 'routeros'])
def test_agent_allocation_drives_preview_writes_and_preserves_default_resources(vendor):
    driver, session = router_driver(vendor)
    status = allocation_status(table=105, priority=103, mark='0x800000')
    manual_table = {'family': 'inet', 'name': 'manual_return', 'marks': ['0x400000']}
    manual_rule = {'priority': '104', 'mark': '0x400000/0x400000', 'table': '104'}
    status['return_tables'] = [manual_table]
    status['fwmark_rules'] = [manual_rule]
    plan = plan_for(router(vendor), driver, status)
    assert plan['blockers'] == []
    [op] = return_stage(plan)['ops']
    for field, value in {'table': 105, 'priority': 103, 'mark': '0x800000'}.items():
        assert op['spec'][field] == value
        assert plan['checks']['return_routing'][field] == value
    assert all(text in op['summary'] for text in ('路由表 105', '优先级 103', 'mark 0x800000'))
    assert '104' not in op['summary'] and '0x400000' not in op['summary']
    assert plan['fingerprint'] != plan_for(router(vendor), driver, SAFE_RETURN_STATUS)['fingerprint']
    agent = FakeAgent(status)
    result = execute(plan, driver, agent)
    assert result['ok'], result
    assert session.writes
    assert manual_table in agent.status()['return_tables']
    assert manual_rule in agent.status()['fwmark_rules']
    assert agent.status()['safety']['allocation']['reused'] is True
    again = plan_for(router(vendor), driver, agent.status())
    assert again['empty'] and return_stage(again)['ops'] == []
    assert execute(again, driver, agent)['ok']


@pytest.mark.parametrize('allocation', [None, {}, [], True, '104'])
def test_old_or_incomplete_allocation_blocks_with_upgrade_guidance(allocation):
    driver, session = router_driver('ikuai')
    status = allocation_status()
    if allocation is None:
        status['safety'].pop('allocation')
    else:
        status['safety']['allocation'] = allocation
    plan = plan_for(router(), driver, status)
    assert plan['stages'] == [] and plan['blockers']
    assert all(text in plan['blockers'][0] for text in ('自动分配', '升级 Agent', '关闭自动配置回流'))
    assert not execute(plan, driver, NoAgentCalls())['ok']
    assert session.writes == []


@pytest.mark.parametrize(('field', 'value'), [
    ('version', None), ('version', True), ('version', 0), ('version', 2), ('version', '1'),
    ('table', None), ('table', True), ('table', 0), ('table', 253), ('table', 104.0), ('table', '104'),
    ('priority', None), ('priority', False), ('priority', 0), ('priority', 32766),
    ('priority', 104.0), ('priority', '104'),
    ('mark', None), ('mark', True), ('mark', 4194304), ('mark', ''), ('mark', '4194304'),
    ('mark', '0x0'), ('mark', '0x3'), ('mark', '0xffffffff'), ('mark', '0x100000000'),
    ('mark', '-0x1'), ('mark', '0x400000/0x400000'), ('mark', ' 0x400000'),
    ('reused', None), ('reused', 'false'), ('reused', 0), ('reused', 1),
])
def test_allocation_contract_rejects_invalid_numbers_mark_and_reuse_flag(field, value):
    driver, session = router_driver('ikuai')
    plan = plan_for(router(), driver, allocation_status(**{field: value}))
    assert plan['blockers'] and plan['stages'] == []
    assert session.writes == []


@pytest.mark.parametrize(('table', 'priority', 'mark', 'normalized'), [
    (1, 1, '0x1', '0x1'),
    (252, 32765, '0x80000000', '0x80000000'),
    (104, 104, '0X00400000', '0x400000'),
])
def test_supported_allocation_boundaries_and_hex_normalization(table, priority, mark, normalized):
    driver, _ = router_driver('ikuai')
    status = allocation_status(table=table, priority=priority, mark=mark)
    plan = plan_for(router(), driver, status)
    assert plan['blockers'] == []
    [op] = return_stage(plan)['ops']
    assert op['spec']['table'] == table and op['spec']['priority'] == priority
    assert op['spec']['mark'] == normalized
    result = execute(plan, driver, FakeAgent(status))
    assert result['ok'], result


@pytest.mark.parametrize(('field', 'value'), [('table', 105), ('priority', 103), ('mark', '0x800000')])
def test_allocation_change_after_preview_stops_before_any_write(field, value):
    driver, session = router_driver('ikuai')
    agent = FakeAgent(SAFE_RETURN_STATUS)
    plan = plan_for(router(), driver, agent.status())
    agent.state['safety']['allocation'][field] = value
    fresh = plan_for(router(), driver, agent.status())
    assert fresh['fingerprint'] != plan['fingerprint']
    result = execute(plan, driver, agent)
    assert not result['ok'] and result['failed_stage'] == 'return_routing'
    assert '重新预览' in result['log'][-1]['summary']
    assert session.writes == [] and agent.calls == []


def test_unchanged_return_stage_still_rejects_changed_allocation():
    driver, session = router_driver('ikuai')
    agent = FakeAgent(SAFE_RETURN_STATUS)
    first = plan_for(router(), driver, agent.status())
    agent.apply(return_stage(first)['ops'][0]['spec'])
    plan = plan_for(router(), driver, agent.status())
    assert return_stage(plan)['ops'] == [] and not plan['empty']
    agent.calls.clear()
    agent.state['safety']['allocation']['priority'] = 103
    result = execute(plan, driver, agent)
    assert not result['ok'] and result['failed_stage'] == 'return_routing'
    assert session.writes == [] and agent.calls == []


@pytest.mark.parametrize('allocation', [None, {}, {'table': 105}, {**SAFE_RETURN_ALLOCATION, 'table': 253}])
def test_missing_or_invalid_allocation_after_apply_does_not_block_owned_cleanup(allocation):
    class ChangedAllocationAgent(FakeAgent):
        def apply(self, spec):
            super().apply(spec)
            self.state['safety']['allocation'] = allocation

    driver, session = router_driver('ikuai')
    agent = ChangedAllocationAgent(allocation_status(table=105, priority=103, mark='0x800000'))
    plan = plan_for(router(), driver, agent.status())
    result = execute(plan, driver, agent)
    assert not result['ok'] and result['failed_stage'] == 'return_routing'
    assert session.writes == [] and agent.calls[-1] == ('remove', False)
    assert result['rollback'][0]['status'] == 'ok'
    assert agent.status()['managed'] is None and agent.status()['fwmark_rules'] == []


def test_changed_valid_allocation_after_apply_fails_readback_and_undoes_only_owned_tuple():
    class ChangedAllocationAgent(FakeAgent):
        def apply(self, spec):
            super().apply(spec)
            self.state['safety']['allocation']['table'] = 106

    driver, session = router_driver('ikuai')
    agent = ChangedAllocationAgent(allocation_status(table=105, priority=103, mark='0x800000'))
    plan = plan_for(router(), driver, agent.status())
    result = execute(plan, driver, agent)
    assert not result['ok'] and result['failed_stage'] == 'return_routing'
    assert '分配' in result['log'][-1]['summary']
    assert session.writes == [] and agent.calls[-1] == ('remove', False)
    assert result['rollback'][0]['status'] == 'ok' and agent.status()['fwmark_rules'] == []


def test_dynamic_allocation_rollback_preserves_other_programs_default_tuple():
    driver, session = router_driver('ikuai')
    session.fail = ('nat_rule', 'edit')
    status = allocation_status(table=105, priority=103, mark='0x800000')
    manual_rule = {'priority': '104', 'mark': '0x400000/0x400000', 'table': '104'}
    status['fwmark_rules'] = [manual_rule]
    agent = FakeAgent(status)
    result = execute(plan_for(router(), driver, status), driver, agent)
    assert not result['ok'] and result['failed_stage'] == 'nat_bypass'
    assert agent.calls[-1] == ('remove', False)
    assert all(item['status'] == 'ok' for item in result['rollback'])
    assert agent.status()['fwmark_rules'] == [manual_rule]


def test_cleanup_capability_check_ignores_allocation_but_still_rejects_old_safety_protocol():
    status = allocation_status()
    status['safety'].pop('allocation')
    assert return_safety_blockers(status, require_safe=False) == []
    status['safety']['version'] = 0
    assert return_safety_blockers(status, require_safe=False)


def test_disabled_return_does_not_require_allocation_or_contact_agent():
    driver, session = router_driver('ikuai')
    plan = plan_for({**router(), 'manage_return_routing': False}, driver, {'status': 'unavailable'})
    assert not plan['blockers'] and 'return_routing' not in plan['checks']
    assert execute(plan, driver, NoAgentCalls())['ok']
    assert session.writes
