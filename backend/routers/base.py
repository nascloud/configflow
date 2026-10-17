"""Common types for upstream router drivers.

Drivers probe the device and return the objects relevant to side-gateway
deployments, normalized to a vendor-neutral shape so the drift report does not
need to know router specifics. Writable drivers apply release operations (see
``backend.routers.plan``) one at a time and return the operation that undoes it.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Prefix used by generated deployments. Unmarked objects are only adopted
# during a release or changed through an explicitly confirmed manual edit.
MANAGED_PREFIX = 'cf'


class RouterError(Exception):
    """A router request failed; the message is safe to show to users."""


class RouterConflictError(RouterError):
    """The route changed or cannot be edited; a fresh preview is required."""


class RouterRollbackError(RouterError):
    """A driver could not fully compensate its partially applied operation."""


@dataclass
class RouterInfo:
    vendor: str
    version: str
    model: str = ''
    capabilities: List[str] = field(default_factory=list)


@dataclass
class RouteEntry:
    id: str
    dst: str            # CIDR, e.g. 198.18.0.0/16
    gateway: str
    enabled: bool
    comment: str = ''


@dataclass
class PolicyRouteEntry:
    """A source-aware forwarding rule (iKuai 端口分流 with a next-hop gateway).

    Static routes match destinations only, so the gateway's own traffic to a
    proxy CIDR would be routed straight back to it. A policy route can exclude
    the gateway as a source and avoid that loop.
    """
    id: str
    dst: List[str]
    gateway: str        # next hop; empty when the rule targets a WAN line
    enabled: bool
    comment: str = ''
    src: List[str] = field(default_factory=list)
    src_invert: bool = False
    dst_invert: bool = False
    protocol: str = ''


@dataclass
class NatBypassEntry:
    """A rule that exempts traffic from source NAT (iKuai 'filter', RouterOS srcnat accept)."""
    id: str
    dst: List[str]      # destination CIDRs/ranges; empty means any
    src: List[str]
    enabled: bool
    comment: str = ''
    # Vendor matchers used to preserve source scopes and detect preview drift.
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DhcpDnsEntry:
    id: str
    network: str
    dns: List[str]
    enabled: bool = True
    attributes: Dict[str, Any] = field(default_factory=dict)
    blockers: List[str] = field(default_factory=list)
    # False when a vendor's empty DNS slot could inherit an unlisted server.
    dns_explicit: bool = True


@dataclass
class DhcpDnsState:
    entries: List[DhcpDnsEntry] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RouterState:
    routes: List[RouteEntry] = field(default_factory=list)
    nat_bypass: List[NatBypassEntry] = field(default_factory=list)
    # Named address sets (iKuai route objects / RouterOS address-lists) -> CIDRs.
    address_sets: Dict[str, List[str]] = field(default_factory=dict)
    # ROS forwarding/NAT evidence. None keeps legacy drivers' coverage checks;
    # an empty rules list explicitly means there are no source-NAT rules.
    nat_context: Optional[Dict[str, Any]] = None
    dhcp_dns: Optional[DhcpDnsState] = None
    # None: the vendor routes proxy CIDRs with static routes. A list (possibly
    # empty) means proxy CIDRs are routed by policy rules excluding the gateway.
    policy_routes: Optional[List[PolicyRouteEntry]] = None


class RouterDriver:
    vendor = ''
    # False: router stages are exported as a script for the user to import.
    supports_write = False

    def probe(self) -> RouterInfo:
        raise NotImplementedError

    def read_state(self) -> RouterState:
        raise NotImplementedError

    def read_dhcp_dns(self) -> DhcpDnsState:
        """Read DHCP DNS only when the user selected a DNS Agent."""
        raise RouterError(f'{self.vendor} 不支持 DHCP DNS 下发')

    def apply(self, op: Dict[str, Any]) -> Dict[str, Any]:
        """Apply one release operation; return the operation that reverts it."""
        raise RouterError(f'{self.vendor} 不支持直接写入')

    def render_script(self, ops: List[Dict[str, Any]]) -> str:
        return ''

    def close(self) -> None:
        pass


def is_managed(comment: Optional[str]) -> bool:
    text = (comment or '').strip().lower()
    return text == MANAGED_PREFIX or text.startswith(MANAGED_PREFIX + ':') or text.startswith(MANAGED_PREFIX + '-')


def managed_comment(comment: Optional[str]) -> str:
    """Tag a comment as managed, keeping what the user wrote: ``tg`` -> ``cf-tg``.

    ``-`` rather than ``:``: router UIs restrict comment characters and every
    existing comment proves ``-`` is accepted.
    """
    text = (comment or '').strip()
    return text if is_managed(text) else (f'{MANAGED_PREFIX}-{text}' if text else MANAGED_PREFIX)


# Policy routes for the proxy gateway: derived CIDRs are fully managed; CIDRs
# migrated from hand-made static routes are only ever added, never removed.
POLICY_AUTO_COMMENT = f'{MANAGED_PREFIX}-mihomo'
POLICY_MANUAL_COMMENT = f'{MANAGED_PREFIX}-mihomo-manual'


def policy_route_ok(entry: 'PolicyRouteEntry', gateway: str) -> bool:
    """Enabled, any protocol, every source except the gateway, next hop the gateway."""
    return (entry.enabled and bool(gateway) and entry.gateway == gateway and entry.src == [gateway]
            and entry.src_invert and not entry.dst_invert and entry.protocol == 'any')


def policy_route(state: 'RouterState', comment: str) -> Optional['PolicyRouteEntry']:
    """The first policy route carrying ``comment``; duplicates are left alone."""
    return next((entry for entry in state.policy_routes or [] if entry.comment == comment), None)


def as_dict(value: Any) -> Any:
    from dataclasses import asdict, is_dataclass
    return asdict(value) if is_dataclass(value) else value
