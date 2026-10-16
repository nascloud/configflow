/**
 * Native configuration catalog, audited against the official OxiDNS 1.6.1 docs
 * and deserializers at the commit below. Defaults are suggestions for newly
 * added fields, never an instruction to overwrite omitted/existing values.
 */
export interface OxidnsSchema {
  type: 'string' | 'number' | 'boolean' | 'null' | 'array' | 'object';
  label?: string;
  help: string;
  properties?: Record<string, OxidnsSchema>;
  items?: OxidnsSchema;
  enum?: Array<string | number>;
  default?: unknown;
  required?: boolean;
  documentation?: string;
  additionalProperties?: OxidnsSchema;
}

export interface OxidnsPluginDefinition {
  type: string;
  category: string;
  label: string;
  help: string;
  documentation: string;
  args: OxidnsSchema;
  example?: unknown;
}

export const oxidnsSchemaVersion = '1.6.1';
export const oxidnsSchemaSource = {
  repository: 'https://github.com/svenshi/oxidns',
  commit: 'bd183f69348aa9c78c3b92bddbce28651d2bf66e',
  documentation: 'https://oxidns.org/plugin-reference/overview/',
  checkedAt: '2026-10-16',
};

const s = (label: string, help: string, value?: string): OxidnsSchema => ({ type: 'string', label, help, ...(value === undefined ? {} : { default: value }) });
const n = (label: string, help: string, value?: number): OxidnsSchema => ({ type: 'number', label, help, ...(value === undefined ? {} : { default: value }) });
const b = (label: string, help: string, value?: boolean): OxidnsSchema => ({ type: 'boolean', label, help, ...(value === undefined ? {} : { default: value }) });
const o = (label: string, help: string, properties: Record<string, OxidnsSchema>): OxidnsSchema => ({ type: 'object', label, help, properties });
const a = (label: string, help: string, items: OxidnsSchema): OxidnsSchema => ({ type: 'array', label, help, items });
const strings = (label: string, help: string): OxidnsSchema => a(label, help, s('条目', help));
const e = (label: string, help: string, values: Array<string | number>, value?: string | number): OxidnsSchema => ({ type: typeof values[0] === 'number' ? 'number' : 'string', label, help, enum: values, ...(value === undefined ? {} : { default: value }) });
const required = (schema: OxidnsSchema): OxidnsSchema => ({ ...schema, required: true });
const map = (label: string, help: string, values: OxidnsSchema = s('值', '为这个名称填写对应的字符串值。')): OxidnsSchema => ({ ...o(label, help, {}), additionalProperties: values });
const none: OxidnsSchema = { type: 'null', label: '无参数', help: '此插件不需要 args，可省略或设置为空值。', default: null };
const stop = b('命中后结束', '获得有效结果后停止后续执行器；关闭时继续按 sequence 顺序执行。', false);
const files = strings('规则文件', '规则文本文件路径；相对路径基于 OxiDNS 工作目录。可在附加文件中提供部署所需文件。');
const timeout = (value = '5s') => s('超时', '等待上限，使用带单位的时长，例如 500ms、5s、1m。', value);
const outbound = s('出站配置', '引用 network.outbound.profiles 中的名称；省略时使用默认出站。');
const socks5 = s('SOCKS5 代理', '填写 host:port 或 username:password@host:port；IPv6 地址需用方括号。局部设置覆盖出站代理。');
const insecure = b('跳过证书校验', '开启后不验证远端 TLS 证书；一般保持关闭。', false);
const errorMode = e('失败处理', 'continue 记录错误并继续，stop 结束执行链，fail 将错误交给调用方。', ['continue', 'stop', 'fail'], 'continue');
const phase = e('执行阶段', 'before 在后续执行器之前运行；after 等后续执行器完成后再运行。', ['before', 'after'], 'after');
const asyncMode = b('异步执行', '开启后投递到后台队列；关闭时当前请求会等待本次操作。', true);

/** Also reused by the template's compact upstream cards. */
export const oxidnsUpstreamSchema = o('DNS 上游', '选择协议并设置该上游的解析、代理和连接参数。', {
  addr: required(s('上游地址', '支持 UDP、TCP、DoT、DoH、DoQ 和 DoH3；例如 223.5.5.5:53 或 https://dns.example/dns-query。')),
  tag: s('日志标识', '给此上游单独命名，便于识别日志和并发查询结果。'),
  outbound,
  dial_addr: s('连接 IP', '直接连接此 IP，同时保留地址中的域名用于 SNI、Host 和证书校验；优先于 bootstrap。'),
  port: n('端口', '覆盖协议默认端口；一般直接在上游地址中指定即可。'),
  bootstrap: s('引导 DNS', '解析上游域名的 DNS 服务器，必须是 IP:端口，避免解析自身形成循环。'),
  bootstrap_version: e('引导地址族', '选择引导解析查询 A 或 AAAA；省略时使用 IPv4。', [4, 6], 4),
  socks5: { ...socks5, help: `${socks5.help} UDP、DoQ 和 DoH3 不支持该代理。` },
  idle_timeout: n('连接空闲时间（秒）', '连接池中空闲连接保留多久。', 10),
  max_conns: n('最大连接数', '连接池上限，范围 1–4096。', 64),
  min_conns: n('最小预热连接数', '保持预热的连接数，范围 0–4096，不能超过最大连接数。', 0),
  insecure_skip_verify: insecure,
  timeout: timeout(),
  enable_pipeline: b('连接流水线（enable_pipeline）', '让 TCP / DoT 单连接同时承载多个查询；需上游支持。', false),
  enable_http3: b('HTTP/3', '为 DoH 启用 HTTP/3；h3:// 地址会强制使用 HTTP/3。', false),
  so_mark: n('SO_MARK', 'Linux socket 标记，可供策略路由匹配；需要对应系统权限。'),
  bind_to_device: s('绑定网卡', 'Linux SO_BINDTODEVICE 的接口名称，例如 eth0；需要对应系统权限。'),
});
oxidnsUpstreamSchema.documentation = 'https://oxidns.org/plugin-reference/executor/resolution/#forward';

