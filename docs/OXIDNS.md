# OxiDNS 配置与发布

ConfigFlow 将 **MosDNS** 和 **OxiDNS** 作为两个独立生成目标。它们共享规则仓库，但分别保存上游、规则选择、缓存和特色功能设置。修改 OxiDNS 不会改写 MosDNS 设置。

模板按 OxiDNS **v1.6.1 full** 的配置语义验证；精简构建可能缺少查询记录、IP 优选或 RouterOS 等插件。不同版本的额外参数请以 [OxiDNS 官方文档](https://oxidns.org/) 和目标二进制的 `oxidns check` 为准。

## 使用流程

1. 在“生成配置”选择 **OxiDNS**，打开参数设置。
2. 配置国内、国外和备用上游（需要 UDP 时显式写 `udp://IP:53`），并在分流规则中选择直连、代理规则。同一条规则只能选择一种策略。
3. 根据机器资源调整线程、缓存和并发参数。可用“应用已验证模板”恢复已验证的性能参数。
4. 保存后查看配置预览。预览显示最终 YAML；下载返回 `oxidns-config.zip`，包括 `config.yaml`、已转换的订阅规则、自定义 hosts 和启用的 AdGuard 文件。
5. 在 Agent 中选择类型为 OxiDNS、配置空间相同的目标进行发布。Agent 先准备完整文件、校验，再切换并检查服务；失败时走既有事务回滚流程。

订阅 URL 只返回 YAML，不能替代首次部署所需的完整 ZIP。手动部署时，解压到 OxiDNS 的工作目录后先执行：

```sh
oxidns check -c /etc/oxidns/config.yaml -d /etc/oxidns
```

`-d` 必须与实际服务的工作目录一致。首版 Agent 要求工作目录等于配置文件所在目录；官方 Debian 布局常为配置放在 `/etc/oxidns`、工作目录为 `/var/lib/oxidns`，此时原生 Agent 安装/发布会明确拒绝，需要先将核心服务的 `-d` 与配置目录对齐。Docker Agent 已使用一致目录。API TLS 证书、RouterOS CA 和 WebUI 静态资源需要在目标机安装；配置包不包含二进制或这些外部资源。Docker Agent 镜像提供的 WebUI 目录为 `/usr/share/oxidns/webui`。

## 已验证模板

| 项目 | 默认值 | 作用 |
| --- | --- | --- |
| Worker 线程 | 2 | 可按目标机器 CPU 调整，不能为 0 |
| 国内 / 国外 / 备用并发 | 2 / 1 / 2 | 各 forward 的上游竞速并发 |
| 主缓存 | 10240 条 | 用于国内直连响应，`short_circuit: true` |
| Lazy cache | 300 秒 | 允许在总保留期内返回过期正响应并后台刷新 |
| 探测缓存 | 2048 条 | 只缓存国内探测结果，不启用 lazy |
| 缓存持久化 | 关闭 | 可独立开启；升级或变更策略后应考虑旧缓存有效性 |
| Provider 合并 | 开启 | 只合并相邻、策略相同的规则，保留直连/代理顺序 |
| 轻量指标 | 开启 | 不默认启用逐条查询日志 |
| DNS 服务 | UDP + TCP，端口 53 | TCP 用于截断响应回退等场景 |

Lazy TTL 不是“原 TTL 再加 300 秒”。v1.6.1 的总保留期为 `max(原始 TTL, lazy_cache_ttl)`；例如原 TTL 60 秒时，可能在第 60–300 秒返回过期响应；原 TTL 600 秒不会额外延长。负响应不会按正响应的 stale 规则续用。

国内探测使用独立子序列：

```yaml
- tag: local_probe_cache
  type: cache
  args:
    size: 2048
    short_circuit: false
- tag: local_probe
  type: sequence
  args:
    - exec: $local_probe_cache
    - matches: ['!has_resp']
      exec: $forward_local
    - exec: return
```

主序列通过 `jump local_probe` 调用，随后继续按响应 IP 分流。不要改成 `goto local_probe` 或将探测缓存设为 `short_circuit: true`；那会改变父序列继续分类的行为。探测缓存不保存最终的代理/Fake-IP 响应。

### 分流与扩展匹配

规则按照配置空间中的顺序执行：先域名规则，再对未命中请求进行国内探测和 IP 分类，最后走默认转发。普通规则和订阅规则使用同一顺序。混合 classical 订阅拆分为域名、IP 两部分；正则中的逗号保留原义。

机器标签和规则文件名使用稳定的 ASCII 标识，中文名称、重复名称或带路径符号的名称不会成为 OxiDNS tag 或不安全的文件路径。

高级匹配可引用模板提供的 `china_dns`、`remote_dns`、`proxy_dns_seq` 等序列。需要绕过常规缓存、按真实上游响应返回的专用域名，可增加域名条件并执行 `goto remote_dns`；实际是否为真实 IP 取决于该上游。OxiDNS v1.6.1 没有原生 Fake-IP 插件，现有 Fake-IP 应继续使用外部上游。

## 特色功能

特色功能默认关闭，可分别启用；开启后会增加相应资源消耗。

| 功能 | 模板行为与可配置项 |
| --- | --- |
| 查询记录 `query_recorder` | 放在主入口，记录 SQLite 查询历史；可调整保留天数、队列、批量写入和读并发，默认 7 天 |
| IP 优选 `ip_selector` | 只接入国内真实 IP 分支；默认后台探测、`top_n: 0` 只排序，位于缓存之前，缓存保留原始应答 |
| 动态域名集 | 可通过 OxiDNS API 管理，支持初始规则与直连/代理目的地；持久文件放在 `state/`；显式域名规则优先于学习结果 |
| 自动学习 `learn_domain` | 可选，仅在明确命中 IP 分流规则的分支学习，不对未分类的默认结果学习；默认只学习成功、有 answer 的 A/AAAA 完整域名 |
| AdGuard DNS 规则 | 支持内联文本和 HTTP(S) 订阅，使用 `question` 匹配以保留 `dnstype`；可选 NXDOMAIN、NODATA、空地址或 REFUSED |
| RouterOS address-list | 仅处理国内真实 IP 响应；支持凭据、TLS、队列、异步和 TTL 参数 |
| RouterOS route | 同上，需要预先存在的路由表以及 IPv4/IPv6 网关；插件不会创建路由表、routing rule 或默认路由 |
| 原生规则更新 | 生成 `cron` → `download` → `reload_provider` 维护链，支持时区、时间表达式和下载出站设置 |

AdGuard 支持的是 DNS 规则子集，不等同于完整 AdGuard：URL/路径、页面元素、`dnsrewrite`、`$client`、`$ctag` 等不受支持。

动态规则的初始 `bootstrap_rules` 由 OxiDNS 处理。`state/` 中的学习文件、查询数据库和缓存 dump 不纳入配置包，避免配置发布覆盖运行数据。需要清空学习结果时，应通过 OxiDNS 的动态 provider API 操作。

### 原生定时更新的边界

原生下载对**每个文件**使用临时文件后替换，单项失败会保留该项旧文件并继续其他项目；随后 provider reload 读取新旧混合的文件集。它不是整套规则的原子事务，也不提供 Agent 配置发布的整包回滚保证。更新内容无法解析时可能导致 provider reload 失败，应检查 OxiDNS 任务状态和日志。

默认关闭此功能。已有 ConfigFlow Agent 推送、systemd timer 或其他外部校验更新器时，应选择一个规则写入来源，避免并发覆盖。需要整套预校验、健康检查和失败回滚时使用 Agent 发布流程。

## API / WebUI 和日志

API 默认关闭。启用后填写监听地址：`127.0.0.1:9199` 只供本机访问，`0.0.0.0:9199` 可供 IPv4 局域网访问。用户名和密码须同时填写；填写后生成 HTTP Basic Auth。WebUI 目录需指向已安装的静态资源，启用 API 本身不会下载 WebUI。

API TLS 证书与私钥必须成对配置。启用 TLS 时，Agent 自动健康检查使用进程状态和实际 DNS 应答，不自动探测管理 API，避免对自签证书、域名证书或 mTLS 做不安全的跳过验证；显式填写的 HTTPS 健康地址仍按系统信任库验证。RouterOS TLS 则为独立的 `args.tls` 对象；对象存在即启用 API-SSL，`ca` 与 `insecure: true` 不能同时使用。

日志默认仅标准输出、`info` 级别；可配置日志文件和轮转。查询历史属于单独的 SQLite 功能，不依赖日志文件开关。

## 高级 YAML

高级 YAML 可追加全局项和独立插件，表单管理的 API、日志和 worker 线程以表单为准。自定义 `plugins` 追加到生成模板，tag 重复、非法 tag、无效 YAML 会报错；`include` 被拒绝，因为完整配置包不能依赖未打包的外部 YAML。

自定义插件的外部规则 `files`、`file` 或 RouterOS `persistent.files` 必须包含在完整包中。通常建议使用共享规则仓库或对应 provider 的内联 `exps` / `ips`；仅填写本机路径不能自动上传该文件。

## 接口与 MCP

- `GET /api/oxidns/settings`：返回当前配置空间的完整默认化设置。
- `POST /api/oxidns/settings`：保存完整设置对象并校验；返回 `{success, settings}`。
- `POST /api/generate/oxidns/preview`：返回 `{content}` YAML。
- `POST /api/generate/oxidns`：下载完整 ZIP。
- 显式配置空间路径：`/api/profiles/<id>/oxidns/settings`、`/api/profiles/<id>/generate/oxidns[/preview]`。
- MCP 支持 `get_oxidns_settings`、`update_oxidns_settings` 和现有生成/预览工具中的 `oxidns` 目标。

设置存储在 profile 的 `oxidns` 字段。旧配置导入后自动补齐缺省值；脱敏导出会去除 API/RouterOS 等凭据以及高级 YAML 中可能包含的秘密。

## 验证记录

本实现使用 OxiDNS v1.6.1 full 进行 10 组原生配置校验，覆盖核心模板、查询记录、IP 优选、动态域名与学习、RouterOS 两类集成、API Basic Auth、AdGuard 与原生规则更新。

Go Agent 的可选真实二进制回归 `TestOxiDNSNativeTransactionalDeployment` 已在 Linux amd64 + OxiDNS v1.6.1 通过：

- 完整包真实 `oxidns check`、启动、Basic Auth readyz 和 DNS 应答检查。
- 原生未知插件导致校验失败，运行中的配置和服务保持可用。
- 新配置启动成功但 DNS 返回 REFUSED，健康检查失败后自动恢复旧配置、旧规则和正常 DNS。
- 新增失败规则被移除，动态域名持久文件内容保留。
- 成功发布后手动回滚，恢复发布前配置并保留运行数据；旧发布记录的回滚请求被拒绝。

此测试使用独立临时目录和 loopback 临时端口，不操作系统 OxiDNS 服务。可在 Go Agent 目录运行：

```sh
OXIDNS_TEST_BINARY=/path/to/oxidns go test ./routes -run '^TestOxiDNSNativeTransactionalDeployment$' -count=1 -v
```

未设置环境变量时此测试跳过；普通单元测试不要求安装 OxiDNS。

## 参考

- [全局配置与 tag 规则](https://oxidns.org/configuration/global/)
- [解析与缓存](https://oxidns.org/plugin-reference/executor/resolution/)
- [IP 优选与响应处理](https://oxidns.org/plugin-reference/executor/response/)
- [查询记录与学习](https://oxidns.org/plugin-reference/executor/observability/)
- [域名 provider 与 AdGuard 子集](https://oxidns.org/plugin-reference/provider/domain/)
- [RouterOS 集成](https://oxidns.org/plugin-reference/executor/integrations/)
- [维护与定时任务](https://oxidns.org/plugin-reference/executor/maintenance/)
