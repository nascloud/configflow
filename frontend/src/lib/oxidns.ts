export interface OxidnsField {
  path: string
  label: string
  type?: 'text' | 'number' | 'password' | 'switch' | 'tls' | 'textarea' | 'list' | 'numbers' | 'upstreams' | 'lines'
  when?: string
  hint?: string
  placeholder?: string
  min?: number
  max?: number
  rows?: number
  wide?: boolean
  emptyHint?: string
  options?: Array<{ value: string; label: string }>
}

const number = (path: string, label: string, min = 1, hint?: string): OxidnsField => ({ path, label, type: 'number', min, hint })
const toggle = (path: string, label: string, hint?: string): OxidnsField => ({ path, label, type: 'switch', hint })
const text = (path: string, label: string, hint?: string): OxidnsField => ({ path, label, hint })
const select = (path: string, label: string, values: string[]): OxidnsField => ({ path, label, options: values.map(value => ({ value, label: value })) })

export const coreFields: OxidnsField[] = [
  text('listen', 'DNS 监听地址', 'UDP 与 TCP 共用地址，例如 0.0.0.0:53。'),
  { ...number('worker_threads', '工作线程数'), max: 256, hint: '已验证模板使用 2 个线程。' },
  toggle('tcp_enabled', '启用 TCP DNS'), toggle('prefer_ipv4', '优先 IPv4'),
  toggle('metrics_enabled', '轻量指标'), toggle('aggregate_rules', '合并相邻同策略规则', '保留直连 / 代理的原始优先级顺序。'),
  { path: 'block_qtypes', label: '屏蔽查询类型', type: 'numbers', wide: true, hint: '65 为 HTTPS，255 为 ANY。删除全部条目则不屏蔽。' },
  { path: 'default_forward', label: '未匹配时转发到', options: [{ value: 'forward_remote', label: '代理 DNS' }, { value: 'forward_local', label: '直连 DNS' }] }
]

export const upstreamFields: OxidnsField[] = [
  { path: 'local_dns', label: '直连 DNS', type: 'upstreams', wide: true, placeholder: 'udp://223.5.5.5:53', emptyHint: '未配置条目，使用默认直连上游。', hint: '逐条添加地址，可展开设置 bootstrap、dial_addr 等参数；YAML 模式支持完整上游配置。' },
  number('local_concurrent', '直连并发数'),
  { path: 'remote_dns', label: '代理 DNS / 外部 Fake-IP 上游', type: 'upstreams', wide: true, placeholder: 'udp://192.0.2.53:1053', emptyHint: '未配置条目，使用默认代理上游。', hint: 'Fake-IP 由外部 DNS 提供。加密上游域名应配置 bootstrap 或 dial_addr，避免解析环路。' },
  number('remote_concurrent', '代理并发数'), number('remote_ttl', '代理响应 TTL（秒）', 0),
  { path: 'fallback_dns', label: '备用 DNS', type: 'upstreams', wide: true, emptyHint: '未配置条目，继承直连 DNS。' },
  number('fallback_concurrent', '备用并发数'), number('fallback_threshold', '触发备用等待时间（毫秒）'),
  toggle('always_standby', '同时请求备用上游')
]

export const cacheFields: OxidnsField[] = [
  toggle('cache_enabled', '启用主缓存'), number('cache_size', '主缓存条目数'),
  number('cache_lazy_ttl', 'Lazy cache 总保留时长（秒）', 0, '0 为关闭；300 表示从写入起至少保留 300 秒，并非在原 TTL 后再加 300 秒。原 TTL 更长时保留原 TTL。'),
  toggle('probe_cache_enabled', '启用国内探测缓存', '独立缓存国内探测响应，返回主序列后继续判断 IP 分流；不启用 lazy cache。'),
  number('probe_cache_size', '探测缓存条目数'),
  toggle('cache_dump_enabled', '缓存持久化', '默认关闭。启用前请确认当前 OxiDNS 版本恢复缓存时的 TTL 行为。'),
  text('cache_dump_file', '缓存文件', '相对于配置目录。'), number('cache_dump_interval', '持久化间隔（秒）')
]

