# NexHome · 络家：家庭网络总控产品、架构与 UI 规划

调研日期：2026-09-24。定位：基于 iKuai + Mihomo 的家庭网络控制面；不引入 MosDNS。

品牌定名：**NexHome · 络家**，标语「看清全网，掌控连接」。原型使用新品牌；下文 ConfigFlow 的现有能力与源码名称保留历史含义。仓库目录与已有部署服务标识暂不改名，局域网预览地址不变。第 14 节截图记录的是改名前的验证版本。

## 0. 本轮交付与边界

- `home-network-control.html`：无外链依赖的本地单文件交互原型，UTF-8，可直接打开。包含 12 个主视图、详情抽屉、配置编辑、发布成功/失败回滚演示、明暗主题及 3 种布局模式。
- 本文：互联网调研、代码事实、功能映射、数据口径、实时应用架构、安全边界、无 MosDNS 迁移与实施验收。
- 原型数据是明确标注的演示快照；IP/设备名部分参考附件，只表达信息组织，不证明现场拓扑或真实性能。原型不访问路由器、不登录 Mihomo、不请求订阅、不执行 DNS 查询，也不写生产配置。
- 本轮不是完整后台实现；现有源码、部署配置和运行网络保持不变。“不要 MosDNS”已进入目标架构，不在设计阶段直接卸载现场服务。
- `[现有]` 表示代码/文档已证实；`[设计]` 表示本方案要求；`[待探测]` 表示只有目标设备/版本才能确认。

## 1. 产品结论

**不是三套后台 iframe 拼接，也不是另一个节点选择器。** 保留 ConfigFlow 作为配置意图源，加入 iKuai 全网观测、Mihomo 运行态、爱快分流编排和可确认的发布控制。

四个日常问题必须在一个界面回答：

1. 家里的网络现在是否正常？问题在线路、网关、旁路、DNS 还是节点？
2. 哪台设备在占带宽？它从哪个 AP/网段接入，访问了哪个目标？
3. 为什么这条连接走了该线路/节点？命中了哪个规则、哪个版本？
4. 我修改订阅、规则或策略后，是否真的生效？失败能否恢复？

主下钻路径：

`家庭站点 → 线路 / 网关 → 网段 / AP → 设备 → 连接 → 命中规则 → 策略组 → 节点 → 发布版本`

“全部网络信息”的可实现含义：所有已接入数据源能观测到的信息，并明确显示缺失范围。不是破解 HTTPS、自动获得所有交换机端口，或保证看见终端的所有加密 DNS。

## 2. 参考对象与调研结论

### 2.1 iKuai 与附件一

附件呈现系统概况、WAN、AP、无线/有线终端、TCP/UDP/ICMP 连接、速率和历史曲线。值得保留的是**从路由基础设施到终端的层级**，不是其浅色表格堆叠。

