# 设计草案：ConfigFlow 接入上游软路由（RouterOS / iKuai）

状态：草案，待评审。

## 1. 背景与目标

旁路网关方案（mihomo fake-ip + OxiDNS）真正生效，依赖主路由上的一组配置：

| 主路由配置 | 作用 | 现状 |
|---|---|---|
| 静态/策略路由：fake-ip 网段、Telegram 等直连 IP 段 → mihomo | 把代理流量导入 mihomo | 手工维护，规则集变化不会同步 |
| NAT 免转换：发往上述网段的流量不做 SNAT | mihomo 看到真实客户端 IP（回流） | 手工维护，且必须与 mihomo 侧回流规则同时生效 |
| DHCP 下发 DNS / 53 端口劫持 → OxiDNS | 客户端统一使用 OxiDNS | 手工维护；劫持未排除 DNS 服务器自身时会形成查询环路 |
| DHCP 静态分配 | 固定 OxiDNS / mihomo 地址 | 手工维护；替换主机时容易漏改 |

这些配置分散在路由器上，与 ConfigFlow 里的策略、规则集、Agent 拓扑没有关联，出问题时难以定位，也无法回滚。

**目标**：把主路由当作 ConfigFlow 的一个“网关目标”，由 ConfigFlow 根据配置空间和 Agent 拓扑计算主路由**应有的状态**，预览差异后再下发，并支持漂移检测和回滚。

**非目标**：

- 不做通用路由器管理面板（WAN、无线、PPPoE、防火墙全量规则等不管）。
- 不碰 ConfigFlow 没有创建的对象。
- 不在 iKuai 上安装任何程序（闭源系统，只走 HTTP API）。

## 2. 接入方式

| 方式 | RouterOS | iKuai | 结论 |
|---|---|---|---|
| 服务端直连路由器 API | v7 REST（`/rest/...`，HTTPS + Basic）或 API-SSL 8729 | Web 管理接口 `/Action/login`、`/Action/call` | **采用** |
| 路由器上跑 Agent | 可用 container，但资源和兼容性差 | 不可行 | 不采用 |
| 下发脚本由用户手动执行 | `.rsc` 导入 | 无对应机制 | 仅作为降级：导出 RouterOS 脚本 |

服务端用驱动（driver）抽象不同路由器：

```
RouterDriver
  probe()            -> 版本、型号、接口列表、能力集
  read(kind)         -> 当前对象（只返回带 ConfigFlow 标记的对象 + 只读引用）
  apply(plan)        -> 按计划增删改，返回逐项结果
  snapshot()/restore(snapshot)
```

- `RouterOSDriver`：优先 REST（7.1+），不可用时退回 API-SSL；可选生成 `.rsc` 导出。
- `IKuaiDriver`：**只支持 iKuai 4.x**（响应在 `results` 下；部分 func 对普通会话返回 1003）。`probe()` 检测到 3.x 时直接拒绝接入并提示升级。iKuai 的 API 没有公开文档，必须以 `probe()` 实测能力为准，不支持的功能在界面上标为不可用，不能盲写。

## 3. 数据模型

配置空间下新增 `routers` 资源：

```yaml
routers:
  - id: rt_xxx
    name: 主路由
    type: ikuai | routeros
    endpoint: https://192.168.1.1
    auth: { username: ..., secret_ref: ... }   # 凭据加密存储，导出时脱敏
    lan_interface: lan1 | bridge
    managed_prefix: "cf"                        # 写入对象的 comment/tagname 前缀，用于识别归属
    topology:
      proxy_gateway: <mihomo Agent ID>          # 地址从 Agent 记录取，不手填
      dns_server: <oxidns Agent ID>
    features:                                   # 每项可单独开关
      proxy_routes: true
      nat_bypass: true
      dns_redirect: false
      dhcp_bindings: false
    failover: fail_closed | fail_open           # 代理网关离线时的处理（见 7.2）
```

**期望状态的来源**（不让用户重复填写）：

