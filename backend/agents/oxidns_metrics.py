"""Normalize the bounded, read-only OxiDNS Prometheus metrics snapshot.

OxiDNS 1.6.1 exports unprefixed names; prefixed aliases are accepted without
counting duplicate series twice. Missing or invalid observations stay unknown.
Counters describe the current plugin lifetime, not a fixed historical window.
"""
import math
import re

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_SAMPLES = 10_000
MAX_LINE_LENGTH = 16 * 1024
MAX_LABELS = 32

_SERVER_FIELDS = {
    'server_request_total': 'requests',
    'server_failed_total': 'failed',
    'server_inflight': 'inflight',
    'server_latency_sum_ms': 'latency_sum_ms',
    'server_latency_count': 'latency_count',
}
_CACHE_FIELDS = {
    'cache_lookup_total': 'lookups',
    'cache_miss_total': 'misses',
    'cache_expired_total': 'expired',
    'cache_entry_count': 'entries',
}
_FAMILIES = set(_SERVER_FIELDS) | set(_CACHE_FIELDS) | {'cache_hit_total'}
_SAMPLE = re.compile(
    r'(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)'
    r'(?:\{(?P<labels>.*)\})?[ \t]+'
    r'(?P<value>\S+)(?:[ \t]+[+-]?\d+)?[ \t]*'
)
_LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)[ \t]*=[ \t]*"((?:[^"\\]|\\["\\n])*)"')
_ESCAPE = re.compile(r'\\(["\\n])')
_NUMBER = re.compile(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?')


def _labels(text):
    if text is None:
        return {}
    labels = {}
    position = 0
    while position < len(text):
        while position < len(text) and text[position] in ' \t':
            position += 1
        if position == len(text):
            break
        match = _LABEL.match(text, position)
        if not match:
            return None
        name, value = match.groups()
        if name in labels or len(labels) >= MAX_LABELS:
            return None
        labels[name] = _ESCAPE.sub(lambda m: '\n' if m[1] == 'n' else m[1], value)
        position = match.end()
        while position < len(text) and text[position] in ' \t':
            position += 1
        if position == len(text):
            break
        if text[position] != ',':
            return None
        position += 1
    return labels


def _number(text):
    if not _NUMBER.fullmatch(text):
        return None
    try:
        value = float(text)
        if not math.isfinite(value) or value < 0:
            return None
        # Preserve integer counters exactly instead of rounding through float.
        return int(text) if re.fullmatch(r'[+-]?\d+', text) else value
    except (ValueError, OverflowError):
        return None


def _sum(values):
    values = list(values)
    if not values or any(value is None for value in values):
        return None
    try:
        result = sum(values)
        return result if math.isfinite(result) else None
    except OverflowError:
        return None


def _divide(numerator, denominator, *, percentage=False):
    if numerator is None or denominator is None or denominator == 0:
        return None
    # Contradictory snapshots must not become a hit rate above 100%.
    if percentage and numerator > denominator:
        return None
    try:
        result = numerator / denominator
        if percentage:
            result *= 100
        return result if math.isfinite(result) else None
    except OverflowError:
        return None


def _observe(group, field, value):
    # Both aliases, or duplicate samples, represent one observation. Conflicts
    # remain unknown, including a valid value paired with a nonfinite sample.
    if field in group and group[field] != value:
        group[field] = None
    else:
        group[field] = value


def summarize_metrics(text):
    """Return metrics/caches/servers; reject oversized input with ValueError.

    Malformed samples are ignored. Recognizable nonfinite/negative values stay
    None, as do missing series. Aggregates require a known value for every
    observed listener/cache, so incomplete snapshots cannot imply zero traffic.
    """
    if not isinstance(text, str):
        raise ValueError('OxiDNS metrics must be text')
    if len(text) > MAX_INPUT_BYTES:
        raise ValueError('OxiDNS metrics exceed size limit')
    try:
        if len(text.encode('utf-8')) > MAX_INPUT_BYTES:
            raise ValueError('OxiDNS metrics exceed size limit')
    except UnicodeEncodeError:
        raise ValueError('OxiDNS metrics contain invalid text') from None

    server_values, cache_values = {}, {}
    sample_count = 0
    for raw in text.splitlines():
        if len(raw) > MAX_LINE_LENGTH:
            raise ValueError('OxiDNS metrics line exceeds size limit')
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        sample_count += 1
        if sample_count > MAX_SAMPLES:
            raise ValueError('OxiDNS metrics exceed sample limit')
        match = _SAMPLE.fullmatch(line)
        if not match:
            continue
        family = match['name']
        if family.startswith('oxidns_'):
            family = family[len('oxidns_'):]
        if family not in _FAMILIES:
            continue
        labels = _labels(match['labels'])
        if not labels or not labels.get('plugin_tag'):
            continue
        tag = labels['plugin_tag']
        value = _number(match['value'])
        if family in _SERVER_FIELDS:
            protocol = labels.get('protocol')
            if not protocol:
                continue
            group = server_values.setdefault((tag, protocol), {})
            _observe(group, _SERVER_FIELDS[family], value)
        elif family == 'cache_hit_total':
            kind = labels.get('kind')
            if kind not in ('fresh', 'stale'):
                continue
            group = cache_values.setdefault(tag, {})
            _observe(group, kind + '_hits', value)
        else:
            group = cache_values.setdefault(tag, {})
            _observe(group, _CACHE_FIELDS[family], value)

    servers = []
    for (tag, protocol), values in sorted(server_values.items()):
        servers.append({
            'tag': tag, 'protocol': protocol,
            **{field: values.get(field) for field in ('requests', 'failed', 'inflight')},
            'avg_latency_ms': _divide(values.get('latency_sum_ms'), values.get('latency_count')),
        })
    caches = []
    for tag, values in sorted(cache_values.items()):
        hits = _sum((values.get('fresh_hits'), values.get('stale_hits')))
        caches.append({
            'tag': tag,
            **{field: values.get(field) for field in (
                'lookups', 'fresh_hits', 'stale_hits', 'misses', 'expired', 'entries')},
            'hits': hits,
            'hit_rate': _divide(hits, values.get('lookups'), percentage=True),
        })
    cache_hit_rate = _divide(
        _sum(row['hits'] for row in caches),
        _sum(row['lookups'] for row in caches), percentage=True)
    if any(row['hits'] is not None and row['lookups'] is not None
           and row['hits'] > row['lookups'] for row in caches):
        cache_hit_rate = None
    return {
        'metrics': {
            'requests_total': _sum(row['requests'] for row in servers),
            'failed_total': _sum(row['failed'] for row in servers),
            'avg_latency_ms': _divide(
                _sum(row.get('latency_sum_ms') for row in server_values.values()),
                _sum(row.get('latency_count') for row in server_values.values())),
            'inflight': _sum(row['inflight'] for row in servers),
            'cache_hit_rate': cache_hit_rate,
        },
        'caches': caches,
        'servers': servers,
    }
