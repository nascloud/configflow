"""Common types for upstream router drivers.

Drivers are read-only in P0: they probe the device and return the objects
relevant to side-gateway deployments, normalized to a vendor-neutral shape so
the drift report does not need to know router specifics.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Prefix written into comment/tagname of objects ConfigFlow manages. In P0 it
# is only used to classify existing objects as managed or adoptable.
MANAGED_PREFIX = 'cf'


class RouterError(Exception):
    """A router request failed; the message is safe to show to users."""


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
class NatBypassEntry:
    """A rule that exempts traffic from source NAT (iKuai 'filter', RouterOS srcnat accept)."""
    id: str
    dst: List[str]      # destination CIDRs/ranges; empty means any
    src: List[str]
    enabled: bool
    comment: str = ''


@dataclass
class RouterState:
    routes: List[RouteEntry] = field(default_factory=list)
    nat_bypass: List[NatBypassEntry] = field(default_factory=list)
    # Named address sets (iKuai route objects / RouterOS address-lists) -> CIDRs.
    address_sets: Dict[str, List[str]] = field(default_factory=dict)


class RouterDriver:
    vendor = ''

    def probe(self) -> RouterInfo:
        raise NotImplementedError

    def read_state(self) -> RouterState:
        raise NotImplementedError

    def close(self) -> None:
        pass


def is_managed(comment: Optional[str]) -> bool:
    text = (comment or '').strip().lower()
    return text == MANAGED_PREFIX or text.startswith(MANAGED_PREFIX + ':') or text.startswith(MANAGED_PREFIX + '-')


def as_dict(value: Any) -> Any:
    from dataclasses import asdict, is_dataclass
    return asdict(value) if is_dataclass(value) else value