| 期望对象 | 数据来源 |
|---|---|
| 代理网段 | mihomo `fake-ip-range` + 规则集里需要按 IP 进 mihomo 的 IP-CIDR（如 Telegram） |
| 下一跳 | 拓扑中 mihomo Agent 的 host |
| NAT 免转换 | 代理网段 × LAN 网段（排除网关自身） |
| DNS 劫持目标 | 拓扑中 OxiDNS Agent 的 host，**自动排除** OxiDNS、mihomo 和路由器自身 |
| DHCP 绑定 | Agent host + 探测到的 MAC |

## 4. 功能与两种路由器的映射

| 功能 | RouterOS | iKuai |
|---|---|---|
| 代理网段集合 | `/ip firewall address-list`（list=`cf-proxy`） | 路由对象（IP 分组） |
| 代理路由 | 少量网段：`/ip route dst-address=... gateway=<mihomo>`；网段多时：mangle `mark-routing` + 独立 routing table | `static_rt`（逐条）或分流策略 + 路由对象 |
| NAT 免转换（回流） | `/ip firewall nat` srcnat `dst-address-list=cf-proxy action=accept`，**必须排在 masquerade 之前** | `nat_rule` 动作“过滤” |
| DNS 劫持 | dstnat 53 → OxiDNS，`src-address` 排除 DNS 服务器自身 | `nat_rule` DNAT 53，源地址用排除自身的 IP 分组 |
| DHCP DNS 下发 | `/ip dhcp-server network dns-server` | DHCP 服务端 DNS 字段 |
| DHCP 静态绑定 | `/ip dhcp-server lease` | `dhcp_static` |
| 动态地址列表（按 DNS 结果） | 已有：OxiDNS `ros_address_list` / `ros_route` 插件 | 无对应能力 |

RouterOS 侧做完后，可以直接在设置页关联 OxiDNS 的 RouterOS 插件（复用同一份 RouterOS 连接配置），实现“按域名解析结果动态加地址列表”，不必再分别填凭据。

## 5. mihomo Agent 管理回流规则

NAT 免转换只有在 mihomo 主机能把回包送回主路由时才安全，两者必须作为一次整体发布。回流规则由 mihomo 的 Go Agent 负责，ConfigFlow 只下发期望状态。

### 5.1 期望状态（由服务端计算后随发布包下发）

```yaml
return_routing:
  enabled: true
  lan_interface: eth0          # Agent 探测默认路由所在网卡，可覆盖
  router_ip: 192.168.1.1       # 来自路由器接入配置
  lan_cidr: 192.168.1.0/24
  proxy_dst: [198.18.0.0/16, 91.108.4.0/22, ...]   # 与主路由“代理网段”同一份数据
  mark: 0x400000               # 默认避开 mihomo auto-redirect 的 0x2023/0x2024
  table: 104
  priority: 104
```

### 5.2 Agent 实现

- **按 mihomo 运行模式生成规则**：Agent 读取当前生效的 mihomo 配置判断模式。
  - TUN（`auto-route`/`auto-redirect`）：TCP 回包在 output 恢复标记；UDP 回包来自 TUN 网卡（名称取 `tun.device`，默认 `Meta`），在 prerouting 恢复标记。
  - tproxy / redir：只需 output 恢复标记，与社区方案一致。
- **独立 nft 表** `inet configflow_return`：整表原子替换（`destroy table` + 新表一次提交），不触碰 mihomo 自己的表；不启用发行版的 nftables 服务（它会 `flush ruleset`）。
- **策略路由**：路由表 `104`（默认网关与本网段都指向主路由）+ `ip rule fwmark <mark>/<mark> lookup 104 priority 104`；同时关闭 `send_redirects`。
- **持久化**：Agent 启动时按已发布的期望状态自动重新应用（不再依赖 `/etc/local.d` 之类的启动脚本）；mihomo 重启后检查规则仍在，缺失则补回。
- **依赖检查**：缺少 `nft` / `ip`（iproute2）时在预检阶段报错，不进入发布。
- **mark 冲突检查**：mark 与 mihomo 配置中的 `routing-mark`、`auto-redirect-*-mark` 有位重叠时拒绝发布。

### 5.3 健康检查与回滚

- 应用后：nft 表与策略路由存在；通过 fake-ip 访问探测地址成功。
- 失败：删除 `configflow_return` 表、路由表 104 与 ip rule，恢复到发布前状态（发布前不存在则全部删除）。
- 回流规则单独存在时不改变行为（主路由仍做 SNAT，回包本来就发往主路由），因此它可以先于主路由变更上线，作为整体发布的第一阶段。

