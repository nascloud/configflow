"""Equivalent RouterOS wire representations, scoped to known resource fields.

Unknown fields, missing values, interface-qualified gateways and non-main
lookup tables remain unchanged. Ownership baselines keep the original rows.
"""
import ipaddress


def comparable_values(path, row):
    result = dict(row)
    field = {'/routing/table': 'fib', '/ip/firewall/mangle': 'passthrough'}.get(path)
    if field in result:
        value = result[field]
        # fib is a presence flag, unlike ordinary yes/no boolean properties.
        if value is True or value in ('true', 'yes') or (field == 'fib' and value == ''):
            result[field] = 'true'
        elif value is False or value in ('false', 'no'):
            result[field] = 'false'
    if path == '/ip/route' and isinstance(result.get('gateway'), str):
        gateway = result['gateway'].removesuffix('@main')
        try:
            # An omitted lookup table means main. Restrict this equivalence to
            # one IPv4 next hop; never erase an interface or another table.
            result['gateway'] = str(ipaddress.IPv4Address(gateway))
        except ValueError:
            pass
    return result