[iKuai 终端监控官方说明](https://www.ikuai8.com/zhic/ymgn/lyym/ztjk/7cf09.html)证实：终端详情包含基础信息、连接详情、流量分布和历史记录；详情能显示线路、目标地址/端口、外网地址、本地端口。页面刷新周期与监控窗口也有具体定义。其 IPv4 监控是近期通过路由产生流量的客户机，不是永不失真的在线设备清单。

[iKuai 线路监控](https://www.ikuai8.com/support/ymgn/lyym/ztjk/cda78.html)覆盖网口状态、线路监控和线路状态监测。

**设计借鉴：** WAN 与设备是主实体；全网连接来自 iKuai；网关在线、线路可达、代理可用和 DNS 可用必须独立标记。

### 2.2 Mihomo 控制台与附件二

附件呈现嵌套策略组、当前节点、延迟、连接、规则、日志与实时速率。保留“当前选择可见、候选节点可比较、运行切换反馈快”；避免把几十个节点卡片当首页。

- [MetaCubeXD](https://github.com/MetaCubeX/metacubexd)：实时流量、策略/延迟测试、连接、规则搜索、日志和响应式 UI。
- [Zashboard](https://github.com/Zephyruso/zashboard)：作为节点卡片、策略与连接交互参考；不假设已安装版本具备仓库最新的全部功能。
- [Mihomo API](https://wiki.metacubex.one/api/)：运行观测和控制接口的依据，详见第 6 节。

**设计借鉴：** 代理策略是二级工作区；从连接详情解释命中规则和代理链，比单独看规则表更有价值。

### 2.3 iKuai 接口并不只有一种

- [官方 Open API 3.0](https://open.ikuai8.com/doc/3.0/)走云端平台认证、设备授权和开放审核流程。
- ikuai-bypass 使用本地 Web 控制台 `/Action/login`、`/Action/call`，操作 `custom_isp`、`route_object`、`stream_domain`、`stream_ipport` 等表。
- **两者不能混为“稳定官方 API”。** 本地接口按固件建立能力与兼容矩阵；未知版本先只读，确认字段与回滚后才能写。
- 附件中的固件/内核版本只是截图时的证据，不等于当前部署版本，更不能直接套用最新 Wiki 的全部字段。

## 3. 当前 ConfigFlow 能力与缺口

源码依据：`frontend/package.json`、`frontend/components.json`、`backend/common/`、`backend/routes/`、`backend/converters/mihomo.py`、`backend/agents/`。

| 能力 | 当前事实 | 总控中的处理 |
|---|---|---|
| 配置空间 | Profile JSON 隔离；system.json 放系统、Agent 与空间索引 | 沿用，不另建一套规则存储；网络站点与 Profile 分开建模 |
| 订阅与节点 | Sub-Store 解析、缓存、手工节点、按名称正则筛选 | 保留来源追踪与敏感 URL 脱敏；新增更新差异 |
| 订阅聚合 | 多订阅、手工节点、regex_filter，生成 provider | 沿用语义；区域/排除关键词表单属于新增 UX，不能冒充已有字段 |
| 策略组 | 支持组引用、节点/订阅/聚合引用与排序；编译为 proxies/use | 加入实际 now、健康、选中节点与配置意图对比 |
| 规则 | `rule_configs` 按列表顺序编译；rule 与 ruleset 共用优先级列表 | 原样保留 first-match；规则库与策略规则分开 |
| 配置生成 | 预览、生成文件、动态配置 URL | 保留高级 YAML 预览/导入导出；默认操作改为校验并应用 |
| Agent | 注册、心跳、指标、服务日志、推送与重启 | 扩展为可确认的部署执行器，不把心跳当代理健康 |
| 备份 | JSON 原子写、旧版备份；Agent 时间戳目录；WebDAV 手动备份 | 发布快照补齐全 bundle；脱敏导出不等于完整可恢复备份 |
| MCP | 复用资源 CRUD、生成、Agent 管理与推送 | 与 UI 使用同一发布授权/任务语义，不能绕过确认 |
| iKuai 集成 | 未发现对应实现 | 新增 Adapter 与版本探测，不称为已有能力 |
| Mihomo 运行控制 | 未发现 Controller API 调用 | 新增运行态观测与操作，不等同于 Agent 重启 |

### 3.1 当前“保存”不等于“实时生效”

1. CRUD 只保存期望配置；没有自动调用 Agent 推送。
2. 动态 provider 会在客户端拉取时生成/更新；编译器当前订阅/聚合 interval 为 3600 秒，rule-provider 为 86400 秒。这是拉取，不是即时推送。
3. `POST /api/agents/<agent_id>/push-config` 编译后推给 Agent。
4. Go Agent `ConfigUpdateHandler` 启动后台 goroutine 后立即返回 success。中心端此时更新的版本来自发送内容，不是内核运行确认。
5. 当前 Go 心跳没有回报最终已应用配置版本。没有可靠的 desired / delivered / applied 区分。
6. 当前服务应用路径是重启命令，不是 Mihomo Controller 热重载；`restart=false` 只是落盘。
7. 下载失败、文件处理失败或重启失败时，当前备份恢复并不覆盖所有分支。不能直接把现有 push 包装成“可靠一键发布”。

关键证据：

- `backend/common/config_repository.py`：配置 JSON 的事务和原子写，**不是跨设备事务**。
- `backend/routes/generate.py`、`backend/routes/config.py`：预览/生成/动态配置输出。
- `backend/converters/mihomo.py:generate_mihomo_config`：按需编译及 provider 更新周期。
- `backend/routes/agents.py:push_config_to_agent`、`backend/agents/manager.py:push_config_to_agent`：中心端推送与确认含义。
- `backend/agents/go-agent/routes/config.go:ConfigUpdateHandler/handleConfigUpdateAsync`：异步受理、备份、写入与重启。
- `backend/agents/go-agent/client.go`：心跳字段。

### 3.2 不无意改变现有契约

- 当前新增规则插在队首；原型建议显式插在 MATCH 前。正式实现必须统一前后端的插入位置参数及回归行为，不能只调整视觉。
- 当前策略组多个筛选正则在编译时可能以 OR 合并，不能展示成“同时满足”。
- 不凭“协议+地址+端口相同”去重节点：认证、传输、TLS、插件等都可能不同。若新增去重，需独立定义语义与预览。
- Surge 支持不是本需求的删除对象：家庭总控主路径聚焦 Mihomo，原有高级导出可保留。明确移除的目标是 MosDNS，不顺带删除无关能力。

## 4. ikuai-bypass 全功能映射

以公开的 [joyanhui/ikuai-bypass](https://github.com/joyanhui/ikuai-bypass) 为调研对象；未在相邻本地目录找到同名项目。若用户使用私有 fork，后续接入时以那个 fork 的版本差异为准。

| 功能面 | 调研事实 | 平台落点与限制 |
|---|---|---|
| 自定义运营商 | 远程 IPv4/IP 列表，分块同步 custom_isp | 爱快分流 → 运营商地址集 |
| IPv4 分组 | 写 route_object 的 IPv4 分组 | 设备组/IP 组，与分流规则关联 |
| 端口分流/下一跳 | 支持协议、地址/源分组、反向匹配、线路、nexthop、优先级及负载模式 | 爱快分流 → 入口策略；上游目前传空 src_port/dst_port，任意端口列表是新增适配，不是现成功能 |
| 域名分流 | 域名列表→stream_domain；可按源地址/组和线路；当前代码优先级 31 | 列出来源、数量、固件限制和生效状态；不当 DNS 拦截 |
| IPv6 分组 | 可同步 IPv6 route_object | 单独标识能力；不能承诺 IPv6 端口分流等价支持 |
| 定时与手动同步 | cron、先执行/等待计划、once、组合模式 | 同步任务、下次执行、历史结果、手动预演 |
| 清理 | IKB / IkuaiBypass 等标记、tag 匹配；cleanAll 清托管对象 | 精确所有权和依赖检查后预演，不沿用宽泛 tag 子串删除 |
| 下载方式 | 自定义/系统/智能 HTTP 代理，GitHub 前缀代理 | 订阅源高级设置；禁止凭据和 URL 出现在日志 |
| 控制台功能 | 配置在线保存、诊断、运行/停止、计划启停、状态、历史/SSE 日志、清理 | 纳入总控统一鉴权与任务模型，不给浏览器直连原 API |
| 广告屏蔽 | 简介有宣传，未证实独立 adblock 配置或引擎 | `[设计]` Mihomo REJECT 规则；只覆盖经过内核的流量，不伪造爱快广告 API |
| 旁路自愈 | 文档依赖特定多 WAN 拓扑和手工规则，未找到独立探活状态机 | `[设计]` 新增独立守护；不是把上游已有功能换个按钮 |

源码/文档依据：

- [config.yml](https://github.com/joyanhui/ikuai-bypass/blob/main/config.yml)
- [router-mode.md](https://github.com/joyanhui/ikuai-bypass/blob/main/docs/router-mode.md)
- [cli-params.md](https://github.com/joyanhui/ikuai-bypass/blob/main/docs/cli-params.md)
- [update.rs](https://github.com/joyanhui/ikuai-bypass/blob/main/crates/core/src/update.rs)
- [stream_ipport.rs](https://github.com/joyanhui/ikuai-bypass/blob/main/crates/core/src/ikuai/stream_ipport.rs)
- [clean.rs](https://github.com/joyanhui/ikuai-bypass/blob/main/crates/core/src/ikuai/clean.rs)
- [web.rs](https://github.com/joyanhui/ikuai-bypass/blob/main/apps/cli/src/web.rs)

### 4.1 固件、写入与许可证风险

- 模式字段随固件变化，项目 FAQ 有 ≤4.0.120 与 ≥4.0.210 的区别；中间区间不自行猜测。
- 名称长度、请求容量、IPv4/IPv6 分块限制按固件探测。原型中的 `cf:home:` 只是所有权可视化，正式实现不能照搬长名称而超出固件限制。
- 名称/备注/服务端映射共同确定 owner；保护手工规则和其他工具对象。接管旧 IKB 规则需要一次显式迁移，不能让两个写入器长期同时管理同一对象。
- 源列表为空与“删除所有规则”是两个不同意图；默认阻止意外清空。删除前检查引用，增改确认后再清理旧对象。
- 上游为 [AGPL-3.0](https://github.com/joyanhui/ikuai-bypass/blob/main/LICENSE)，当前 ConfigFlow README 标 MIT。直接复制、修改或组合分发需评估许可义务，不以“独立 CLI”自动豁免。推荐清晰的独立适配边界；本轮未引入上游代码。

## 5. 信息架构与关键 UI

### 5.1 一级导航

| 工作区 | 页面 | 核心内容 / 操作 |
|---|---|---|
| 全网观察 | 网络总览 | WAN 吞吐、终端、连接、逻辑拓扑、基础设施、趋势、活跃设备 |
| 全网观察 | 网络拓扑 | 物理接入与逻辑转发分层，点击节点下钻 |
| 全网观察 | 终端设备 | IP/MAC、位置、AP/有线、吞吐、策略、连接详情 |
| 全网观察 | 实时连接 | 来源、目标、协议、观测来源、命中规则、策略链 |
| 流量编排 | 代理策略 | 配置意图与实际选择；手动切换；自动组健康；节点探测 |
| 流量编排 | 规则中心 | 优先级列表、规则仓库、来源、引用、草稿编辑 |
| 流量编排 | 订阅与节点 | 来源、聚合、节点库、更新差异、配额可用性 |
| 流量编排 | 爱快分流 | 运营商、IPv4/IPv6 分组、域名、入口策略、同步任务、守护 |
| 流量编排 | DNS 解析 | 上游/分流/内网解析、Fake-IP 边界、主动诊断 |
| 配置运维 | 配置发布 | 期望/受理/应用/确认版本、差异、目标状态、回滚 |
| 配置运维 | 事件日志 | 配置变更、执行结果、旁路事件、运行日志 |
| 设计说明 | 平台蓝图 | 原型内嵌的规划摘要、能力边界、来源链接 |

生产版的空间管理、Agent/凭据接入、备份恢复、权限和通知归入全局设置，不挤占网络首页。原型的 Home 是固定演示空间，不冒充已实现的多站点切换。

### 5.2 四条完整操作链

1. **定位问题：** 总览异常 → 设备 → 连接 → 规则/策略/节点 → 来源配置；保留面包屑和筛选上下文。
2. **改策略：** 在策略组选择节点 → 立即运行操作 → 读取 now 确认 → 明确提示“仅运行态” → 用户可保存为意图。
3. **改规则/订阅：** 编辑/更新预览 → 显示受影响组和引用 → 编译校验 → 自动应用或手动发布 → 版本确认；失败保留草稿。
4. **旁路异常：** 显示故障证据与影响设备 → 独立守护按预设直连/断网 → DNS 与入口同步降级 → 恢复冷却 → 显示审计轨迹。

### 5.3 视觉设计系统

- 模式：独立的全面改版方案（overhaul concept），不修改现有路由/组件契约。品牌更新为 NexHome · 络家；保留中文界面、已有 Vue 技术栈及资源模型。
- Design Read：家庭管理员使用的高密度网络工作台；克制的仪器化控制室，而非游戏 HUD。视觉变化 6/10、动效 3/10、密度 8/10、外部素材依赖 2/10、原视觉保真 4/10。
- 定位四问：页面用于操作而非展示封面；笔记本阅读距离；冷静、精确、有掌控感；首屏容纳状态/KPI/拓扑/趋势，下部放设备，复杂配置进入子页和抽屉。
- 色板：石墨 `#0b1014`；表面 `#111a20`；分割 `#233139`；正文 `#e7eff1`；薄荷青 `#77e2c3` 表达主操作/直连；代理紫 `#b4a0f7` 表达代理链；琥珀 `#eac38a` 表达风险。颜色须辅以文字，不独立传递状态。
- 排版：Avenir Next / PingFang SC；数值使用 SFMono/Consolas 与等宽数字。标题 26–28px；高密度表格 11–13px；生产版必须支持用户字号缩放，极小字仅用于辅助标签。
- 间距：4px 基础；8/12/16/20/24/32 级；桌面 218px 侧栏。
- 圆角：面板 12px，控件 6–8px；不使用夸张胶囊和全页玻璃。
- 层次：普通卡片靠底色/细边框，阴影只给抽屉/弹层；拓扑保留稀疏网格和细线，不用装饰性 3D 地球。
- 动效：短过渡 150–220ms，拓扑虚线慢速流动；能关闭，并尊重 reduced-motion。原型不把数字随机跳动伪装成实时采集。
- 3 个变体：均衡控制室、高密度终端、拓扑沉浸；再提供明暗主题。所有变体在同一 HTML 的 Tweaks 内切换。
- 状态：默认、悬停、选中、焦点、禁用、异步加载、空列表、采集过期、发布失败/回滚。对话框支持 Esc、焦点限制和后台 inert。

### 5.4 技术与组件映射

项目实际已是 Vue 3 + Reka UI + Tailwind 4 + ECharts，`components.json` 为 shadcn-vue new-york/zinc。README 的 Element Plus 描述落后于 package.json；**不为“用 shadcn”重写为 React**。

正式实现复用现有 `components/ui`、`components/common`、`components/shell`：Sidebar/导航壳、Card、Table、Tabs、Badge、Button、Sheet、Dialog/AlertDialog、Command、Field/Input/Select、Switch、ScrollArea、Skeleton、Empty、Sonner；图表继续 ECharts。

HTML 原型使用原生控件和内联 SVG 表达布局/交互，无 CDN、无外部字体、无图片依赖。它是设计验证载体，不声称已安装或运行生产 shadcn 组件。

## 6. 实时生效：配置意图与运行控制分离

### 6.1 操作分类

| 用户动作 | 执行路径 | 完成条件 | 持久性 |
|---|---|---|---|
| 手动选择节点 | `PUT /proxies/{name}`，再读取该组 | 返回成功且 now 与目标一致 | 单独说明运行选择/固定配置；不要依赖未知内核缓存设置 |
| 延迟测试 | 单节点或组 delay API | 有超时、测试 URL、时间戳和结果 | 观测数据，不是策略保存 |
| 调整运行模式 | `PATCH /configs`，回读 | 确认实际 mode | 要求明确是否写回期望配置 |
| provider 内容更新 | `PUT /providers/proxies/{name}` / `PUT /providers/rules/{name}` | 接口接受后核验 provider 元信息与探测 | 与发布快照绑定；不能只看 HTTP 204 |
| 改规则顺序/组结构/DNS | 编译 bundle + 目标内核校验 + `PUT /configs?force=true` | Agent 应用结果 + 运行态验证 + 健康探测 | 落盘的不可变版本，可回滚 |
| 断开指定连接 | `DELETE /connections/{id}` | 该连接不再存在 | 高影响临时操作；二次确认 |
| 临时禁用规则 | 能力允许时 `PATCH /rules/disable` | 回读 disabled | API 文档标注重启失效，不当作持久编辑 |
| 爱快分流变化 | Adapter：读→diff→按依赖应用→回读 | 每个受管对象一致，探测通过 | 独立版本和补偿步骤 |

路径中的名称必须编码；每个 endpoint 按目标内核版本探测。较新 API 字段可能在附件所示旧版内核中不存在。

### 6.2 “实时”不等于每次敲键盘都重启

`[设计]` 配置空间设置「自动应用」与「先存草稿」两种模式：

- 节点切换：明确提交后立即调用运行 API。
- 普通规则/策略编辑：表单提交并校验后触发自动应用；合并短时间重复保存，建议 0.5–1 秒防抖，不以键入中间态发布。
- 订阅内容变化：拉取完成、引用校验通过后按已授权策略触发 provider 更新/发布；订阅站并无推送能力时，不能承诺源端变化零延迟。
- 路由/DNS/批量删除/故障行为等高风险改动：始终展示差异与影响，需要确认，不用“实时”绕过保护。
- 性能目标而非已实测承诺：本地控制 API 操作希望 1–2 秒内显示确认；全量发布进度必须可见，不给未经测量的固定秒数保证。
- 原型展示手动发布与运行切换，重点验证确认/失败语义；自动应用调度由正式后端实现。

### 6.3 发布状态机

`draft → validated → staged → applying → verifying → confirmed`

失败路径：`failed → compensating → rolled_back`；补偿也失败则 `degraded / needs_attention`，**不能显示“已回滚”**。

每个任务保存：`job_id`、`site_id`、`profile_id`、`desired_revision`、`base_applied_revision`、bundle 内容哈希、触发人、每目标阶段/错误、回滚版本、确认时间。

每个执行目标保存：`desired_revision`、`delivered_revision`、`applied_revision`、`verified_at`、`capabilities`、`last_seen`、`health`。`HTTP accepted` 只推进 delivered，不推进 applied。

落地关键点：

1. 从同一不可变配置快照编译主 YAML 与 provider/ruleset bundle；冻结外部依赖内容或明确标记外部可变依赖。否则所谓版本无法复现。
2. 暂存完整文件并用**目标版本**内核执行配置检查；校验失败不动活动目录。
3. 保留已知良好版本；在目标文件系统内原子切换单目标活动版本，再加载，不先删除当前目录。
4. Mihomo `/configs` 不是完整 YAML/全部规则的内容哈希回显。确认需结合 Agent 的落盘/加载结果、proxies/rules/providers 的可观测投影和真实连通探测，不虚构“GET 一下就验证整份配置”。
5. 发布锁按 site/target 隔离，同目标只允许一个写入任务；用版本前置条件拒绝旧草稿覆盖新配置。幂等 job 避免断线重发造成重复写入。
6. 不能声称 iKuai 与 Mihomo 跨系统原子事务。通常先准备旁路和 DNS、确认下游，再调整入口；移除时反向处理。具体计划按依赖生成，失败按已完成步骤补偿。
7. 默认保留旧连接；用户可另行授权断开特定设备/策略的连接。不要宣传“切换节点会迁移已有 TCP”。
8. 外部面板改变运行节点或手工改爱快时标记漂移，不悄悄覆盖；用户选择采纳到意图或按期望重新应用。

## 7. 无 MosDNS 的网络与 DNS 方案

### 7.1 职责切分

- iKuai：主网关、WAN、DHCP、NAT、VLAN/AP 可用信息、设备/网段粗粒度入口策略。
- Mihomo：代理与直连的应用级规则、策略组、节点、DNS 解析/分流及 Fake-IP（在拓扑允许时）。
- ConfigFlow：统一配置源、订阅/规则编译、观测关联、发布与审计；控制面停机不能让当前转发停机。
- 独立守护：位于不会随代理容器一起消失的执行位置；网络探测、切换、冷却与恢复，不依赖 UI 页面开着。

### 7.2 解析链路

`客户端 → Mihomo DNS → nameserver-policy / direct-nameserver / proxy-server-nameserver → 上游`

- DHCP 只能发普通 DNS IP，不能告诉客户端“用 1053”。若选 1053 监听，需真实可用的 :53 转发路径；否则直接在合适的接口提供 TCP/UDP :53。
- `default-nameserver` 负责加密 DNS 域名的引导，节点域名用 `proxy-server-nameserver` 避免代理依赖循环。
- 内网域名及静态 hosts 迁移到独立于 MosDNS 的 ConfigFlow 字段，编译到 Mihomo hosts/定向 DNS；内网权威源不能再把查询转回 Mihomo 形成循环。
- `respect-rules` 是否启用按解析路径设计；开启时配置必要的节点域名解析，不盲目堆叠 fallback。
- 混合直连/代理网络优先明确哪些设备由 Mihomo 管理。Fake-IP 只下发给能把虚拟地址路由回内核的客户端/路径；否则使用真实地址模式或有证据的例外，不把所有客户端一刀切。
- IPv6：RA/DHCPv6、AAAA 响应、路由、转发、透明代理与泄漏检查作为同一验收。能同步 IPv6 地址集不代表 IPv6 代理已闭环。
- 故障策略同时覆盖路由和 DNS；严格设备 fail-closed，允许降级的设备才 fail-open。双 DNS 地址并不保证所有客户端严格按顺序故障切换。

### 7.3 具体迁移切点

当前代码有真实 MosDNS 耦合，不能只删页面：

- `backend/converters/mihomo.py:parse_mosdns_custom_hosts/sync_mosdns_hosts` 读取 `mosdns.custom_hosts`；先迁移至中立 hosts schema，再移除旧依赖。
- Go Agent `ENABLE_MOSDNS` 会调整 DNS listen / tun dns-hijack 到 1053；安装脚本与 Compose 也包含 MosDNS 分支。
- MosDNS 独立 routes、converter、默认配置、Agent service_type、下载目录与自定义文件处理均在最终 cutover 清理范围。

顺序：导出/备份当前配置 → 建立 Mihomo DNS 与内网 hosts → 少量设备验证 → 验证 :53、TCP/UDP、内网/公网/节点域名、IPv4/IPv6、重启和失效恢复 → 迁移 DHCP/转发 → 观察后停用 MosDNS → 清除旧服务、环境变量、schema、生成器和入口。完成后生产主路径不留 MosDNS 兼容分支。

## 8. 数据采集与指标口径

### 8.1 统一实体

- `Site`：家庭网络及其入口/地址域；不直接等于一个 Profile。
- `Gateway / Interface / Link / NetworkSegment / AccessPoint`：物理接入可知范围。
- `Device`：稳定内部 ID；地址以带时间窗的关联记录保存，不能拿 IP 当永久身份。随机 MAC、静态地址、VPN 和 SNAT 均可能降低关联可信度。
- `Connection`：来源采集器、原始 ID、时间、五元组、域名可用性、命中规则/代理链、关联证据、连接建立版本。
- `Policy / Rule / Provider / Node`：复用配置资源 ID；名称只是显示名，改名需要完整引用迁移。
- `Revision / DeploymentJob / Event`：意图、实际应用、变更历史。

所有观测统一带：`source`、`observed_at`、`scope`、`freshness`、`quality`（observed / correlated / unknown）。推断链不能与直接观测用同一种强确定性样式。

### 8.2 指标不可乱加

| 指标 | 主口径 | 禁止行为 |
|---|---|---|
| 全网 WAN 吞吐 | iKuai 所有 WAN 的同时间窗接口计数增量 | 把 Mihomo throughput 再加一遍 |
| 经代理流量 | Mihomo traffic / connections，注明实例与采样 | 当作全网流量或全部历史 |
| 在线终端 | ARP/DHCP/AP/终端活跃证据与定义 | 把近期活跃列表当精确全量在线 |
| 活跃连接 | iKuai 全网、Mihomo 子范围分别显示 | 将两个连接数相加 |
| 设备历史 | 已采集的时间序列与租约地址归属 | 用当前 IP 回填过去的全部流量 |
| 代理延迟 | 测试 URL、节点、时间、超时 | 等同 WAN ICMP 延迟或网速 |
| 规则命中数 | 能力探测后读取统计，或说明基于连接样本聚合 | 未采集时显示 0 / 凭空产生百分比 |
| DNS 历史/命中率 | 仅在额外采集链确认后提供 | 把 `/dns/query` 当成全量 DNS 日志 API |
| 订阅流量配额 | 来源实际给出的 subscription metadata | 没有元数据就估算为 0 GB |

### 8.3 采集与存储建议

- Mihomo：服务端维护 `/traffic`、`/connections`、`/logs` 等流；一个上游流由多个浏览器复用，避免每标签页重复采集。
- iKuai：先参考 2–5 秒级轻量轮询，按固件负载测量再调整；重查询降低频率。失败有退避，状态显示过期，不抹成零。
- UI：以 SSE 或 WebSocket 接受已归一化事件；重连先取快照，序列号去重，页面隐藏降低重绘频率。
- 家庭规模优先复用现有部署与 JSON 意图源，观测/任务可用 SQLite WAL 单写者；先不引入 Kafka/K8s。只有测量证明需要时再换时序库。
- 建议保留策略（待容量验证）：短期 1–5 秒粒度、长期分钟聚合；连接/域名日志短期且可关闭，敏感隐私数据不无限保留。

## 9. 安全与故障边界

- 浏览器只访问总控后端，不携带爱快密码、Mihomo secret 或订阅 token。
- 管理接口只绑定可信管理网/回环，通过 TLS 或受控隧道使用；token 不能补偿明文外部网络传输。
- 读者/操作者/管理员权限分离；发布、断连接、路由清理、故障直连、升级分别授权和审计。
- iKuai 自签证书优先固定信任或配置 CA；不能把“默认忽略证书”当安全设计。
- 订阅 URL 请求要约束重定向、目标地址与日志脱敏；接口即使允许内网源，也应通过明确的受信任源配置而非任意请求。
- 路由规则只管理带 owner 的对象；不可删除未知对象或清空整表。
- UI/控制面离线时，已应用配置继续工作；独立守护继续按最后确认策略运行。
- 真实写入验收必须在限定设备/地址组内进行，不能用全网清理、生产断连或重启来验证 UI。

## 10. 分阶段实施与验收

| 阶段 | 交付 | 可观测验收 |
|---|---|---|
| A：接入与只读 | iKuai/Mihomo Adapter、版本能力探测、来源/时间戳、全网总览、设备/连接关联 | WAN 与原面板同窗口一致；Mihomo 子范围不重复计数；断开采集显示过期；SNAT/未知链路标未知 |
| B：实时控制与发布 | 节点切换、不可变 bundle、Agent job/回报、校验、hot reload、运行确认、发布锁/回滚 | 改规则后新连接命中变化；内核拒绝时旧配置仍可用；重启后保留已确认配置；重复提交不重复写；部分失败不报成功 |
| C：爱快纳管 | 运营商、域名、IPv4/IPv6 分组、入口策略、计划、清理预演、旧 IKB 对象迁移 | 只影响试点 owner；手工规则不变；空源不误删；版本不支持则写入受阻；引用检查防止删活动分组 |
| D：DNS 与守护 cutover | 中立 hosts、Mihomo DNS、故障直连/断网、DNS 降级、独立守护、彻底移除 MosDNS | :53 TCP/UDP 与域名正确；IPv6 无意外绕过；代理/总控分别停机行为符合预设；严格设备不泄漏；恢复不抖动 |
| E：运营完整性 | 多空间/Agent 设置、历史、备份恢复、审计、MCP 同契约、移动端 | 备份可恢复实际运行 bundle；权限不能绕过；操作可追溯到用户/版本/目标；手机能定位设备并安全确认变更 |

**优先做“设备 → 连接 → 规则 → 一次变更真的生效”的纵向闭环，再铺更多统计图。** 全部阶段属于目标平台范围，不把未实现阶段包装成已完成产品。

## 11. 交付证据

在 `root@10.0.0.49` 的 Chromium 中执行交互与视觉检查，本机未运行测试/构建脚本：

- 桌面 1512×1080 与移动端 390×844：逐一切换全部 12 个主视图，标题正常，均无页面级横向溢出；宽表格在组件内部滚动。
- 设备搜索 → MacBook 详情 → 连接链路 → 规则中心 → 编辑 → 发布差异，完整走通。
- 失败发布：期望 r25、运行 r24、草稿保留，显示“确认失败 · 已回滚”；再次成功发布后两者均为 r25，草稿清空。以上是原型状态机，不是真实设备回滚。
- 节点切换后当前出口与延迟变化；“固定为配置”单独加入草稿；新增草稿后显示期望/已确认版本分离。
- 订阅更新预览、聚合筛选保存、IPv6 分组同步差异、旁路故障/恢复演示、受管清理的引用保护均已操作。
- 唯一 MATCH 校验：重复新增被拒绝；有效域名规则保存成功。
- DNS 示例返回明确标注的文档保留地址；非示例域名显示“未提供演示数据”，没有编造真实查询结果。
- 全局搜索、Esc 关闭抽屉、采集过期时禁止发布、空数据态、三个布局变体已操作；发布中快捷键不会覆盖进度弹层。
- 深色/浅色实际截图检查；修正了浅色主题继承和主按钮对比度问题。最终页面错误记录为空，资源记录中没有外部 HTTP 请求。
- 远程专用验证浏览器已退出，临时浏览器 profile 与远程 HTML 副本已清理。

截图：`home-network-desktop.png`、`home-network-policies.png`、`home-network-light.png`、`home-network-mobile.png`。

没有访问真实网关/内核；真实接入、配置热加载、固件兼容性、流量计量与故障恢复仍须按第 10 节在受控现场验收。

## 12. 范围扩展：服务发布，替代 Lucky 的对应能力

用户新增要求：Nginx 反向代理、ACME 证书管理、DDNS 更新当前公网地址，并以替代 Lucky 为目标。本节为设计范围，尚未加入现有 HTML 原型或实现后台。

- **反向代理**：以“服务”为中心关联域名、监听入口、HTTP/HTTPS 上游、WebSocket/SSE、访问控制、日志和健康检查。发布经过配置校验、平滑加载及真实请求确认，不以 reload 返回值代替业务可用性。
- **证书中心**：复用成熟 ACME 客户端，支持签发、自动续期、域名匹配、部署关联、私钥保护及失败恢复。家庭场景优先 DNS-01，支持通配符，不要求入站 80；签发成功、部署成功、实际对外证书一致分别记录。
- **域名与 DDNS**：A/AAAA 独立任务，绑定 WAN/地址来源与记录；分别显示 WAN 地址、外部出口、服务商记录和公共解析。公网地址探测不能误经 Mihomo 代理。IPv6 选择实际服务所在的可达地址，不发布临时隐私地址。
- **公网入口**：记录监听、NAT/防火墙、IPv4/IPv6 可达性；CGNAT、端口限制、DNS 缓存及 Cloudflare 代理状态都影响验证，DDNS 不是穿透。
- **依赖链**：域名 → DDNS → 公网入口 → 证书 → Nginx → 内网服务。实际外部可达性需外部探针证据。
- **迁移**：Lucky 与新平台不能同时写同一 DNS 记录或占用同一地址/端口。先临时端口验证，再交接监听、DDNS 和续期任务；保留可恢复的原配置。Lucky 其他模块不因这三项实现就自动视为等价替代。
- **权限**：DNS token、ACME 账户密钥、证书私钥只在受控服务端存储；按区域/记录最小授权。总控与反代进程独立，避免总控升级让所有家庭服务不可访问。

来源：[Lucky 功能边界](https://github.com/gdy666/lucky)、[Nginx 重载语义](https://nginx.org/en/docs/control.html)、[ACME 验证方式](https://letsencrypt.org/docs/challenge-types/)。

## 13. 范围扩展：远程回家与组网

用户明确新增 Tailscale、SS、WireGuard 及其他内网穿透回家工具。此处将 SS 理解为 Shadowsocks；如果指 SSH，则属于另一种端口隧道适配器，不混用配置。

### 13.1 按访问目的组织，而不是把软件开关堆一起

| 场景 | 纳管工具 | 配置与观测重点 |
|---|---|---|
| 私有组网 / 访问家庭网段 | Tailscale；可选 Headscale 控制端 | 身份注册、设备授权、子网路由宣告/批准、访问策略、DNS、直连/中继、密钥到期 |
| 自主管理 VPN | 原生 WireGuard | 接口、peer、公钥、endpoint、AllowedIPs、路由、防火墙、最近握手、收发量、客户端配置 |
| 通过家庭出口代理访问 | Shadowsocks | 服务端/客户端角色、监听、加密方法、密钥、TCP/UDP、访问范围、来源限制、客户端配置 |
| 无家庭公网入口，借 VPS 发布服务 | frp | 家中 frpc、VPS frps、认证、TLS、映射/访问者模式、监听端口、重连、外部可达性 |
| 其他组网/托管隧道 | ZeroTier、Cloudflare Tunnel；其他工具通过独立适配器纳管 | 先核实目标版本控制接口、账户授权、协议限制与服务条款，不承诺任意协议通用透传 |

Tailscale、WireGuard、Shadowsocks、frp 是明确纳入的配置面；其他工具列入适配路线，不用“通用脚本框”假装已经支持。Headscale 是可选的 Tailscale 客户端控制端方案，不是另一个数据转发协议；是否支持特定客户端能力需版本核验。

### 13.2 不能混淆的边界

- Tailscale **子网路由**用于访问家中网段；**出口节点**用于让远端设备经家庭出口上网。两者独立授权，默认不宣告 0.0.0.0/0 或 ::/0。
- 原生 WireGuard 不自带 Tailscale 式发现、协调与中继服务。需要可达的 peer 入口或明确的 VPS 中转拓扑；PersistentKeepalive 维持 NAT 映射，不等于解决任意 CGNAT。
- WireGuard 最近无握手可能是闲置，而非故障；展示“上次握手”和主动探测结果，不凭握手时间单独宣判离线。
- Shadowsocks 是 TCP/UDP 代理，不是完整三层 VPN，也不会自动穿透家中 NAT。家中服务端需要公网可达入口，或由单独隧道承载；配置出站 SS 节点不等于部署回家 SS 服务端。
- frp 的普通反向代理路径依赖可达的 frps；P2P 能否建立取决于网络条件，不承诺总能直连。
- “回家访问 NAS”与“把所有流量带回家，再走 Mihomo”是两个策略。后者显式选择，展示全链路与额外延迟，不默认套娃。
- Tailscale、WireGuard 与 Mihomo TUN 可能同时修改路由/DNS。须检查网段重叠、默认路由竞争、MTU、回程、DNS 环路及节点控制/中继流量是否被自身代理捕获。
- 用户在外地 LAN 与家庭 LAN 网段相同时，标记冲突并选择明确的重编号/映射方案；不静默安装一条可能导错流量的路由。

### 13.3 UI：新增“远程接入”工作区

页面包含：接入总览、私有组网、WireGuard、代理回家、穿透隧道、客户端与授权。工具配置使用各自专属表单；统一状态展示“配置期望 / 已应用 / 连通证据”，没有热加载能力的工具明确提示需要重启及连接影响。

创建向导先选择目的：

1. **只访问指定家庭服务**：选择 NAS/媒体服务等资源，默认最小权限。
2. **访问整个家庭网段**：选择子网路由或 WireGuard，并显式列出可访问网段。
3. **使用家庭网络出口**：选择出口节点/全隧道或 SS 代理，说明覆盖范围。
4. **给外部用户发布服务**：选择直接公网入口或 frp/托管隧道，再关联反代与证书。

接入详情展示：远端设备身份 → 直连/中继/隧道 → 家庭接入节点 → 授权规则 → 内网服务或家庭出口。关联 Nginx/DDNS/证书，但 VPN 不被强制要求经过 Nginx。

首页新增“远程接入”面板，显示已授权设备、活动 peer/会话、当前传输路径、探测延迟、吞吐、异常和凭据到期。无法采集的来源或指标标为未知，不生成假状态。

### 13.4 执行与安全设计

- Agent 在具备相应权限的执行节点管理 tailscaled、WireGuard、SS 和 frpc/frps；Mihomo、Nginx 与远程接入进程独立运行，不把所有服务放进一个必需同时重启的进程。
- 登录 Tailscale 等账户需要真实交互授权；批准路由、修改 ACL/grants、发布出口需对应账户权限，不伪造“配置保存即云端授权成功”。
- peer 使用独立密钥，可单独撤销；私钥优先客户端生成。配置文件、二维码、SS 分享链接都是敏感凭据，需权限校验、明确显示/下载动作、审计与日志脱敏。
- 默认仅允许访问指定网段/服务，不自动放行整个 LAN，不把总控、iKuai 管理页或 NAS 管理端口直接开放到公网。
- 同一网络资源一个权威写入者；纳管前导入现有运行配置并预览差异，避免覆盖用户手工 peer、路由或防火墙。
- 备用连接不自动等同于高可用。仅在用户配置了独立备用入口、访问规则及可达性证据后设计切换；不得因隧道失败自动公开原本私有服务。

### 13.5 实施与验收

工具按独立适配器逐个实现，完整验收后才显示“已支持”，不是同时启动所有工具。先接 Tailscale 子网回家与 WireGuard peer 管理，再完成 Shadowsocks 回家和 frp 发布，其他工具按实际部署纳管。

验收覆盖：蜂窝网络真实访问、仅授权资源可达、未授权访问被拒绝、直连/中继识别、家庭公网 IP 变化、密钥撤销、网段冲突、IPv6、DNS 与回程、Mihomo TUN 共存、进程/主机重启后持久化、配置拒绝时保留原运行状态。无外网设备或中转实例时，只能验收局部配置，不能声称已实现端到端穿透。

来源：[Tailscale 子网路由](https://tailscale.com/docs/features/subnet-routers)、[Tailscale 出口节点](https://tailscale.com/docs/features/exit-nodes)、[WireGuard 配置与 NAT 保活](https://www.wireguard.com/quickstart/)、[Shadowsocks 协议定位](https://shadowsocks.org/doc/what-is-shadowsocks.html)、[frp 架构与协议](https://github.com/fatedier/frp)。

第 12–13 节描述产品目标，不代表后端能力已经接入。新增工作区的交互原型已补齐，范围与验证证据见第 14 节。

## 14. 原型扩展与移动端适配

原型文件仍为 `home-network-control.html`，单文件、无外链依赖；刷新会清空演示变更。新增 10 个主视图，总计 22 个主视图，不修改生产前端或真实网络配置。

### 14.1 新增可交互工作区

- **服务发布**：服务总览、Nginx 反代、ACME 证书、DDNS、公网入口；可新增和编辑配置，查看已应用配置投影、链路诊断与迁移检查项。
- **远程回家**：接入总览、Tailscale、WireGuard、Shadowsocks、穿透与组网；后者分别提供 frp、ZeroTier、Cloudflare Tunnel 专属表单。Tailscale 可配置托管或自建 Headscale 控制端。
- **接入向导**：从访问家庭资源、家庭网段、家庭代理或发布服务进入相应配置；不默认授权全隧道。
- **发布闭环**：配置草稿与已应用快照分离，同一对象多次编辑合并为当前待发布项；演示失败保留旧应用快照和草稿，成功才提交新快照。
- **生命周期**：证书续期成功/失败预演、DDNS 候选地址更新、WireGuard 撤销、Tailscale 身份授权和直连/中继场景。客户端只展示参数，不生成假密钥、假二维码或不可用分享链接。

### 14.2 手机端交互

- 手机侧栏独立滚动、点击遮罩关闭，顶部导航与搜索保持可达。
- 卡片、配置表单、接入链路单列布局；统计区域保留双列；宽表格仅在组件内部横向滚动。
- 表单与详情使用全屏面板，标题/关闭入口及保存操作栏保持可达，预留底部安全区。
- 主要按钮和导航采用至少 44px 触控高度，输入框采用 16px 字号，避免小字号输入在 iOS 上触发自动放大。
- 明暗主题共用语义色；原有总览、规则、策略和发布流程保留。

### 14.3 实际浏览器验证

在 `root@10.0.0.49` 的 Chromium 中执行，本机未运行测试、脚本或构建：

1. **页面覆盖**：逐一点击全部 22 个主视图，在 390×844、430×844、1512×1080 下均未发现整页横向溢出。
2. **手机配置**：430×932 下编辑反代、证书、DDNS、公网入口、Tailscale、WireGuard、SS、frp、ZeroTier、Cloudflare Tunnel，并确认保存后的名称显示在对应工作区。
3. **手机发布**：新增服务后，已应用 Nginx 投影不包含草稿；失败发布保留 r24 与草稿，再次成功发布后新服务出现在 r25 投影中。
4. **生命周期证据**：证书挑战失败保持旧证书；成功续期发布后显示 90 天及 r25；DDNS 发布前记录仍为 `192.0.2.10`，发布后为 `192.0.2.20`；Peer 撤销前后客户端预览分别为“已授权”和“已撤销”。新建未发布 Peer 显示“尚未应用”，不冒充已授权。
5. **配置边界**：拦截 HTTP-01 通配符、AAAA 与 IPv4 来源不匹配、未明确授权的默认路由；采集离线时阻止运行诊断。
6. **触控与视觉**：390px 下服务新增表单无横向溢出，保存按钮高度 44px；搜索可进入新增工作区，点击遮罩可关闭菜单；检查桌面暗色、手机暗色及 430px 手机浅色实际截图。
7. 浏览器页面错误记录为空；原型资源请求中没有外部 HTTP 请求。验证浏览器进程已退出。

截图：

- `home-network-services-desktop.png`
- `home-network-access-desktop.png`
- `home-network-services-mobile.png`
- `home-network-service-form-mobile.png`
- `home-network-access-mobile-light.png`

**验证边界**：以上是 Chromium 手机视口模拟，不是 iPhone/Android 真机或 Safari 验证，软键盘弹出和真机安全区仍需实际设备确认。未接入 Nginx、DNS 服务商、ACME CA、VPN 控制端或任何真实执行器；不作为公网可达、真实证书签发、云端授权或隧道穿透的证据。
