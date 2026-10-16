/** Presentation categories only: changing a category never changes native YAML. */
export const oxidnsFeatureCategories = [
  {
    id: 'runtime',
    label: '运行设置',
    help: '管理工作线程、日志、管理 API 和 WebUI。DNS 查询监听在“监听服务”中配置。',
  },
  {
    id: 'listeners',
    label: '监听服务',
    help: '接收客户端的 UDP、TCP、DoT、DoH 或 DoQ 查询，并指定处理请求的入口执行器。',
  },
  {
    id: 'upstreams',
    label: 'DNS 上游',
    help: '设置向上游 DNS 转发查询的插件。可分别创建直连、远程和备用上游，并在分流规则中引用。',
  },
  {
    id: 'cache',
    label: '缓存',
    help: '配置 DNS 响应缓存、lazy cache 和反向查询缓存。独立的探测缓存也可通过 DNS 缓存实例配置。',
  },
  {
    id: 'routing',
    label: '分流规则',
    help: '编排执行链、主备切换和匹配条件，或设置静态响应、重定向与拦截动作。',
  },
  {
    id: 'resources',
    label: '规则资源',
    help: '管理域名、IP、GeoSite、GeoIP 和 AdGuard 规则集合，以及通过 include 载入的配置文件。',
  },
  {
    id: 'features',
    label: '特色功能',
    help: '配置 IP 优选、ECS、查询记录、系统与 RouterOS 联动，以及下载、升级和定时维护等功能。',
  },
  {
    id: 'network',
    label: '网络出站',
    help: '定义可复用的域名解析器和连接代理，供 DNS 上游及 HTTP 客户端通过名称引用。',
  },
  {
    id: 'advanced',
    label: '扩展配置',
    help: '保留并编辑目录尚未识别的顶层参数或插件类型，支持自定义字段与 YAML 编辑。',
  },
] as const;

export type OxidnsFeatureCategoryId = typeof oxidnsFeatureCategories[number]['id'];

const globalCategories = new Map<string, OxidnsFeatureCategoryId>([
  ['runtime', 'runtime'],
  ['log', 'runtime'],
  ['api', 'runtime'],
  ['include', 'resources'],
  ['network', 'network'],
]);

// Keep this inventory explicit: a new catalog plugin should be consciously
// classified rather than silently inheriting an unrelated visual category.
const pluginTypesByCategory: Partial<Record<OxidnsFeatureCategoryId, readonly string[]>> = {
  listeners: ['udp_server', 'tcp_server', 'http_server', 'quic_server'],
  upstreams: ['forward'],
  cache: ['cache', 'reverse_lookup'],
  routing: [
    'sequence', 'fallback', 'hosts', 'arbitrary', 'response', 'redirect',
    'black_hole', 'drop_resp', 'sleep',
    'qname', 'question', 'qtype', 'qclass', 'client_ip', 'ptr_ip', 'resp_ip',
    'cname', 'rcode', 'has_resp', 'has_wanted_ans', 'mark', 'env', 'time',
    'random', 'rate_limiter', 'string_exp', 'any_match', '_true', '_false',
  ],
  resources: ['domain_set', 'dynamic_domain_set', 'geosite', 'adguard_rule', 'ip_set', 'geoip'],
  features: [
    'client_ip_from_ecs', 'ecs_handler', 'forward_edns0opt', 'ttl',
    'ip_selector', 'prefer_ipv4', 'prefer_ipv6', 'query_summary',
    'query_recorder', 'metrics_collector', 'debug_print', 'learn_domain',
    'http_request', 'script', 'ipset', 'nftset', 'ros_address_list', 'ros_route',
    'upgrade', 'download', 'reload_provider', 'reload', 'cron',
  ],
};

const pluginCategories = new Map<string, OxidnsFeatureCategoryId>(
  Object.entries(pluginTypesByCategory).flatMap(([category, types]) =>
    types.map(type => [type, category as OxidnsFeatureCategoryId] as const),
  ),
);

export function getOxidnsGlobalCategory(key: string): OxidnsFeatureCategoryId {
  return globalCategories.get(key) ?? 'advanced';
}

export function getOxidnsPluginCategory(type: string): OxidnsFeatureCategoryId {
  return pluginCategories.get(type) ?? 'advanced';
}