const resolver = o('域名解析器', '填写 nameservers 使用指定 DNS；也可将值类型改为字符串并填写 system，使用系统 DNS。', {
  nameservers: required(a('解析服务器', '指定用于解析出站目标的 DNS；域名型地址必须填写连接 IP，避免循环解析。', o('服务器', '解析服务器地址与实际连接 IP。', {
    addr: required(s('地址', '支持 UDP、TCP、DoT、DoH、DoQ、DoH3，未写协议时使用 UDP。')),
    dial_addr: s('连接 IP', '域名型解析服务器必须填写；地址中的域名仍用于证书验证。'),
  }))),
  ip_version: e('地址族', '4 查询 IPv4 地址，6 查询 IPv6 地址。', [4, 6], 4),
  timeout: timeout(),
  proxy: e('解析代理', 'none 直连；profile 让 TCP、DoT、DoH 复用当前出站的 SOCKS5。', ['none', 'profile'], 'none'),
});

export const oxidnsGlobalSchema = o('全局配置', '原生 OxiDNS 顶层设置。字符串支持环境变量占位符；需要将数字参数写成占位符时可切换值类型。', {
  runtime: o('运行时', '设置运行线程；省略时由 OxiDNS 自动选择。', {
    worker_threads: n('工作线程数', 'Tokio 工作线程数量，必须大于 0；省略时使用系统可用并行度。'),
  }),
  log: o('日志', '日志等级、文件和轮转；不填文件时仅输出到标准输出。', {
    level: e('日志级别', '越详细的等级记录越多；一般使用 info，排障时使用 debug。', ['off', 'trace', 'debug', 'info', 'warn', 'error'], 'info'),
    file: s('日志文件', '可选输出文件，配置后仍保留标准输出。相对路径基于工作目录。'),
    rotation: o('日志轮转', '按时间分割日志；省略时不轮转。', {
      type: e('轮转周期', 'never 不轮转；其余选项分别按分钟、小时、日、周生成历史日志。', ['never', 'minutely', 'hourly', 'daily', 'weekly'], 'never'),
      max_files: n('历史文件上限', '最多保留多少个轮转文件；0 表示不自动清理。', 0),
    }),
  }),
  network: o('共享网络', '管理上游和 HTTP 客户端共用的解析器与代理。', {
    outbound: o('出站配置', '定义可复用的出站 profile。', {
      default: s('默认出站', '必须引用 profiles 中已存在的名称；留空不设置默认代理。'),
      profiles: map('出站列表', '每个条目的键是 profile 名称，可被上游、下载和 HTTP 请求引用。', o('出站', '独立设置目标域名的解析方式和连接代理。', {
        resolver,
        proxy: o('连接代理', '填写 socks5 使用代理；也可切换为字符串填写 none 或 direct。', { socks5 }),
      })),
    }),
  }),
  api: o('管理 API', '提供管理接口和可选 WebUI；API 与 DNS 监听相互独立。', {
    http: o('HTTP 服务', '详细设置监听、TLS、认证、跨域和 WebUI；也支持字符串形式的监听地址。', {
      listen: required(s('监听地址', '例如 127.0.0.1:9199；0.0.0.0 允许局域网 IPv4 访问，:端口 表示双栈监听。')),
      ssl: o('TLS', '证书和私钥必须同时配置；启用客户端证书验证时还需 CA。', {
        cert: required(s('证书文件', 'PEM 服务器证书链路径。')),
        key: required(s('私钥文件', '与证书匹配的 PEM 私钥路径。')),
        client_ca: s('客户端 CA', '用于验证客户端证书的 CA 文件路径。'),
        require_client_cert: b('要求客户端证书', '启用双向 TLS；必须配置 client_ca。', false),
      }),
      auth: o('身份认证', 'Basic 认证需要同时设置非空用户名和密码。', {
        type: e('认证方式', '当前原生 API 支持 HTTP Basic。', ['basic'], 'basic'),
        username: required(s('用户名', '管理 API / WebUI 登录用户名。')),
        password: required(s('密码', '管理 API / WebUI 登录密码；可使用 ${env:变量名}。')),
      }),
      cors: o('跨域访问', '限制哪些网页来源可调用 API；省略时根据监听地址推导。', {
        allowed_origins: strings('允许的来源', '按浏览器 Origin 精确匹配，例如 https://dns.example；* 不能与跨域浏览器凭据同时使用。'),
      }),
      webui: o('WebUI', '挂载静态管理页面，API 仍位于 /api/*。', {
        root: required(s('静态文件目录', 'WebUI 文件目录；相对路径基于 OxiDNS 工作目录。')),
        index: s('首页文件', '目录中的默认入口文件名。', 'index.html'),
      }),
    }),
  }),
  include: strings('包含配置', '按顺序递归载入其他 YAML 的 plugins；最多 8 层。相对路径以声明 include 的文件位置为基准。可将文件放入附加文件。'),
});
oxidnsGlobalSchema.documentation = 'https://oxidns.org/configuration/global/';

