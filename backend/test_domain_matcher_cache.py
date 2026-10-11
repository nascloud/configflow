"""Domain discovery cache correctness under edits and concurrent refreshes."""
import copy
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.utils import domain_matcher_cache as cache


@pytest.fixture
def rules(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, 'get_rules_dir', lambda: str(tmp_path))
    cache._CACHE.clear()
    config = {
        'rule_configs': [
            {'id': 'first', 'itemType': 'rule', 'rule_type': 'DOMAIN-SUFFIX',
             'value': 'first.example', 'policy': 'DIRECT'},
            {'id': 'set', 'itemType': 'ruleset', 'library_rule_id': 'lib', 'name': 'domains', 'policy': 'PROXY'},
        ],
        'rule_library': [{'id': 'lib', 'name': 'domains', 'source_type': 'content',
                          'content': 'DOMAIN-SUFFIX,matched.example\n'}],
    }
    # Use the same local-file/inline precedence as production, without a repository.
    from backend.routes import rules as route
    def content(item, library_rule, allow_network=True):
        assert allow_network is False
        path = tmp_path / f"{library_rule['name']}.list"
        return path.read_text() if path.exists() else library_rule.get('content', '')
    monkeypatch.setattr(route, 'get_ruleset_content', content)
    yield config, tmp_path
    cache._CACHE.clear()


def test_reuses_positive_and_negative_matches_without_caching_traffic(rules, monkeypatch):
    config, _ = rules
    calls = []
    original = cache.RuleConfigMatcher.match
    def match(self, host):
        calls.append(host)
        return original(self, host)
    monkeypatch.setattr(cache.RuleConfigMatcher, 'match', match)
    first = cache.local_matcher(config, 'default')
    result = first.match('www.matched.example')
    result['policy'] = 'corrupted'
    assert first.match('missing.example') is None
    changed = copy.deepcopy(config)
    changed['agents'] = [{'last_heartbeat': 'new'}]
    changed['domain_discovery'] = {'ignored': ['ignored.example']}
    second = cache.local_matcher(changed, 'default')
    assert second is first
    assert second.match('www.matched.example')['policy'] == 'PROXY'
    assert second.match('missing.example') is None
    assert calls == ['www.matched.example', 'missing.example']


@pytest.mark.parametrize('edit', ['inline', 'policy', 'enabled', 'order'])
def test_config_edits_invalidate_immediately(rules, edit):
    config, _ = rules
    config['rule_library'][0]['content'] += 'DOMAIN-SUFFIX,first.example\n'
    old = cache.local_matcher(config, 'default')
    assert old.match('new.example') is None
    assert old.match('first.example')['policy'] == 'DIRECT'
    changed = copy.deepcopy(config)
    if edit == 'inline':
        changed['rule_library'][0]['content'] += 'DOMAIN,new.example\n'
    elif edit == 'policy':
        changed['rule_configs'][0]['policy'] = 'PROXY'
    elif edit == 'enabled':
        changed['rule_configs'][0]['enabled'] = False
    else:
        changed['rule_configs'].reverse()
    new = cache.local_matcher(changed, 'default')
    assert new is not old
    assert new.match('new.example' if edit == 'inline' else 'first.example')['policy'] == 'PROXY'


def test_file_creation_replacement_and_deletion_invalidate_negative_matches(rules):
    config, root = rules
    old = cache.local_matcher(config, 'default')
    assert old.match('remote.example') is None
    path = root / 'domains.list'
    path.write_text('DOMAIN,remote.example\n')
    created = cache.local_matcher(config, 'default')
    assert created is not old
    assert created.match('remote.example')['policy'] == 'PROXY'
    assert created.match('newone.example') is None
    stat = path.stat()
    replacement = root / 'replacement'
    replacement.write_text('DOMAIN,newone.example\n')
    os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    replacement.replace(path)
    replaced = cache.local_matcher(config, 'default')
    assert replaced is not created
    assert replaced.match('remote.example') is None
    assert replaced.match('newone.example')['policy'] == 'PROXY'
    path.unlink()
    removed = cache.local_matcher(config, 'default')
    assert removed.match('newone.example') is None
    assert removed.match('matched.example')['policy'] == 'PROXY'


def test_parallel_refreshes_compute_each_host_once(rules, monkeypatch):
    config, _ = rules
    original = cache.RuleConfigMatcher.match
    entered, release = threading.Event(), threading.Event()
    calls = []
    def match(self, host):
        calls.append(host)
        entered.set()
        assert release.wait(3)
        return original(self, host)
    monkeypatch.setattr(cache.RuleConfigMatcher, 'match', match)
    def refresh():
        matcher = cache.local_matcher(copy.deepcopy(config), 'default')
        return matcher.match('matched.example'), matcher.match('missing.example')
    with ThreadPoolExecutor(max_workers=8) as pool:
        tasks = [pool.submit(refresh) for _ in range(8)]
        try:
            assert entered.wait(3)
        finally:
            release.set()
        results = [task.result(timeout=3) for task in tasks]
    assert all(result == results[0] for result in results)
    assert calls == ['matched.example', 'missing.example']


def test_cache_is_bounded_and_profiles_are_isolated(rules, monkeypatch):
    config, _ = rules
    monkeypatch.setattr(cache, 'MAX_MATCHERS', 2)
    monkeypatch.setattr(cache, 'MAX_HOST_RESULTS', 2)
    first = cache.local_matcher(config, 'first')
    second = cache.local_matcher(config, 'second')
    assert first is not second
    cache.local_matcher(config, 'third')
    assert len(cache._CACHE) == 2
    assert cache.local_matcher(config, 'first') is not first
    for host in ['a.example', 'b.example', 'c.example']:
        first.match(host)
    assert list(first._results) == ['b.example', 'c.example']


def test_ttl_retries_transient_read_failures(rules, monkeypatch):
    config, _ = rules
    now = [1.0]
    monkeypatch.setattr(cache.time, 'monotonic', lambda: now[0])
    first = cache.local_matcher(config, 'default')
    now[0] += cache.MATCHER_TTL_SECONDS
    assert cache.local_matcher(config, 'default') is not first