## 6. 下发流程

```
计算期望状态 → 读取当前状态 → 生成差异（新增 / 修改 / 删除 / 冲突）
   → 预览确认 → 快照 → 按依赖顺序分阶段应用 → 每阶段健康检查 → 失败自动回滚
```

### 6.1 归属与冲突

- 只管理带 `managed_prefix` 标记的对象；其他对象只读，不改、不删。
- 期望对象与一个**非 ConfigFlow 管理**的对象冲突（例如已有同一网段的手工静态路由）时，标为“冲突”，由用户选择：接管（打上标记）、跳过、或先手动删除。不自动覆盖。

### 6.2 分阶段与顺序（来自实际事故的约束）

1. **先旁路网关，后主路由**：一次发布内部按“mihomo Agent 回流规则 → 主路由代理路由 → 主路由 NAT 免转换”的顺序执行；前一阶段健康检查通过才进入下一阶段。顺序反了会出现回包路径不对称而断流。回滚按相反顺序进行：先停用 NAT 免转换，再撤 Agent 回流规则。
2. **DNS 劫持必须排除 DNS 服务器自身**：OxiDNS（以及 mihomo）发出的 53 端口查询不能被劫持回 OxiDNS，否则形成查询风暴。下发前做静态检查，不满足就拒绝下发。
3. **改路由前先确认下一跳可用**：mihomo Agent 在线、fake-ip DNS 可应答。
4. 每个阶段之后做健康检查，失败立即回滚本阶段，不继续下一阶段。

### 6.3 健康检查

- 经 OxiDNS 解析：国内域名为真实 IP，代理域名为 fake-ip，自定义 hosts 正确。
- 环路检测：在 OxiDNS 主机上用 UDP 查询公共 DNS，查一个只存在于自定义 hosts 里的域名，结果**不能**是 hosts 里的内网地址。
- 经代理访问探测地址成功。
- mihomo 连接列表中出现真实客户端 IP（启用 NAT 免转换之后）。

### 6.4 快照与回滚

- RouterOS：应用前导出受管对象（以及冲突对象）为 `.rsc` 片段并保存；回滚时按快照逆向操作，不使用整机备份恢复。
- iKuai：保存受影响对象的完整 JSON（含 id），回滚时逐条恢复或删除新增项。
- 回滚只覆盖本次计划涉及的对象。如果用户在此期间手动改过同一对象（漂移），回滚前先提示，不强行覆盖。

## 7. 运行期

### 7.1 漂移检测

定时读取受管对象，与期望状态比对，在界面上显示“已同步 / 有漂移 / 缺失”，不自动纠正。规则集或 Agent 地址变化后，提示有待下发的差异。

### 7.2 代理网关故障时的处理

ConfigFlow 已有 Agent 心跳。mihomo Agent 离线时：

- `fail_closed`（默认）：保持路由不变，代理流量中断，绝不以家宽 IP 直连（适合对 IP 敏感的服务）。
- `fail_open`：自动停用代理路由和 DNS 劫持，保证基础上网，同时告警。网关恢复后需要手动确认，才重新启用。

## 8. 安全

- 凭据加密存储，界面与导出中脱敏；日志中不打印 API 请求体。
- RouterOS 建议建专用用户组，只授予 `api, rest-api, read, write` 中需要的权限，并启用 HTTPS（`www-ssl`）或 API-SSL；默认校验证书，自签证书需导入 CA。
- iKuai 没有细粒度权限，只能使用管理员账号，需在界面上明确提示风险。
- 安全护栏，下发前强制检查：不管理默认路由、WAN 口相关对象和路由器管理访问规则；不允许把路由器自身或管理网段放进劫持、代理范围，防止把自己锁在外面。

## 9. 分期

| 阶段 | 内容 | 风险 |
|---|---|---|
| P0 | 只读：接入两种路由器（iKuai 仅 4.x）、能力探测、展示当前相关配置与期望状态的差异（漂移报告）；mihomo Agent 上报当前回流规则状态 | 无写操作 |
| P1 | mihomo Agent 回流规则 + 主路由代理路由 + NAT 免转换，作为一次整体发布：预览、分阶段应用、回读校验、逆序回滚（已实现） | 中 |
| P2 | DNS 劫持（自动排除自身）+ DHCP DNS 下发 + DHCP 静态绑定；代理网关故障处理 | 中高 |
| P3 | RouterOS 地址列表按规则集同步；与 OxiDNS RouterOS 插件共用连接；RouterOS `.rsc` 导出 | 低 |

