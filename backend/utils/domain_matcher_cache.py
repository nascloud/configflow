"""Reuse local indexes and host matches, without caching Agent/traffic status."""

import copy
import hashlib
import json
import os
import threading
import time
from collections import OrderedDict

from backend.utils.rule_matcher import RuleConfigMatcher
from backend.utils.rule_utils import get_rules_dir, sanitize_rule_name

MAX_MATCHERS = 4
MAX_HOST_RESULTS = 10000
MATCHER_TTL_SECONDS = 300
_CACHE = OrderedDict()
_LOCK = threading.Lock()


class _CachedMatcher:
    def __init__(self, config):
        from backend.routes.rules import get_ruleset_content

        self.created_at = time.monotonic()
        self._lock = threading.Lock()
        self._results = OrderedDict()
        self._matcher = RuleConfigMatcher(
            config.get('rule_configs', []), config.get('rule_library', []),
            lambda item, library_rule: get_ruleset_content(item, library_rule, allow_network=False),
        )

    def match(self, host):
        # Merge concurrent misses from refreshes and the automatic probing loop.
        with self._lock:
            if host not in self._results:
                self._results[host] = self._matcher.match(host)
                if len(self._results) > MAX_HOST_RESULTS:
                    self._results.popitem(last=False)
            self._results.move_to_end(host)
            return copy.deepcopy(self._results[host])


def _signature(config, rules_dir):
    rules = config.get('rule_configs', [])
    library = {item.get('id'): item for item in config.get('rule_library', [])}
    referenced = []
    files = []
    for item in rules:
        if (item.get('itemType') != 'ruleset' or not item.get('enabled', True)
                or not item.get('library_enabled', True)):
            continue
        source = library.get(item.get('library_rule_id'))
        referenced.append(source)
        name = (source or {}).get('name') or item.get('name')
        if not name:
            continue
        path = os.path.join(rules_dir, f'{sanitize_rule_name(name)}.list')
        try:
            stat = os.stat(path)
            stamp = (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        except OSError:
            stamp = None
        files.append((path, stamp))
    payload = json.dumps([rules, referenced, files], sort_keys=True, ensure_ascii=False).encode('utf8')
    return hashlib.sha256(payload).digest()


def local_matcher(config, profile_id):
    # Isolate repositories/profiles; configuration and cached-file edits also
    # invalidate negative matches immediately. TTL bounds transient read errors.
    rules_dir = os.path.realpath(get_rules_dir())
    slot = (rules_dir, profile_id)
    signature = _signature(config, rules_dir)
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(slot)
        if (cached is None or cached[0] != signature
                or now - cached[1].created_at >= MATCHER_TTL_SECONDS):
            cached = (signature, _CachedMatcher(config))
            _CACHE[slot] = cached
        _CACHE.move_to_end(slot)
        while len(_CACHE) > MAX_MATCHERS:
            _CACHE.popitem(last=False)
        return cached[1]
