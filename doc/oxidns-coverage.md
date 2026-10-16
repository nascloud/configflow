# OxiDNS 原生配置覆盖记录

## 对照基准

- 官方文档：[全局配置](https://oxidns.org/configuration/global/)、[插件总览](https://oxidns.org/plugin-reference/overview/)及各插件字段说明。
- 官方源码：[svenshi/oxidns，bd183f69348aa9c78c3b92bddbce28651d2bf66e](https://github.com/svenshi/oxidns/tree/bd183f69348aa9c78c3b92bddbce28651d2bf66e)。该提交的 Cargo 包版本为 **1.6.1**。
- 对照日期：2026-10-16。
- 字段目录：`frontend/src/lib/oxidnsSchema.ts`，全局与每个插件均带中文说明和官方文档入口。

## 覆盖范围

| 范围 | 数量 | 表达方式 |
| --- | ---: | --- |
| server | 4 | UDP、TCP / DoT、HTTP / DoH / HTTP3、QUIC / DoQ |
| executor | 35 | 编排、转发、缓存、响应处理、观测、系统联动和维护 |
| matcher | 20 | 请求、响应、环境、时间、限速、组合与表达式 |
| provider | 6 | 静态/动态域名、AdGuard、GeoSite、IP、GeoIP |
| 官方插件合计 | **65** | 每种类型都有对应表单字段目录 |
| 插件具名字段路径 | **288** | 包括数组内对象、TLS、缓存、持久地址等嵌套路径 |
| 全局具名字段路径 | **39** | runtime、log、network、api、include；动态 profile 名按通配路径计数 |
| 单个 DNS 上游参数 | **17** | 同一份字段目录供模板上游卡片与完整配置复用 |

字段路径数量包含容器字段，例如 `cache` 和 `cache.enabled` 分别计数；不重复计算数组中的多条实例。数组、字符串及无参数插件本身不算具名字段，但均包含在 65 种插件中。

全部 **286 个**官方字段标题/示例配置路径都能在目录中定位。另加入同一提交源码支持的 `upgrade.allow_prerelease` 与 `ip_selector.reorder_only`；后者是 DNSSEC 策略兼容项，建议优先使用 `dnssec_policy`。对 **41 个 YAML 反序列化对象结构体**比对了字段集合，没有遗漏或额外无效字段。两个双栈优选插件共享同一结构体。

## 全局配置

- `runtime.worker_threads`。
- `log.level`、`file`、`rotation.type`、`rotation.max_files`。
- `network.outbound.default`、任意命名 `profiles`，以及 resolver 的 nameservers / dial_addr / ip_version / timeout / proxy、连接代理 socks5。
- `api.http` 的监听、完整 TLS / mTLS、Basic Auth、CORS、WebUI 目录与入口文件。
- `include` 文件列表。包含文件只合并 plugins，相对路径基于声明它的配置文件位置，最多递归八层。
- `plugins` 由插件编辑器管理，保留 tag、type、args 及扩展键。

## 原生值形态与兼容

- 保留 OxiDNS 的数组、对象、字符串和空值形态；例如 sequence 是规则数组，qname / qtype 等 matcher 是参数数组，string_exp 是字符串或分词数组。
- `api.http` 支持监听地址简写；resolver 支持 `system` 或对象，proxy 支持 `none` / `direct` 或对象，sequence.matches 支持字符串或数组。切换值类型和 YAML 时应保持已有值，不隐式覆盖。
- 环境变量占位符也属于原生字符串值，数字/布尔字段可切换类型输入 `${VAR}` 或 `${env:VAR:-default}`。
- 表单展示的默认值只是新增字段的建议；省略与显式设置不是同一含义。尤其关闭 lazy cache 时应删除 `lazy_cache_ttl`，不能生成 `0`。
- 协议、系统联动等能力仍取决于目标 OxiDNS 编译特性、操作系统、权限和外部服务。字段覆盖不代表所有设备都能运行所有插件。
- 核心升级不由字段目录自动执行；部署仍需目标二进制原生校验。源码版本号相同也不能代替特性集与实际行为验证。

## 使用与部署

1. 在配置生成页选择 OxiDNS，打开设置。原有功能继续位于「快捷模板」。
2. 首次切换「完整配置」时，以当前未保存的模板草稿和当前空间规则生成原生配置。之后两种草稿独立保留，实际生成使用当前选中的模式。
3. 「完整配置」可在表单和 YAML 之间切换。通过插件搜索添加类型，在参数选择器中添加官方字段；每个字段和折叠标题旁的小问号提供说明。
4. 「附加文件」支持文本编辑、文件上传和 YAML 列表。文件按字节保留，UTF-8 和 Base64 均可；单文件 16 MiB、总量 64 MiB、最多 256 个。
5. 相对资源路径需随配置打包；`include` 必须引用附加文件，打包时按官方顺序展开插件。嵌套的 `../` 只允许解析到包内路径，不支持目标机绝对路径或环境变量形式的外部 include。
6. 绝对路径和环境变量形式的证书、规则等资源由目标机器预先提供。运行数据库、缓存与动态域名文件不随配置替换；建议放在 `state/`。
7. 保存后可预览、完整下载，或使用 Agent 暂存校验、激活和回滚。完整配置模式中的规则选择与执行链由原生配置本身决定，快捷模板后续修改不会自动同步进去。

## 插件目录

| 类型 | 功能 | args 默认形态 | 具名字段路径 |
| --- | --- | --- | ---: |
| `udp_server` | UDP 入口 | object | 2 |
| `tcp_server` | TCP / DoT 入口 | object | 5 |
| `http_server` | HTTP / DoH 入口 | object | 10 |
| `quic_server` | DoQ 入口 | object | 5 |
| `sequence` | 规则执行链 | array | 2 |
| `fallback` | 主备切换 | object | 5 |
| `forward` | DNS 转发 | object | 21 |
| `cache` | DNS 缓存 | object | 11 |
| `hosts` | 静态 Hosts | object | 3 |
| `arbitrary` | 静态 DNS 记录 | object | 3 |
| `response` | 构造完整响应 | object | 7 |
| `redirect` | 域名重定向 | object | 2 |
| `client_ip_from_ecs` | 从 ECS 读取来源 | array | 0 |
| `ecs_handler` | ECS 处理 | object | 5 |
| `forward_edns0opt` | 转发 EDNS0 选项 | object | 1 |
| `ttl` | TTL 改写 | object | 3 |
| `ip_selector` | IP 优选 | object | 16 |
| `prefer_ipv4` | 优先 IPv4 | object | 3 |
| `prefer_ipv6` | 优先 IPv6 | object | 3 |
| `black_hole` | 拦截响应 | object | 3 |
| `drop_resp` | 清空响应 | null | 0 |
| `reverse_lookup` | 反向查询缓存 | object | 3 |
| `query_summary` | 查询摘要 | object | 1 |
| `query_recorder` | 查询记录 | object | 8 |
| `metrics_collector` | 指标收集 | object | 1 |
| `debug_print` | 调试输出 | object | 1 |
| `sleep` | 异步等待 | object | 1 |
| `learn_domain` | 域名学习 | object | 10 |
| `http_request` | HTTP 回调 | object | 17 |
| `script` | 外部命令 | object | 7 |
| `ipset` | Linux ipset | object | 4 |
| `nftset` | Linux nftables | object | 18 |
| `ros_address_list` | RouterOS 地址列表 | object | 23 |
| `ros_route` | RouterOS 路由 | object | 26 |
| `upgrade` | 自动升级 | object | 16 |
| `download` | 文件下载 | object | 9 |
| `reload_provider` | 重新载入规则 | array | 0 |
| `reload` | 重新载入全部配置 | null | 0 |
| `cron` | 定时任务 | object | 6 |
| `qname` | 查询域名 | array | 0 |
| `question` | 完整问题匹配 | array | 0 |
| `qtype` | 查询类型 | array | 0 |
| `qclass` | 查询类别 | array | 0 |
| `client_ip` | 客户端 IP | array | 0 |
| `ptr_ip` | PTR 地址 | array | 0 |
| `resp_ip` | 响应 IP | array | 0 |
| `cname` | CNAME 目标 | array | 0 |
| `rcode` | 响应码 | array | 0 |
| `has_resp` | 已有响应 | null | 0 |
| `has_wanted_ans` | 包含所需记录 | null | 0 |
| `mark` | 上下文标记 | array | 0 |
| `env` | 环境变量 | array | 0 |
| `time` | 时间条件 | object | 6 |
| `random` | 概率匹配 | array | 0 |
| `rate_limiter` | 客户端限速 | object | 4 |
| `string_exp` | 字符串表达式 | string | 0 |
| `any_match` | 任一条件 | array | 0 |
| `_true` | 恒为真 | null | 0 |
| `_false` | 恒为假 | null | 0 |
| `domain_set` | 域名集合 | object | 3 |
| `dynamic_domain_set` | 动态域名集合 | object | 5 |
| `geosite` | GeoSite 数据库 | object | 2 |
| `adguard_rule` | AdGuard 规则 | object | 2 |
| `ip_set` | IP 集合 | object | 3 |
| `geoip` | GeoIP 数据库 | object | 2 |

## 可复现检查

从 ConfigFlow 仓库根目录执行：

```sh
npm --prefix frontend test -- tests/oxidns-schema.test.ts
.venv/bin/python scripts/audit_oxidns_schema.py /path/to/official-oxidns-checkout
```

审计脚本不会联网或操作运行中的 DNS。它要求官方 checkout 的提交与字段目录固定提交一致，提取插件总览、配置字段标题、YAML 示例，并对源码的配置结构体逐一检查。HTTP 请求体中的自定义 JSON 键不属于 OxiDNS 配置字段，因此不计入覆盖率。

测试目录中的字段清单来自固定官方文档，避免仅根据实现反向生成预期。表单/YAML 往返、附加文件、下载、Agent 发布与回滚由对应功能测试覆盖；原生功能校验和部署结果另见此次测试记录。