export const apiFields: OxidnsField[] = [
  toggle('api.enabled', '启用 API / WebUI'), text('api.listen', 'API 监听地址', '局域网访问填写 0.0.0.0:9199，并设置用户名与密码。'),
  text('api.username', '用户名'), { path: 'api.password', label: '密码', type: 'password' },
  { ...text('api.webui_root', 'WebUI 静态文件目录', 'Docker 镜像内置目录为 /usr/share/oxidns/webui；通过桥接网络访问还需映射 API 端口。'), wide: true },
  text('api.ssl.cert', 'API TLS 证书路径'), { path: 'api.ssl.key', label: 'API TLS 私钥路径', type: 'password' },
  select('log.level', '日志级别', ['off', 'error', 'warn', 'info', 'debug', 'trace']), text('log.file', '日志文件', '留空输出到服务日志。'),
  select('log.rotation', '日志轮转', ['never', 'minutely', 'hourly', 'daily', 'weekly']), number('log.max_files', '保留日志文件数', 0)
]

const rosFields = (name: string): OxidnsField[] => [
  text(`${name}.args.address`, 'RouterOS API 地址', '例如 192.168.0.1:8728；TLS API 通常为 8729。'),
  text(`${name}.args.username`, '用户名'), { path: `${name}.args.password`, label: '密码', type: 'password' },
  { path: `${name}.args.tls`, label: '使用 TLS', type: 'tls' },
  { ...text(`${name}.args.tls.server_name`, 'TLS 服务器名称'), when: `${name}.args.tls` },
  { ...text(`${name}.args.tls.ca`, 'TLS CA 文件路径', '留空使用系统证书；配置 CA 时不能跳过证书验证。'), when: `${name}.args.tls` },
  { ...toggle(`${name}.args.tls.insecure`, '跳过 TLS 证书验证'), when: `${name}.args.tls` },
  toggle(`${name}.args.async`, '异步写入'),
  number(`${name}.args.min_ttl`, '最小 TTL（秒）', 0), number(`${name}.args.max_ttl`, '最大 TTL（秒）', 0),
  number(`${name}.args.queue_capacity`, '队列容量'), text(`${name}.args.comment_prefix`, '注释前缀'), toggle(`${name}.args.cleanup_on_shutdown`, '正常停止时清理临时条目')
]

