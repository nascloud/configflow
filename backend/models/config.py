"""配置数据模型"""
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, asdict


@dataclass
class Subscription:
    """订阅配置"""
    id: str
    name: str
    url: str
    type: str  # mihomo, surge, universal
    enabled: bool = True


@dataclass
class ProxyNode:
    """代理节点"""
    id: str
    name: str
    type: str  # ss, ssr, vmess, trojan, hysteria, hysteria2, etc
    server: str
    port: int
    params: Dict[str, Any]  # 其他协议特定参数


@dataclass
class Rule:
    """规则配置"""
    id: str
    rule_type: str  # DOMAIN, DOMAIN-SUFFIX, DOMAIN-KEYWORD, IP-CIDR, GEOIP, RULE-SET
    value: str  # 域名、IP或URL
    policy: str  # 策略名称
    enabled: bool = True


@dataclass
class RuleSet:
    """规则集"""
    id: str
    name: str
    url: str  # 远程规则集URL
    behavior: str  # domain, ipcidr, classical
    enabled: bool = True


@dataclass
class ProxyGroup:
    """策略组"""
    id: str
    name: str
    type: str  # select, url-test, fallback, load-balance
    proxies: List[str]  # 节点名称列表
    url: Optional[str] = None  # 健康检查URL
    interval: Optional[int] = None


@dataclass
class Config:
    """完整配置"""
    subscriptions: List[Subscription]
    nodes: List[ProxyNode]
    rules: List[Rule]
    rule_sets: List[RuleSet]
    proxy_groups: List[ProxyGroup]

    def to_dict(self):
        return asdict(self)
