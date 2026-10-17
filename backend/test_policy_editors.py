"""Editors change authoring drafts and preserve ownership, including on conflict."""
import copy
import pytest
from backend.common.config import get_repository
from backend.test_agent_upgrade_auth import authenticated_app  # noqa: F401
from backend.test_ros_policy import MemoryROS, release, ADDRESSES, desired_for, inspect
from backend.routers.release import execute
from backend.routers.ros_policy_view import read_policy_objects


@pytest.fixture
def content_editor(authenticated_app, monkeypatch):
    client, _, _, admin, _ = authenticated_app
    repository = get_repository()
    rule = {'id': 'manual', 'name': 'Manual IPs', 'source_type': 'content',
            'content': '1.1.1.0/24\n8.8.8.8/32', 'behavior': 'ipcidr', 'enabled': True}
    repository.update_shared_transaction(lambda shared: shared.setdefault('rule_library', []).append(rule))
    written = []
    monkeypatch.setattr('backend.routes.rule_library.save_rule_to_local', lambda row: written.append(copy.deepcopy(row)))
    return client, repository, admin, written, rule


def test_content_edit_preserves_metadata_profiles_and_rejects_stale_edits(content_editor):
    client, repository, admin, written, rule = content_editor
    path = '/api/rule-library/manual/draft-content'
    original = client.get(path, headers=admin).get_json()
    before = copy.deepcopy(repository.get_profile('default'))
    response = client.put(path, headers=admin, json={'revision': original['revision'], 'content': '9.9.9.9/32'})
    assert response.status_code == 200, response.get_data(as_text=True)
    assert repository.get_shared()['rule_library'][-1] == {**rule, 'content': '9.9.9.9/32'}
    assert repository.get_profile('default') == before
    assert written == [{**rule, 'content': '9.9.9.9/32'}]
    response = client.put(path, headers=admin, json={'revision': original['revision'], 'content': 'overwrite'})
    assert response.status_code == 409
    assert '其他页面更新' in response.get_json()['message']
    assert len(written) == 1
    assert client.get(path, headers=admin).get_json()['rule']['content'] == '9.9.9.9/32'


@pytest.mark.parametrize('method', ['get', 'put'])
def test_content_endpoint_requires_authentication(content_editor, method):
    client, _, _, _, _ = content_editor
    assert getattr(client, method)('/api/rule-library/manual/draft-content').status_code == 401


@pytest.mark.parametrize('change', ['rename', 'url', 'delete'])
def test_other_source_changes_are_not_overwritten(content_editor, change):
    client, repository, admin, written, _ = content_editor
    path = '/api/rule-library/manual/draft-content'
    original = client.get(path, headers=admin).get_json()
    def mutate(shared):
        rule = shared['rule_library'][-1]
        if change == 'rename': rule['name'] = 'Renamed elsewhere'
        elif change == 'url':
            rule.update(source_type='url', url='https://example.test/ips.txt')
            rule.pop('content')
        else: shared['rule_library'].pop()
    repository.update_shared_transaction(mutate)
    before = copy.deepcopy(repository.get_shared())
    response = client.put(path, headers=admin, json={'revision': original['revision'], 'content': '1.1.1.1'})
    assert response.status_code == (404 if change == 'delete' else 409)
    assert repository.get_shared() == before and not written
    if change == 'url':
        fresh = client.get(path, headers=admin).get_json()
        response = client.put(path, headers=admin, json={'revision': fresh['revision'], 'content': '1.1.1.1'})
        assert response.status_code == 400
        assert repository.get_shared() == before and not written


@pytest.mark.parametrize('body', [None, [], {}, {'revision': 'x', 'content': []},
                                  {'revision': 'x', 'content': '', 'enabled': False}])
def test_invalid_content_edit_is_atomic(content_editor, body):
    client, repository, admin, written, _ = content_editor
    before = copy.deepcopy(repository.get_shared())
    response = client.put('/api/rule-library/manual/draft-content', headers=admin, json=body)
    assert response.status_code in (400, 415)
    assert repository.get_shared() == before and not written


def test_editor_roles_use_manifest_references_even_if_live_names_changed():
    driver = MemoryROS()
    release(driver)
    # The driver records the complete bundle for ownership and cleanup.
    manifest = driver.ros_policy_manifest
    before = copy.deepcopy(manifest)
    objects = read_policy_objects(driver, manifest)
    source = next(row for row in objects if row['editor'] == 'source')
    destination = next(row for row in objects if row['editor'] == 'destination')
    live = next(row for row in driver.rows[ADDRESSES] if row['.id'] == source['id'])
    live.update(list='unrelated-live-name', address='192.0.2.99')
    edited = next(row for row in read_policy_objects(driver, manifest) if row['id'] == source['id'])
    assert edited['editor'] == 'source' and edited['status'] == 'changed'
    assert edited['configured_address'] == source['configured_address']
    assert destination['configured_address']
    assert manifest == before


def test_edited_bundle_stays_owned_and_can_be_withdrawn_without_touching_manual_objects():
    driver = MemoryROS()
    release(driver)
    foreign = {'.id': '*foreign', 'list': 'Manual_List', 'address': '9.9.9.9/32'}
    driver.rows[ADDRESSES].append(copy.deepcopy(foreign))
    desired = desired_for(driver, cidrs=['1.1.1.0/24'])
    desired['ros_policy']['sources'] = ['192.168.0.105/32']
    _, plan = inspect(driver, desired)
    assert not plan['blockers'] and execute(plan, driver, None)['ok']
    objects = read_policy_objects(driver, driver.ros_policy_manifest)
    assert any(row['editor'] == 'source' and row['configured_address'] == '192.168.0.105' for row in objects)
    assert any(row['editor'] == 'destination' and row['configured_address'] == '1.1.1.0/24' for row in objects)
    withdrawn = desired_for(driver, selected=False)
    withdrawn['proxy_cidrs'] = [row for row in withdrawn['proxy_cidrs'] if row['sources'] == ['fake-ip']]
    _, plan = inspect(driver, withdrawn)
    assert not plan['blockers'] and execute(plan, driver, None)['ok']
    assert driver.rows[ADDRESSES] == [foreign]
    assert read_policy_objects(driver, driver.ros_policy_manifest) == []