export const featureGroups: Array<{ key: string; title: string; hint: string; fields: OxidnsField[] }> = [
  { key: 'query_recorder', title: '查询记录', hint: 'SQLite 查询历史、执行路径与耗时。启用后增加磁盘写入；数据存放于 state/，部署保留。', fields: [
    text('query_recorder.args.path', '数据库路径'), number('query_recorder.args.retention_days', '保留天数'),
    number('query_recorder.args.queue_size', '写入队列'), number('query_recorder.args.batch_size', '每批条数'),
    number('query_recorder.args.flush_interval_ms', '刷新间隔（毫秒）'), number('query_recorder.args.memory_tail', '内存最近记录数', 0),
    number('query_recorder.args.cleanup_interval_hours', '清理间隔（小时）'), number('query_recorder.args.reader_concurrency', '读取并发数')
  ] },
  { key: 'ip_selector', title: 'IP 优选（实验性）', hint: '只应用于直连的真实 IP 响应；代理 / Fake-IP 分支不探测。后台模式配合 top_n=0 仅调整顺序。', fields: [
    select('ip_selector.args.selection_mode', '探测模式', ['background', 'first_success', 'best_within_budget']),
    number('ip_selector.args.top_n', '保留 IP 数量', 0, '0 保留全部地址，仅按探测结果排序。'),
    { path: 'ip_selector.args.probe_methods', label: '探测方法', type: 'list', wide: true, hint: '例如 tcp:443、tcp:80、ping。' },
    select('ip_selector.args.dnssec_policy', 'DNSSEC 策略', ['reorder_only', 'skip']),
    number('ip_selector.args.probe_stagger', '探测间隔（毫秒）', 0), number('ip_selector.args.probe_timeout', '探测超时（毫秒）'),
    number('ip_selector.args.max_wait', '等待预算（毫秒）'), number('ip_selector.args.max_parallel_probes', '最大探测并发'),
    toggle('ip_selector.args.cache.enabled', '缓存探测结果'), number('ip_selector.args.cache.size', '探测结果缓存条数'),
    number('ip_selector.args.cache.ttl', '探测成功缓存（秒）'), number('ip_selector.args.cache.failure_ttl', '探测失败缓存（秒）')
  ] },
  { key: 'dynamic_domain_set', title: '动态域名集与学习（实验性）', hint: '可通过 OxiDNS API 更新域名；启用学习后从已归类的响应学习，可能固定后续分流结果。运行数据保留在 state/。', fields: [
    text('dynamic_domain_set.args.path', '动态规则文件'), { path: 'dynamic_domain_set.route', label: '动态规则转发到', options: [{ value: 'direct', label: '直连 DNS' }, { value: 'proxy', label: '代理 DNS' }] },
    { path: 'dynamic_domain_set.args.bootstrap_rules', label: '初始规则', type: 'list', wide: true, hint: '例如 full:example.com 或 domain:example.com。' },
    number('dynamic_domain_set.args.queue_size', '写入队列'), number('dynamic_domain_set.args.batch_size', '每批条数'), number('dynamic_domain_set.args.flush_interval_ms', '刷新间隔（毫秒）'),
    toggle('dynamic_domain_set.learn', '启用自动学习'), select('dynamic_domain_set.learn_args.phase', '学习阶段', ['after', 'before']),
    select('dynamic_domain_set.learn_args.questions', '学习问题范围', ['first', 'all']),
    { path: 'dynamic_domain_set.learn_args.qtypes', label: '学习查询类型', type: 'list', wide: true, hint: '例如 A、AAAA。' },
    select('dynamic_domain_set.learn_args.rule_kind', '学习规则类型', ['full', 'domain']), toggle('dynamic_domain_set.learn_args.success_only', '仅学习成功响应'),
    toggle('dynamic_domain_set.learn_args.answer_required', '要求响应中含答案'), toggle('dynamic_domain_set.learn_args.async', '异步学习'),
    select('dynamic_domain_set.learn_args.error_mode', '学习失败行为', ['continue', 'stop', 'fail']), text('dynamic_domain_set.learn_args.timeout', '学习超时')
  ] },
  { key: 'adguard', title: 'AdGuard DNS 过滤', hint: '支持 DNS 规则子集；不支持网页元素过滤、URL 路径、dnsrewrite 或 $client。规则文件包含在完整下载包中。', fields: [
    { path: 'adguard.content', label: '自定义规则', type: 'textarea', wide: true, rows: 5, placeholder: '||ads.example.com^\n@@||allowed.example.com^' },
    { path: 'adguard.urls', label: '规则订阅', type: 'list', wide: true, hint: '每个条目填写一个 HTTP(S) 地址。' },
    select('adguard.mode', '拦截响应', ['nxdomain', 'nodata', 'null', 'refused'])
  ] },
  { key: 'ros_address_list', title: 'RouterOS 地址列表', hint: '只写入直连分支的真实 A / AAAA 地址。需要 RouterOS API 权限及已有的地址列表策略。', fields: [
    ...rosFields('ros_address_list'), text('ros_address_list.args.address_list4', 'IPv4 地址列表'), text('ros_address_list.args.address_list6', 'IPv6 地址列表')
  ] },
  { key: 'ros_route', title: 'RouterOS 主机路由', hint: '只为直连分支的真实 IP 创建主机路由。路由表和网关须先在 RouterOS 中配置。', fields: [
    ...rosFields('ros_route'), text('ros_route.args.routing_table', '已有路由表'), text('ros_route.args.gateway4', 'IPv4 网关'),
    text('ros_route.args.gateway6', 'IPv6 网关'), number('ros_route.args.distance', '路由距离')
  ] },
  { key: 'rule_update', title: '原生规则定时更新', hint: '生成 cron、download 与 provider reload 任务。逐文件更新成功后重载，下载失败的文件保留旧版，不提供整批回滚。与外部维护脚本同时更新同一规则目录会冲突，请只启用一套更新方式。', fields: [
    text('rule_update.args.schedule', 'Cron（五段）', '例如 30 3 * * *，每天 03:30。'), text('rule_update.args.timezone', '时区'),
    text('rule_update.args.timeout', '下载超时'), text('rule_update.args.outbound', '出站配置标签'), text('rule_update.args.socks5', 'SOCKS5 代理')
  ] }
]

export const applyVerifiedOxidnsTemplate = (settings: Record<string, any>) => Object.assign(settings, {
  worker_threads: 2, aggregate_rules: true, metrics_enabled: true,
  local_concurrent: 2, remote_concurrent: 1, fallback_concurrent: 2,
  fallback_threshold: 500, always_standby: false, remote_ttl: 1,
  cache_enabled: true, cache_size: 10240, cache_lazy_ttl: 300, cache_dump_enabled: false,
  probe_cache_enabled: true, probe_cache_size: 2048
})
