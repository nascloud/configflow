"""Common types for upstream router drivers.

Drivers probe the device and return the objects relevant to side-gateway
deployments, normalized to a vendor-neutral shape so the drift report does not
need to know router specifics. Writable drivers apply release operations (see
``backend.routers.plan``) one at a time and return the operation that undoes it.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Prefix written into the comment of objects ConfigFlow manages; objects without
# it are never modified except when adopted (re-commented) during a release.
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
    # False: router stages are exported as a script for the user to import.
    supports_write = False

    def probe(self) -> RouterInfo:
        raise NotImplementedError

    def read_state(self) -> RouterState:
        raise NotImplementedError

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
    """Tag a comment as managed, keeping what the user wrote: ``tg`` -> ``cf:tg``."""
    text = (comment or '').strip()
    return text if is_managed(text) else (f'{MANAGED_PREFIX}:{text}' if text else MANAGED_PREFIX)


def as_dict(value: Any) -> Any:
    from dataclasses import asdict, is_dataclass
    return asdict(value) if is_dataclass(value) else value