const plugin = (type: string, category: string, label: string, help: string, page: string, args: OxidnsSchema, example?: unknown): OxidnsPluginDefinition => ({
  type, category, label, help,
  documentation: `https://oxidns.org/plugin-reference/${page}/#${type === 'prefer_ipv4' || type === 'prefer_ipv6' ? 'prefer_ipv4--prefer_ipv6' : type}`,
  args,
  ...(example === undefined ? {} : { example }),
});
const serverEntry = required(s('入口执行器', '填写已定义的执行器 tag，通常为主 sequence 的名称，不加 $。'));
const listen = required(s('监听地址', '支持 IP:端口、[IPv6]:端口、:端口；:端口 监听双栈。'));
const cert = s('TLS 证书', 'PEM 证书链文件路径，必须与私钥配套。');
const key = s('TLS 私钥', 'PEM 私钥文件路径，必须与证书配套。');
const probeCache = o('探测缓存', '缓存 IP 测速结果，减少重复探测。', {
  enabled: b('启用缓存', '保存探测评分供后续请求复用。', true),
  size: n('缓存容量', '探测结果缓存的条目容量。', 4096),
  ttl: n('成功缓存（秒）', '成功探测结果保留时间。', 3600),
  failure_ttl: n('失败缓存（秒）', '失败探测结果保留时间。', 60),
});
const preferArgs = o('双栈优选参数', '偏好地址族有答案时抑制另一地址族；内部探针可使用独立执行器。', {
  probe_executor: s('探针执行器', '只处理偏好类型内部探针的执行器 tag，不加 $；不要指回自身导致循环。'),
  cache: b('探测结果缓存', '缓存偏好地址族是否存在，避免重复探针。', true),
  cache_ttl: n('探测缓存（秒）', '保存地址族存在状态的时长。', 3600),
});
const rosShared: Record<string, OxidnsSchema> = {
  address: required(s('RouterOS 地址', 'RouterOS API 的 host:port；通常明文为 8728，TLS 为 8729。')),
  username: required(s('用户名', '需要具备读取和维护目标地址列表或路由表的权限。')),
  password: required(s('密码', 'RouterOS API 登录密码，也可使用环境变量占位符。')),
  tls: o('API TLS', '添加此对象启用 API-SSL。', {
    server_name: s('证书服务器名', '覆盖由连接地址推导的证书主机名。'),
    ca: s('CA 文件', '私有 CA 或自签名证书的 PEM 文件路径。'),
    insecure: b('跳过校验', '关闭证书校验，不能与自定义 CA 同时使用。', false),
  }),
  connect_timeout: n('连接超时（秒）', '建立 API 连接的等待上限，必须大于 0。', 5),
  send_timeout: n('发送超时（秒）', '发送单条 API 命令的等待上限，必须大于 0。', 5),
  receive_timeout: n('接收超时（秒）', '等待下一段 API 响应的上限，必须大于 0。', 5),
  async: asyncMode,
  wait_timeout: { ...timeout('8s'), label: '同步等待超时', help: '异步关闭时等待一次后台写入的上限，超时后 DNS 响应继续返回。' },
  queue_capacity: n('队列容量', '限制不同 IP / 路由键的入队及重试积压数量。', 16384),
  comment_prefix: s('注释前缀', '与插件 tag 共同标识 OxiDNS 管理的条目，用于对账和清理。', 'oxi'),
  persistent: o('常驻地址', '启动时同步、不随 DNS TTL 过期的固定 IP / CIDR。', {
    ips: strings('常驻 IP / CIDR', '逐条添加固定地址或网段。'),
    files,
  }),
  min_ttl: n('最小 TTL（秒）', '提升过短的动态地址租约。', 60),
  max_ttl: n('最大 TTL（秒）', '截断过长的动态地址租约。', 3600),
  fixed_ttl: n('固定 TTL（秒）', '覆盖 DNS TTL 和上下限；0 表示动态项不按时间到期。'),
  cleanup_on_shutdown: b('关闭时清理', '正常关闭或重载时清理当前插件管理的 RouterOS 条目。', true),
};
const nftTarget = o('nftables 集合', '目标集合需在系统中预先创建。', {
  table_family: required(s('地址族', '例如 ip、ip6 或 inet。')),
  table_name: required(s('表名', '已有 nftables table 的名称。')),
  set_name: required(s('集合名', '已有 nftables set 的名称。')),
  mask: n('前缀长度', '写入地址使用的网络掩码；不配置时使用单地址前缀。'),
});
const records = (label: string) => strings(label, '逐条填写 zone record，例如 {qname} 60 {qclass} A 192.0.2.1；占位符仅允许在名称和类别位置。');
const domainRules = strings('域名规则', '支持 full:、domain:、keyword:、regexp:，或使用 $tag 引用域名 provider。');
const ipRules = strings('IP 规则', '逐条填写 IP、CIDR 或 $tag 形式的 IP provider 引用。');
const namedRules = (label: string, help: string) => strings(label, help);
const cacheArgs = o('缓存参数', '缓存按响应 TTL 过期，可配置负缓存、过期应答和持久化。', {
  size: n('容量', '最大缓存条目数。', 1024),
  lazy_cache_ttl: n('Lazy cache TTL（秒）', '填大于 0 的值启用过期正响应与后台刷新；要关闭应删除此参数，不能填 0。'),
  dump_file: s('持久化文件', '缓存快照路径；建议放在 state/ 下保留运行数据。'),
  dump_interval: n('持久化间隔（秒）', '定期保存缓存快照的周期。', 600),
  short_circuit: stop,
  cache_negative: b('负缓存', '缓存 NXDOMAIN 与 NODATA 响应。', true),
  max_negative_ttl: n('负缓存上限（秒）', '负响应缓存的最大时长。', 300),
  negative_ttl_without_soa: n('无 SOA 负缓存（秒）', '缺少 SOA 时的负缓存时长；0 表示不缓存该类响应。', 60),
  max_positive_ttl: n('正缓存上限（秒）', '限制正响应进入缓存后的最长有效期。'),
  min_positive_ttl: n('正缓存准入 TTL（秒）', '有效 TTL 低于此值的正响应不写入缓存；不会把 TTL 强行提高。'),
  ecs_in_key: b('按 ECS 区分缓存', '将 ECS scope 纳入缓存键，避免不同客户端网段共享结果。', false),
});

