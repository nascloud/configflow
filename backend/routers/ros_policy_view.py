"""Read the recorded policy objects without adopting objects by their names."""
from backend.routers.ros_policy import ADDRESSES, MANGLE, NAT, ROUTES, TABLES, config_row
from backend.routers.ros_values import comparable_values

KINDS = {ADDRESSES: 'addresses', MANGLE: 'mangle', TABLES: 'tables', ROUTES: 'routes', NAT: 'nat'}
PUBLIC_FIELDS = {
    'list', 'address', 'name', 'fib', 'chain', 'action', 'src-address', 'dst-address',
    'src-address-list', 'dst-address-list', 'dst-address-type', 'routing-mark',
    'new-routing-mark', 'jump-target', 'passthrough', 'routing-table', 'gateway',
    'immediate-gw', 'distance', 'scope', 'target-scope', 'in-interface', 'out-interface',
    'disabled', 'active', 'invalid', 'comment', 'protocol', 'src-port', 'dst-port',
    'packets', 'bytes',
}


def read_policy_objects(driver, manifest):
    resources = (manifest or {}).get('resources', [])
    # No manifest means nothing has been claimed. Names/comments alone are never
    # proof of ownership, including objects from another configuration space.
    live = {path: {row['.id']: row for row in driver._list(path)}
            for path in KINDS if any(entry['path'] == path for entry in resources)}
    result = []
    for entry in resources:
        if entry['path'] not in KINDS:
            continue
        row = live[entry['path']].get(entry['id'])
        status = 'missing' if row is None else 'matched' if config_row(row) == config_row(entry['config']) else 'changed'
        shown = entry['config'] if row is None else row
        # A present-empty fib flag means enabled; expose it consistently to the
        # UI without changing the raw ownership baseline or other field values.
        if entry['path'] == TABLES:
            shown = comparable_values(TABLES, shown)
        result.append({'id': entry['id'], 'kind': KINDS[entry['path']], 'status': status,
                       'fields': {key: value for key, value in shown.items() if key in PUBLIC_FIELDS}})
    return result