建议先做 P0，用现有家庭环境（iKuai 4.x）把驱动和差异模型跑通，再进入 P1。

## 10. 已确定

- iKuai 只支持 4.x，检测到 3.x 拒绝接入。
- mihomo 主机上的回流规则由 Go Agent 管理，与主路由的代理路由、NAT 免转换作为一次整体发布（见第 5 节）。
- 现有手工配置直接接管：差异报告把“内容正确但没有 ConfigFlow 标记”的对象标为“可接管”，P1 发布时为其打上标记，不删除重建。
- P0 已实现（分支 `feat/router-integration-p0`）：路由器接入、iKuai 4.x / RouterOS 驱动（只读）、期望状态计算、差异报告、mihomo Agent `GET /api/return-routing`、“上游路由”页面。
- 代理网段只来自 fake-ip 网段与用户在路由器上**显式勾选**的规则集（按 IP 路由），不按策略推断。
- P1 已实现（分支 `feat/router-integration-p1`）：
  - **Agent** `PUT /api/return-routing` 应用期望状态：独立表 `inet configflow_return`（单事务整表替换）、路由表 104、`ip rule priority 104`、关闭 `send_redirects`；校验 mark 与 mihomo `routing-mark`/auto-redirect 标记不重叠、路由表与 `iproute2-table-index` 不冲突、优先级小于 `iproute2-rule-index`；`adopt_legacy` 接管同 mark 的手工表——先应用受管规则再删手工表，并把引用它的 `/etc/local.d/*.start|stop` 改名为 `*.configflow-disabled`，手工表导出保存。期望状态持久化在 Agent 目录 `return-routing.json`，Agent 启动时重新应用。`DELETE ?restore_legacy=1` 删除受管规则并恢复被接管的手工规则。
  - **只标记经主路由转发的连接**：标记规则匹配主路由的源 MAC（Agent 从邻居表解析）。客户端直接把 Agent 主机当网关，或收到主路由的 ICMP 重定向后直连时，主路由没见过这条连接，回包若被强制送回主路由会被丢弃，因此这类连接不打标记、直接回包。
  - **回流网段** = 期望代理网段 ∪ 发布后所有 NAT 免转换规则的目的网段（免转换但不打标记会导致回包不对称）。
  - **iKuai 写入**：静态路由 add/edit/del、NAT 规则 add/edit，参数与字符串编码与 Web UI 一致（`%`→`%25`、空白→`%20` 等）；edit 先读回完整记录再改，回滚用读回的原记录。名称（tagname）必填且 1–15 字符，新增对象用 `cf_rt_<hash>` / `cf_nat_<hash>`；受管备注写成 `cf-<原备注>`（不用冒号，iKuai 备注字符受限），识别时 `cf:` / `cf-` 都认。
  - **发布**：`POST /api/routers/<id>/plan` 只生成计划（不写入，带指纹）；`POST /api/routers/<id>/apply` 重新计算计划，指纹不一致返回 409，一致才按“回流规则 → 代理路由 → NAT 免转换”执行，每阶段回读校验，失败逆序撤销。冲突网段整体跳过（路由、NAT、回流都不处理）并提示。
  - **RouterOS**：路由器部分只生成 `.rsc` 脚本由用户导入，回流规则阶段照常由 Agent 执行。

## 11. 待确认的问题

1. 代理网段里除 fake-ip 和 Telegram 外，是否需要支持用户自定义 IP 段（例如部分游戏、IP 直连服务）？
2. 多台主路由 / 多个配置空间共用一台路由器时，归属标记如何区分？
3. RouterOS 侧走“静态路由”还是“mangle + routing table”，是否按代理网段数量自动选择？
4. 现有手工配置（iKuai 上的 fake-ip / Telegram 静态路由、`NAT_1`，mihomo 主机上的 `inet mihomo_return` 表）接入时如何迁移：接管为受管对象，还是要求先手动删除？