export const oxidnsPluginCatalog: OxidnsPluginDefinition[] = [
  plugin('udp_server', 'server', 'UDP 入口', '接收传统 UDP DNS 查询并交给入口执行器。', 'server/datagram-stream', o('UDP 参数', 'DNS UDP 监听设置。', { entry: serverEntry, listen })),
  plugin('tcp_server', 'server', 'TCP / DoT 入口', '接收 TCP DNS；同时配置证书与私钥时提供 DoT。', 'server/datagram-stream', o('TCP 参数', 'DNS TCP 和可选 TLS 设置。', { entry: serverEntry, listen, cert, key, idle_timeout: n('空闲超时（秒）', '空闲 TCP / DoT 连接的回收时间。', 10) })),
  plugin('http_server', 'server', 'HTTP / DoH 入口', '按 URL 路径接入 DoH，可启用 TLS、JSON API 和 HTTP/3。', 'server/encrypted-http', o('HTTP 参数', '每条 URL 路径可进入不同执行链。', {
    listen, cert, key,
    entries: required(a('路径映射', '为每个 DoH 路径选择执行器。', o('映射', '路径必须以 / 开头。', {
      path: required(s('路径', '例如 /dns-query。', '/dns-query')),
      exec: required(s('执行器', '填写处理此路径的执行器 tag。')),
      json_api: b('JSON DNS API', '允许 name/type 形式的 JSON DNS 查询。', false),
    }))),
    idle_timeout: n('空闲超时（秒）', 'HTTP 连接空闲后的回收时间。', 30),
    src_ip_header: s('客户端 IP 请求头', '从可信反向代理请求头恢复来源地址，例如 X-Forwarded-For。'),
    enable_http3: b('HTTP/3', '额外开启 QUIC DoH 监听，必须同时提供证书和私钥。', false),
  })),
  plugin('quic_server', 'server', 'DoQ 入口', '通过 QUIC 接入 DNS，必须配置 TLS 证书。', 'server/encrypted-http', o('QUIC 参数', '监听底层 UDP 端口，请避免与 UDP DNS 冲突。', { entry: serverEntry, listen, cert: required(cert), key: required(key), idle_timeout: n('空闲超时（秒）', '回收空闲 QUIC 连接的等待时间。') })),

  plugin('sequence', 'executor', '规则执行链', '按顺序判断条件并执行动作；多条匹配条件之间为 AND。', 'executor/control-flow', a('规则', '通过 $tag 引用插件；支持内建 accept、return、reject、mark、set_mark、jump、goto。', o('规则', '不填 matches 时总是执行。', {
    matches: strings('匹配条件', '条件使用 $匹配器 或 quick setup 表达式，前加 ! 可取反；也支持单字符串形式。'),
    exec: s('执行动作', '填写 $执行器、quick setup，或 accept、return、reject [rcode]、mark、set_mark、jump/goto tag。'),
  })), [{ exec: 'return' }]),
  plugin('fallback', 'executor', '主备切换', '主路径未及时产生响应时启用备用路径。', 'executor/control-flow', o('主备参数', '分支是否成功只看有没有 DNS 响应；若需过滤错误响应，请在分支内增加匹配条件。', {
    primary: required(s('主执行器', '主路径执行器 tag。')), secondary: required(s('备用执行器', '备用路径执行器 tag。')),
    threshold: n('等待阈值（毫秒）', '超过阈值未得到主响应时启动备用路径。', 0),
    always_standby: b('备用同时待命', '让备用路径与主路径同时执行，以额外查询换取较低回退延迟。', false), short_circuit: stop,
  })),
  plugin('forward', 'executor', 'DNS 转发', '向一个或多个 DNS 上游查询，控制并发与响应选择。', 'executor/resolution', o('转发参数', '多上游竞争时可选择偏向速度、正响应或负响应共识的策略。', {
    upstreams: required(a('上游', '至少配置一个上游。', oxidnsUpstreamSchema)),
    concurrent: n('并发数', '每次最多并行查询 1–32 个上游，且不超过上游数量。', 1),
    response_selection: e('响应选择', 'fastest 取最先返回；balanced 兼顾正响应与等待时间；prefer_positive 等待正响应；consensus 更谨慎确认负响应。', ['fastest', 'balanced', 'prefer_positive', 'consensus'], 'balanced'),
    short_circuit: stop,
  }), { upstreams: [{ addr: '223.5.5.5' }], concurrent: 1 }),
  plugin('cache', 'executor', 'DNS 缓存', '复用正负响应，支持 lazy cache、缓存分区和磁盘快照。', 'executor/resolution', cacheArgs),
  plugin('hosts', 'executor', '静态 Hosts', '按域名规则返回本地 A / AAAA 地址。', 'executor/resolution', o('Hosts 参数', '内联条目和文件可以同时提供规则。', {
    entries: strings('条目', '格式：域名规则 IP1 IP2，例如 full:router.lan 192.168.1.1。'), files, short_circuit: stop,
  })),
  plugin('arbitrary', 'executor', '静态 DNS 记录', '用 zone record 定义任意类型的本地 DNS 应答。', 'executor/resolution', o('静态记录参数', '适合 MX、TXT、SRV 等 Hosts 无法表示的记录。', {
    rules: strings('记录', '每条使用 zone record 语法，例如 example.test. 60 IN TXT "hello"。'), files, short_circuit: stop,
  })),
  plugin('response', 'executor', '构造完整响应', '设置响应码、记录区和标志，直接生成 DNS 应答。', 'executor/resolution', o('响应参数', '可在记录模板中使用 {qname} 与 {qclass}。', {
    rcode: s('响应码', '填写 NOERROR、NXDOMAIN 等名称，也可切换为数字填写代码。', 'NOERROR'),
    answers: records('应答记录'), authorities: records('权威记录'), additionals: records('附加记录'),
    authoritative: b('权威应答 AA', '设置响应的 authoritative-answer 标志。', false),
    authentic_data: b('认证数据 AD', '设置响应的 authenticated-data 标志。', false),
    short_circuit: { ...stop, default: true },
  })),
  plugin('redirect', 'executor', '域名重定向', '改写查询目标，并在回程恢复客户端可见的 CNAME。', 'executor/resolution', o('重定向参数', '匹配域名后查询另一个域名。', { rules: strings('重定向规则', '格式：域名规则 目标域名，例如 full:old.example new.example。'), files })),
  plugin('client_ip_from_ecs', 'executor', '从 ECS 读取来源', '从可信来源的 ECS 中恢复客户端 IP，供后续分流和记录使用。', 'executor/resolution', strings('可信客户端', '允许提供 ECS 的原始客户端 IP / CIDR；默认仅信任 127.0.0.1 和 ::1。')),
  plugin('ecs_handler', 'executor', 'ECS 处理', '保留、补充或替换 EDNS Client Subnet。', 'executor/resolution', o('ECS 参数', '按来源地址或指定地址附加客户端网段。', {
    forward: b('保留已有 ECS', '保留客户端已发送的 ECS。', false), send: b('自动补充 ECS', '请求缺少 ECS 时根据来源地址生成。', false),
    preset: s('固定来源地址', '使用此 IP 作为生成 ECS 的来源。'), mask4: n('IPv4 前缀', 'IPv4 ECS 网络前缀长度。', 24), mask6: n('IPv6 前缀', 'IPv6 ECS 网络前缀长度。', 48),
  })),
  plugin('forward_edns0opt', 'executor', '转发 EDNS0 选项', '将指定选项从请求复制到最终响应。', 'executor/resolution', o('EDNS0 参数', '按数字 option code 选择需要复制的扩展。', { codes: a('选项代码', 'EDNS0 option code 数字列表。', n('代码', '要复制的 EDNS0 option code。')) })),

  plugin('ttl', 'executor', 'TTL 改写', '将响应 TTL 设为固定值，或限制在指定上下限。', 'executor/response', o('TTL 参数', '所有时间均为秒。', { fix: n('固定 TTL', '将全部记录 TTL 设为这个值。'), min: n('最小 TTL', '提高低于此值的 TTL。'), max: n('最大 TTL', '降低高于此值的 TTL。') })),
  plugin('ip_selector', 'executor', 'IP 优选', '探测响应中的多个 IP，并选择或重排较优地址。', 'executor/response', o('优选参数', '探测会消耗额外连接，可使用后台模式及缓存降低查询延迟。', {
    selection_mode: e('优选模式', 'first_success 首次成功即返回；best_within_budget 在预算内比较；background 先应答再后台刷新。', ['first_success', 'best_within_budget', 'background'], 'first_success'),
    probe_methods: strings('探测方法', '支持 tcp:端口、ping、none；也可切换为单字符串。默认 tcp:443 与 tcp:80。'), outbound, socks5,
    probe_stagger: n('探测错峰（毫秒）', '多种探测方法之间的启动间隔。', 200), probe_timeout: n('探测超时（毫秒）', '单个 IP 探测的等待上限。', 600), max_wait: n('总等待预算（毫秒）', '本次响应优选最多等待多久。', 1000),
    top_n: n('保留地址数', '保留排名前 N 个地址；0 表示只重排。', 1),
    reorder_only: b('DNSSEC 仅重排（兼容）', '旧参数：未设置 dnssec_policy 时，true 对应 reorder_only；建议直接设置 DNSSEC 策略。', false),
    dnssec_policy: e('DNSSEC 策略', 'reorder_only 对带 DNSSEC 的响应只重排；skip 完全跳过优选。', ['reorder_only', 'skip'], 'reorder_only'),
    max_parallel_probes: n('最大并行探测', '插件级探测并发上限，避免冷请求同时发起过多连接。', 256), cache: probeCache,
  })),
  plugin('prefer_ipv4', 'executor', '优先 IPv4', '探测和缓存 IPv4 可用状态，必要时抑制 IPv6 响应。', 'executor/response', preferArgs),
  plugin('prefer_ipv6', 'executor', '优先 IPv6', '探测和缓存 IPv6 可用状态，必要时抑制 IPv4 响应。', 'executor/response', preferArgs),
  plugin('black_hole', 'executor', '拦截响应', '返回 NXDOMAIN、空应答、空地址、自定义地址或拒绝。', 'executor/response', o('拦截参数', '可用于广告屏蔽或自定义拒绝策略。', {
    mode: e('响应模式', '未填时：有 ips 使用 custom，否则使用 nxdomain；null 是模式名称字符串。', ['nxdomain', 'nodata', 'null', 'custom', 'refused']),
    ips: strings('自定义 IP', 'custom 模式按查询地址族选择对应 IP；其他类型返回空应答。'), short_circuit: stop,
  })),
  plugin('drop_resp', 'executor', '清空响应', '删除当前已有响应，使后续链路可以重新查询。', 'executor/response', none),
  plugin('reverse_lookup', 'executor', '反向查询缓存', '从应答学习 IP 到域名映射，可直接回答 PTR 查询。', 'executor/response', o('反查参数', '缓存从 DNS 响应中学习的映射。', { size: n('容量', '最多保存的 IP 到域名映射数。', 65535), handle_ptr: b('处理 PTR', '使用缓存直接响应 PTR 请求。', false), ttl: n('映射 TTL（秒）', 'IP 到域名映射的保留时间。', 7200) })),

  plugin('query_summary', 'executor', '查询摘要', '后续执行链结束时输出简短查询日志。', 'executor/observability', o('摘要参数', '日志输出到全局日志目标。', { msg: s('摘要标题', '用于区分不同位置输出的摘要。', 'query summary') })),
  plugin('query_recorder', 'executor', '查询记录', '把查询、响应与执行路径保存到 SQLite，并提供历史与统计 API。', 'executor/observability', o('记录参数', '数据库应放在 state/ 下，避免部署覆盖运行数据。', {
    path: required(s('数据库路径', '此记录器独立使用的 SQLite 文件路径。', 'state/query-recorder.sqlite')),
    queue_size: n('写入队列', '查询处理与后台写线程之间的有界队列容量。', 8192), batch_size: n('写入批量', '每批写入数据库的记录数。', 256),
    flush_interval_ms: n('刷新间隔（毫秒）', '等待不足一批数据时的定期写入周期。', 200), memory_tail: n('内存最近记录', '用于实时流回放的内存记录数量。', 1024),
    retention_days: n('保留天数', '自动清理超过此时间的查询记录。', 7), cleanup_interval_hours: n('清理间隔（小时）', '执行过期数据清理的周期。', 1), reader_concurrency: n('并发读取数', '限制 WebUI 历史与统计同时运行的数据库读取任务。', 2),
  })),
  plugin('metrics_collector', 'executor', '指标收集', '收集请求量与延迟，供 Prometheus 抓取。', 'executor/observability', o('指标参数', '为不同链路提供独立指标名称。', { name: s('名称', '输出指标使用的名称标签。', 'default') })),
  plugin('debug_print', 'executor', '调试输出', '将请求和响应对象输出到日志用于排障。', 'executor/observability', o('调试参数', '调试输出可能较多，排障后可移除此执行器。', { msg: s('日志标题', '识别此处输出的调试内容。', 'debug print') })),
  plugin('sleep', 'executor', '异步等待', '为当前查询增加指定延迟，便于验证主备与超时策略。', 'executor/observability', o('等待参数', '只延迟当前任务，不阻塞运行线程。', { duration: n('等待时间（毫秒）', '增加的异步等待时长。', 0) })),
  plugin('learn_domain', 'executor', '域名学习', '把经过执行链的域名写入 dynamic_domain_set。', 'executor/observability', o('学习参数', '目标 provider 必须是可写的动态域名集合。', {
    provider: required(s('目标 Provider', 'dynamic_domain_set 插件的 tag。')), phase,
    questions: e('学习范围', 'first 只处理第一个 question，all 处理全部。', ['first', 'all'], 'first'),
    qtypes: strings('查询类型', '只学习指定查询类型，例如 A、AAAA；也接受数字代码。'),
    success_only: b('仅成功响应', 'after 阶段只学习 NOERROR 响应。', true), answer_required: b('要求有应答记录', 'after 阶段只学习包含 answer 的响应。', true),
    rule_kind: e('规则类型', 'full 只匹配域名本身；domain 也包含子域名。', ['full', 'domain'], 'full'), async: asyncMode, error_mode: errorMode, timeout: timeout('1s'),
  })),

  plugin('http_request', 'executor', 'HTTP 回调', '向外部服务发送 HTTP 请求，用于 webhook 或查询联动。', 'executor/integrations', o('HTTP 请求参数', 'URL、请求头与请求体可使用 ${qname}、${client_ip} 等运行期占位符。', {
    method: required(e('方法', '请求使用的 HTTP 方法。', ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'], 'POST')),
    url: required(s('URL', '目标 http:// 或 https:// 地址。')), phase, async: asyncMode, timeout: timeout(), error_mode: errorMode,
    headers: map('请求头', '逐项添加 HTTP 头名称和字符串值。'), query_params: map('查询参数', '逐项添加到 URL 查询字符串。'),
    body: s('文本请求体', '原始请求体；body、json、form 不能同时配置。'), json: o('JSON 请求体', '可切换为数组或其他 JSON 值，支持递归编辑字段。', {}), form: map('表单请求体', '作为 application/x-www-form-urlencoded 发送。'), content_type: s('内容类型', '仅为原始 body 设置 Content-Type。'),
    outbound, socks5, insecure_skip_verify: insecure, max_redirects: n('重定向上限', '最多跟随多少次 HTTP 重定向。', 5), queue_size: n('异步队列容量', '异步模式待发送请求的最大数量。', 256),
  })),
  plugin('script', 'executor', '外部命令', '执行指定程序，按请求上下文生成参数和环境变量。', 'executor/integrations', o('命令参数', '直接启动程序，不自动添加 shell；在目标主机准备对应命令。', {
    command: required(s('程序', '要执行的命令或可执行文件路径。')), args: strings('参数', '每项为一个参数，支持 ${qname}、${client_ip} 等占位符。'), env: map('环境变量', '为本次子进程设置变量，可引用运行期占位符。'), cwd: s('工作目录', '子进程执行时的工作目录。'), timeout: timeout(), error_mode: errorMode, max_output_bytes: n('输出捕获上限（字节）', '超过上限的 stdout / stderr 会被截断。', 4096),
  })),
  plugin('ipset', 'executor', 'Linux ipset', '将应答 IP 写入已有 ipset，供防火墙或策略路由使用。', 'executor/integrations', o('ipset 参数', '使用内置 netlink 后端，需要 Linux 及相应权限。', {
    set_name4: s('IPv4 集合', 'IPv4 地址写入的已有 ipset 名称。'), set_name6: s('IPv6 集合', 'IPv6 地址写入的已有 ipset 名称。'), mask4: n('IPv4 前缀', '写入 IPv4 网络前缀长度。', 32), mask6: n('IPv6 前缀', '写入 IPv6 网络前缀长度。', 128),
  })),
  plugin('nftset', 'executor', 'Linux nftables', '将应答 IP 写入已有 nftables set。', 'executor/integrations', o('nftables 参数', '优先使用 ipv4 / ipv6 对象；旧扁平字段同时保留兼容。', {
    ipv4: nftTarget, ipv6: nftTarget,
    table_family4: s('IPv4 地址族（兼容）', '旧格式的 table family，通常为 ip。'), table_family6: s('IPv6 地址族（兼容）', '旧格式的 table family，通常为 ip6。'),
    table_name4: s('IPv4 表名（兼容）', '旧格式 IPv4 table 名称。'), table_name6: s('IPv6 表名（兼容）', '旧格式 IPv6 table 名称。'), set_name4: s('IPv4 集合（兼容）', '旧格式 IPv4 set 名称。'), set_name6: s('IPv6 集合（兼容）', '旧格式 IPv6 set 名称。'), mask4: n('IPv4 前缀（兼容）', '旧格式 IPv4 网络前缀长度。', 32), mask6: n('IPv6 前缀（兼容）', '旧格式 IPv6 网络前缀长度。', 128),
  })),
  plugin('ros_address_list', 'executor', 'RouterOS 地址列表', '把应答 IP 写入 RouterOS 地址列表，并维护 TTL 与常驻地址。', 'executor/integrations', o('地址列表参数', '至少选择一个地址族的目标列表。', { ...rosShared, address_list4: s('IPv4 地址列表', '接收 A 记录的 address-list 名称。'), address_list6: s('IPv6 地址列表', '接收 AAAA 记录的 address-list 名称。') })),
  plugin('ros_route', 'executor', 'RouterOS 路由', '把应答 IP 变为目标路由表中的主机路由，并维护租约。', 'executor/integrations', o('路由参数', '路由表和网关需预先存在；插件不创建默认路由或 routing rule。', {
    ...rosShared, routing_table: required(s('路由表', 'RouterOS 中已有的目标 routing table 名称。')), gateway4: s('IPv4 网关', 'IPv4 路由使用的 RouterOS gateway 表达式。'), gateway6: s('IPv6 网关', 'IPv6 路由使用的 RouterOS gateway 表达式。'), distance: n('路由距离', '写入路由的 distance。', 100), conntrack_guard: b('连接跟踪保护', '到期删除主机路由前检查活跃连接，有连接时延后 30 秒再检查。', false),
  })),

  plugin('upgrade', 'executor', '自动升级', '执行 OxiDNS 二进制与 WebUI 升级，可由定时任务触发。', 'executor/maintenance', o('升级参数', '此执行器执行 apply；可选择保留备份或跳过重启。', {
    repository: s('GitHub 仓库', '获取 Release 的 owner/repository。', 'svenshi/oxidns'), asset: s('发布文件', 'auto 按平台和构建版本选择；也可填写确切资产名称。', 'auto'), bundle: e('构建版本', 'asset 为 auto 时生效；auto 跟随当前二进制构建。', ['auto', 'full', 'standard', 'minimal'], 'auto'),
    github_token: s('GitHub Token', '可提高 API 限额或访问私有仓库；建议使用环境变量。'), cache_dir: s('下载缓存目录', '保存升级下载文件的目录。'), backup_dir: s('备份目录', '替换前的文件备份目录。'), webui_dir: s('WebUI 目录', '应与 api.http.webui.root 保持一致。', './webui'),
    skip_webui: b('跳过 WebUI', '只升级二进制。', false), no_restart: b('暂不重启', '升级完成后不自动重启 OxiDNS。', false), allow_prerelease: b('包含预发布版', '允许选择 GitHub 预发布版本；同版本官方源码支持此参数。', false), force: b('强制升级', '即使目标版本不比当前新也执行替换。', false), cleanup: b('完成后清理', '成功后清理下载缓存与备份；需要保留回滚文件时关闭。', true), timeout: timeout('30s'), outbound, socks5, insecure_skip_verify: insecure,
  })),
  plugin('download', 'executor', '文件下载', '按顺序下载规则或其他文件，完整写入后替换旧文件。', 'executor/maintenance', o('下载参数', '文件更新后可串联 reload_provider，让规则 provider 重新载入。', {
    timeout: timeout('30s'), outbound, socks5, insecure_skip_verify: insecure,
    startup_if_missing: b('启动时补齐文件', '启动时下载缺失目标；失败会阻止启动，已有文件不会被覆盖。', true),
    downloads: required(a('下载列表', '每项指定 URL、目录和可选文件名。', o('下载项', '仅支持 HTTP / HTTPS。', { url: required(s('URL', '远端文件地址。')), dir: required(s('保存目录', '目标本地目录，不存在时创建。')), filename: s('文件名', '省略时根据 URL 推导文件名。') }))),
  })),
  plugin('reload_provider', 'executor', '重新载入规则', '按 tag 刷新 provider 快照，无需重建整条执行链。', 'executor/maintenance', strings('Provider 列表', '填写 $provider_tag，按需要刷新文件型规则集合。')),
  plugin('reload', 'executor', '重新载入全部配置', '触发应用级配置重载，重建依赖与插件。', 'executor/maintenance', none),
  plugin('cron', 'executor', '定时任务', '按固定间隔或 cron 表达式顺序执行维护动作。', 'executor/maintenance', o('调度参数', '每个任务必须选择 schedule 或 interval 之一。', {
    timezone: s('时区', 'IANA 时区，例如 Asia/Shanghai；省略时使用系统时区。'),
    jobs: required(a('任务', '为每个任务添加名称、时间条件和执行器。', o('任务', '任务中的执行器按填写顺序执行。', {
      name: required(s('任务名称', '用于日志和运行时标识。')), schedule: s('Cron 表达式', '标准 5 字段表达式，例如 0 */6 * * *；与 interval 互斥。'), interval: s('固定间隔', '例如 5m、1h、1d；与 schedule 互斥。'), executors: required(strings('执行器', '支持 $tag、裸 tag 或 quick setup 表达式。')),
    }))),
  })),

  plugin('qname', 'matcher', '查询域名', '按内联规则或 provider 匹配请求域名。', 'matcher/request', domainRules),
  plugin('question', 'matcher', '完整问题匹配', '调用 provider 的 contains_question，支持同时按域名和类型等条件判断。', 'matcher/request', strings('Provider 引用', '使用 $tag 引用支持 question 匹配的 provider，例如 adguard_rule。')),
  plugin('qtype', 'matcher', '查询类型', '匹配 A、AAAA、HTTPS 等 DNS 查询类型。', 'matcher/request', namedRules('查询类型', '填写数字代码或类型名称，例如 A、AAAA、65。')),
  plugin('qclass', 'matcher', '查询类别', '匹配 DNS question 的 class。', 'matcher/request', namedRules('查询类别', '填写类别名称或数字，例如 IN、1。')),
  plugin('client_ip', 'matcher', '客户端 IP', '匹配客户端来源地址或网段。', 'matcher/request', ipRules),
  plugin('ptr_ip', 'matcher', 'PTR 地址', '从 PTR 查询名称中提取 IP 后匹配。', 'matcher/request', ipRules),
  plugin('resp_ip', 'matcher', '响应 IP', '匹配应答 A / AAAA 地址。', 'matcher/response', ipRules),
  plugin('cname', 'matcher', 'CNAME 目标', '匹配响应中的 CNAME 目标域名。', 'matcher/response', domainRules),
  plugin('rcode', 'matcher', '响应码', '按响应码选择执行分支。', 'matcher/response', namedRules('响应码', '填写 NOERROR、NXDOMAIN 等名称或数字代码。')),
  plugin('has_resp', 'matcher', '已有响应', '当前上下文中存在 DNS 响应时命中。', 'matcher/response', none),
  plugin('has_wanted_ans', 'matcher', '包含所需记录', '应答区中包含本次查询类型所需记录时命中。', 'matcher/response', none),
  plugin('mark', 'matcher', '上下文标记', '配置标记与当前请求 marks 有交集时命中。', 'matcher/context', namedRules('标记', '每项为无符号整数标记，也支持在字符串中用空白或逗号分隔。')),
  plugin('env', 'matcher', '环境变量', '所有环境变量条件均满足时命中。', 'matcher/context', strings('环境条件', 'KEY=VALUE 或 KEY:VALUE 检查等值；KEY、KEY=、KEY: 检查存在。每项保留其中的空格和逗号。')),
  plugin('time', 'matcher', '时间条件', '按时区、时间段、星期和月日选择策略。', 'matcher/context', o('时间参数', '多个时段为 OR，同一时段的条件为 AND。', {
    timezone: s('时区', 'IANA 时区名称，例如 Asia/Shanghai；省略时使用系统时区。'),
    periods: required(a('时段', '添加 1–64 个时段；跨午夜条件归属于开始日。', o('时段', '起止时间需同时填写或同时省略；省略时需配置星期或月日。', {
      start: s('开始时间', 'HH:MM，包含此时刻。'), end: s('结束时间', 'HH:MM，不包含此时刻；早于开始时间表示跨午夜。'), weekdays: strings('星期', 'mon 至 sun，或切换条目为数字填写 1–7。'), monthdays: a('每月日期', '1–31；该月不存在的日期不会命中。', n('日期', '每月第几日，1–31。')),
    }))),
  })),
  plugin('random', 'matcher', '概率匹配', '按指定概率进行随机采样或分流。', 'matcher/context', strings('概率', '只添加一个值，范围 0.0–1.0；例如 0.1 表示约 10%。'), ['0.1']),
  plugin('rate_limiter', 'matcher', '客户端限速', '按客户端网段维护令牌桶；命中表示允许通过，false 表示已限流。', 'matcher/context', o('限速参数', '可按 IPv4 / IPv6 前缀聚合客户端。', { qps: n('每秒令牌', '令牌每秒补充速率。', 20), burst: n('突发容量', '令牌桶最多容纳的令牌数量。', 40), mask4: n('IPv4 聚合前缀', '32 表示每个 IPv4 地址独立限速。', 32), mask6: n('IPv6 聚合前缀', '按此前缀聚合 IPv6 客户端。', 48) })),
  plugin('string_exp', 'matcher', '字符串表达式', '用字符串规则匹配请求上下文字段。', 'matcher/composition', s('表达式', '格式：来源 运算 参数，例如 qname suffix .example.com；也可切为数组，逐项填写 source、op、参数。')),
  plugin('any_match', 'matcher', '任一条件', '多个匹配表达式中有一个命中即为真。', 'matcher/composition', strings('条件', '填写 $matcher 或 quick setup 匹配表达式。')),
  plugin('_true', 'matcher', '恒为真', '始终命中，可用于规则测试。', 'matcher/composition', none),
  plugin('_false', 'matcher', '恒为假', '始终不命中，可用于规则测试。', 'matcher/composition', none),

  plugin('domain_set', 'provider', '域名集合', '汇总内联、文件和其他 provider 中的域名规则。', 'provider/domain', o('域名集合参数', '可由 qname、cname 等匹配器引用。', { exps: strings('域名表达式', '支持 full:、domain:、keyword:、regexp: 或无前缀域名；复用 provider 请添加到 sets。'), files, sets: strings('引用集合', '引用具有域名匹配能力的 provider tag。') })),
  plugin('dynamic_domain_set', 'provider', '动态域名集合', '维护可持久化的动态规则，支持 API 编辑和自动学习。', 'provider/domain', o('动态集合参数', '运行期生成的文件建议放在 state/ 中。', { path: required(s('规则文件', '动态集合自己的本地文件路径。')), bootstrap_rules: strings('初始规则', '仅在规则文件不存在时写入的域名规则。'), queue_size: n('写入队列', '待写入域名的队列容量。', 1024), batch_size: n('写入批量', '触发批量追加的规则数量。', 256), flush_interval_ms: n('刷新间隔（毫秒）', '后台定期追加文件的间隔。', 200) })),
  plugin('geosite', 'provider', 'GeoSite 数据库', '从 geosite.dat 中提取域名分类与属性。', 'provider/domain', o('GeoSite 参数', '加载二进制规则库；可通过附加文件上传数据库。', { file: required(s('数据库文件', 'geosite.dat 路径。')), selectors: strings('分类选择', '例如 cn、geolocation-!cn，也支持 code@attribute。') })),
  plugin('adguard_rule', 'provider', 'AdGuard 规则', '读取 AdGuard DNS 过滤规则，供 question 匹配器使用。', 'provider/domain', o('AdGuard 参数', '支持官方文档列出的 DNS 过滤规则子集。', { rules: strings('规则', '逐条添加，例如 ||ads.example^ 或 @@||safe.example^。'), files })),
  plugin('ip_set', 'provider', 'IP 集合', '汇总 IP / CIDR 供客户端、响应和 PTR 匹配使用。', 'provider/ip', o('IP 集合参数', '将可复用的 IP 规则集中管理。', { ips: strings('IP / CIDR', '逐条填写 IP 或网段。'), files, sets: strings('引用集合', '引用具有 IP 匹配能力的 provider tag，例如 ip_set、geoip。') })),
  plugin('geoip', 'provider', 'GeoIP 数据库', '从 geoip.dat 中提取指定分类的 IP / CIDR。', 'provider/ip', o('GeoIP 参数', '加载二进制规则库；可通过附加文件上传数据库。', { file: required(s('数据库文件', 'geoip.dat 路径。')), selectors: strings('分类选择', '填写数据库中的 code，例如 cn。') })),
];
