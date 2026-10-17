# RouterOS 容器：OxiDNS + Mihomo 部署与回流说明

本文是 `OXIDNS_MIHOMO_HOME_GUIDE.md` 的 RouterOS 容器版，面向 **RouterOS Container + ConfigFlow Agent**。文中地址、端口、容器名和镜像摘要均为示例或占位符；上线前必须替换为本机实测值。原文（iKuai 虚拟机版）：

<https://github.com/thsrite/configflow/blob/main/docs/OXIDNS_MIHOMO_HOME_GUIDE.md>

## 1. 架构与边界

```text
LAN 客户端 ── DNS ──▶ RouterOS dst-nat / veth ──▶ OxiDNS:53
                                                   ├─ 直连规则 ──▶ DoH:443
                                                   └─ 代理规则 ──▶ Mihomo DNS:1053

LAN 客户端 ──访问 Mihomo fake-ip 网段──▶ RouterOS 静态路由/策略路由
                                      ──▶ Mihomo 容器 ──▶ 出站
                                      ◀──── 返回路径回到 RouterOS ────┘
```

RouterOS 与 iKuai 不同：

- RouterOS Container 使用 `veth`、`root-dir`、`envlists` 和 `mountlists`；容器重建会更换临时 veth 路由，不能把 iKuai 的虚拟机网卡步骤直接套用。
- RouterOS 的本地 `file=<name>.tar remote-image=""` 是已导入的 TAR 快照，不是在线拉取。必须记录源 Docker 镜像摘要和 TAR SHA-256。
- RouterOS 的 NAT、地址列表、策略路由和 fake-ip 路由属于路由器数据面；没有明确授权时只读检查，不为了部署 OxiDNS 任意增删防火墙或路由。
- ConfigFlow 是配置源。正式配置必须使用 Agent 的 `push-config` 事务发布，不能把 SFTP 覆盖 YAML 当成正式发布。首次启用新 DNS 核心时，可将同一 Profile 的受管生成器产出的完整、原生校验通过的 bundle 作为启动引导，先备份并逐文件读回哈希；引导后仍必须完成下文 `ready → 原生校验 → activate → succeeded` 正式事务。不要手工改写引导配置、使用其他实例配置或把引导成功标为迁移完成。

OxiDNS 的代理规则应与 Mihomo 的代理策略保持一致。代理域名必须交给 Mihomo 返回 fake-ip；否则得到真实 IP 后会绕过 Mihomo。代理路径应 fail-closed，不要把公共 DNS 作为代理失败时的备用路径。

## 2. 地址规划

请使用独立且不重叠的 fake-ip 网段，并为每个 Mihomo 实例配置对应 RouterOS 路由：

| 角色 | 示例 | 说明 |
|---|---|---|
| RouterOS LAN | `<ROUTEROS_LAN_IP>` | 默认网关 |
| ConfigFlow | `<CONFIGFLOW_IP>:<PORT>` | 管理端 |
| Mihomo veth | `<MIHOMO_IP>` | 监听 DNS `:1053`、代理端口 |
| OxiDNS veth | `<OXIDNS_IP>` | 监听 DNS `:53` |
| fake-ip 网段 | `<FAKE_IP_CIDR>` | 每个 Mihomo 实例独立 |

上线前读取并保存：

```routeros
/export file=cf-prechange
/system backup save dont-encrypt=no name=cf-prechange
:foreach i in=[/container/find where name="<TARGET_CONTAINER>"] do={:put ("id=".$i." root=".[/container/get $i root-dir]." interface=".[/container/get $i interface]." mounts=".[/container/get $i mountlists]." envlists=".[/container/get $i envlists])}
/interface veth print detail
/ip address print detail
/ip route print detail without-paging
/routing rule print detail without-paging
/ip firewall nat print detail without-paging
/ip firewall filter print detail without-paging
```

同时保存 OxiDNS/Mihomo 容器的 `root-dir`、`interface`、`envlists`、`mountlists`、`start-on-boot` 和镜像摘要。新 RouterOS 默认隐藏导出中的敏感字段，但普通容器输出仍可能暴露内联环境变量；上面的白名单读取替代完整 `/container print`。二进制备份可能包含凭据，使用设备支持的加密备份方式，下载后限制权限。不要把 `env`、Agent token、ConfigFlow 密码或 API 密码粘贴到工单或聊天中。

## 3. 导出并导入 Agent 镜像

在已验证的 Docker 主机上固定镜像摘要，不要把 `latest` 标签当作版本证明：

```sh
docker pull thsrite/config-flow-agent:latest
docker image inspect thsrite/config-flow-agent:latest \
  --format '{{json .RepoDigests}} {{.Id}}'
docker save thsrite/config-flow-agent@sha256:<AGENT_IMAGE_DIGEST> \
  -o thsrite-agent-<VERSION>.tar
sha256sum thsrite-agent-<VERSION>.tar
```

使用既有受控 SFTP 流程传到 RouterOS：

```sh
sftp routeros
put thsrite-agent-<VERSION>.tar <ROUTEROS_STORAGE>/thsrite-agent-<VERSION>.tar
```

RouterOS 上核对文件大小和摘要（摘要可在管理主机上对传输前后文件核对）：

```routeros
/file print detail where name~"thsrite-agent"
```

推荐的候选镜像至少独立核对四项：配置镜像名、Docker/RouterOS image-id、Agent `/usr/local/bin/configflow-agent` 版本、Agent 二进制 SHA-256。实测维护镜像 `1.7.1-go` 同时包含 Mihomo、MosDNS、OxiDNS 和事务发布生命周期字段；仍应按下文先停用一个实例验证，不要批量重启。

## 4. RouterOS AIO 容器

AIO 中 Mihomo 与 OxiDNS 不能同时占用默认 DNS `:53`。只启用一个 DNS 核心：

```yaml
services:
  configflow-agent-aio:
    image: thsrite/config-flow-agent@sha256:<AGENT_IMAGE_DIGEST>
    environment:
      SERVER_URL: https://<CONFIGFLOW_HOST>/
      AGENT_IP: <VETH_IP>
      HEARTBEAT_INTERVAL: "30"
      ENABLE_MIHOMO: "true"
      AGENT_MIHOMO_NAME: <MIHOMO_AGENT_NAME>
      AGENT_MIHOMO_PORT: "<MIHOMO_AGENT_PORT>"
      ENABLE_MOSDNS: "false"
      ENABLE_OXIDNS: "true"
      AGENT_OXIDNS_NAME: <OXIDNS_AGENT_NAME>
      AGENT_OXIDNS_PORT: "<OXIDNS_AGENT_PORT>"
    volumes:
      - ./config/mihomo:/etc/mihomo
      - ./config/oxidns:/etc/oxidns
      - ./state:/opt/configflow-agent
```

先复制并校验 `/opt/configflow-agent/config-mosdns.json`，再按 Agent 文档生成保留 `agent_id` 和 `token` 的 `config-oxidns.json`。服务类型、服务单元、二进制和配置路径必须同步改为 OxiDNS；不要让入口脚本在重建时注册一个重复 Agent。重建后应看到：

```text
agent-mihomo RUNNING
agent-oxidns RUNNING
mihomo       RUNNING
oxidns       RUNNING
agent-mosdns STOPPED
mosdns       STOPPED
```

此时 ConfigFlow 中应通过 Agent API 将原 MosDNS Agent 的 `service_type` 改为 `oxidns`，保留原 Agent ID、Profile、端口、token 和名称语义，再读回确认。新 Agent 注册成功但 ID 改变，不算身份保持。

## 5. RouterOS 单实例容器迁移

**镜像升级不等于 MosDNS → OxiDNS 迁移。** 即使 Agent 显示新版本、在线且 MosDNS 正常运行，只要注册记录仍为 `service_type=mosdns`，就没有完成 OxiDNS 替换。已升级到包含 OxiDNS 的维护镜像时，不必再次导入镜像；需要的是保留身份的核心切换：

1. 逐实例备份 Profile、当前有效 MosDNS 受管 bundle、Mihomo 配置和运行选择、完整身份目录、Supervisor、RouterOS 导出及二进制备份。不要拿镜像升级前已经失效的旧磁盘 YAML 当回滚来源。
2. 读取真实 `/entrypoint.sh`：AIO 的 OxiDNS 路径为 `/etc/oxidns/config.yaml`；需要 `ENABLE_MOSDNS=false`、`ENABLE_OXIDNS=true` 和原 DNS Agent 的端口，不能凭默认 `8082` 改变已有端口。默认生成 JSON 只保留当前服务文件中的 ID/token，所以在首次启用前必须准备好 `config-oxidns.json`。
3. 从当前 Profile 的 MosDNS 设置显式迁移规则选择、直连/代理/备用上游、hosts 和适用的缓存字段到 OxiDNS 设置，保存后 GET 精确读回。不要直接复制 MosDNS YAML 或其 API 配置；OxiDNS 模板有独立的 schema、插件和缓存控制流。备机、主机保持各自 Mihomo `:1053` 和独有 fake-ip CIDR。
4. 确认 OxiDNS store 的实际 mount。推荐独立目录；若沿用旧 resolver store 并新增 `/etc/oxidns` 挂载，目录名即使仍含 `mosdns`，运行核心也由命令、Supervisor 和 Agent 类型决定。旧 MosDNS 只能保持 STOPPED；回滚时必须先停止 OxiDNS，再恢复**本实例此次迁移前**核验过的 MosDNS 主配置及规则，不能让两个核心同时写同一 store，也不能仅改启用开关。
5. 对完整引导 bundle 做 `oxidns check -c <config> -d <bundle-dir>`，保持原身份、端口和 Profile 后切换原容器。完整重启会短暂停止同容器的 Mihomo，因此要先完成备机所有应用验收，再动主机。随后立即完成第 6 节正式事务，不能把引导当最终发布。
6. 容器重启后逐项读取 `config-oxidns.json` 的非敏感生命周期字段，确认 `service_manager=supervisor`、`service_unit=oxidns`、`service_binary=/usr/local/bin/oxidns`，ID/token 私下比较一致；重新验证 Mihomo 静态文件哈希和各个原有 Selector 的 `now`。必要时仅恢复仍存在的原选择并读回，不改候选池或策略。

下面仅适用于**确需更换镜像容器**的步骤：

先升级备机，再升级 NAS，最后升级主机；每次只操作一个实例。新容器必须使用新的 `root-dir`，保留实例自己的 veth、envlist、Mihomo/MosDNS（或 OxiDNS）配置 mount 和身份 mount：

```routeros
/container add file=<ROUTEROS_STORAGE>/thsrite-agent-<VERSION>.tar \
  name=<NEW_CONTAINER> root-dir=<NEW_ROOT_DIR> \
  interface=<OLD_INTERFACE> envlists=<OLD_ENVLIST> \
  mountlists=<OLD_SERVICE_MOUNTS>,<OLD_IDENTITY_MOUNT> \
  hostname=<NEW_CONTAINER> start-on-boot=no logging=no
```

等待抽取完成后，SFTP 只读回 `/usr/local/bin/configflow-agent` 和 `/entrypoint.sh`，核对候选二进制 SHA-256 与 Docker 源文件一致。确认新容器停止、旧容器仍运行且旧容器 `start-on-boot` 未被改变后，才进入切换：

```routeros
/container set <OLD_ID> start-on-boot=no
/container set <NEW_ID> start-on-boot=yes
/container stop <OLD_ID>
/container start <NEW_ID>
```

如果 RouterOS 拒绝修改旧容器的 `start-on-boot` 或要求改变 `root-dir`，停止并保留旧容器；不要为了清理条目直接删除容器。旧 TAR、旧 `root-dir`、旧身份目录、RouterOS `.rsc` 导出和二进制备份都应保留到验收完成。

## 6. ConfigFlow OxiDNS 配置与事务发布

在 ConfigFlow 中把目标 Agent 的服务类型设为 OxiDNS，检查 Profile 绑定和 OxiDNS 配置。直连上游优先使用 DoH `:443`，避免 RouterOS 劫持所有 LAN UDP/53 时形成回环；代理上游指向同一 Profile 的 Mihomo DNS `:1053`，备用路径也必须 fail-closed。

正式发布必须按以下顺序执行：

1. `POST /api/agents/<id>/push-config`，请求体 `{"restart": false}`；
2. 轮询 `GET /api/agents/<id>/deployments/<deployment_id>`，必须到 `ready`；
3. 对同一 deployment 做原生 OxiDNS 校验，确认所有引用的规则文件已打包且非空；
4. `POST /api/agents/<id>/deployments/<deployment_id>/activate`；
5. 继续轮询直到 `succeeded`；
6. 读取配置目录下 `.configflow-deployments/active-manifest.json`，核对 deployment ID、每个受管文件 SHA-256、OxiDNS native check、Supervisor 状态和运行流量。`ready` 阶段先冻结保存 `effective-manifest.json` 与完整 `stage/`；激活可能移动或清理原 stage，所以不能等发布后再取候选文件。原生检查使用独立副本、相同镜像摘要和隔离网络，不写入生产 stage。

不要在 `ready` 后重新生成配置，也不要用另一份 SFTP 文件替换 staged bundle。`202 Accepted` 只表示受理，不表示已经生效。

OxiDNS 配置至少应有：

- UDP 和 TCP `:53` 两个监听；
- 直连规则与代理规则明确分组；
- 代理上游指向 Mihomo `:1053`；
- fake-ip 运行状态目录与受管配置分离；
- API 若用于监控，绑定受限地址并启用认证；
- 规则文件全部随事务 bundle 传输，不能只推送主 YAML。

## 7. Mihomo fake-ip 与 RouterOS 路由

Mihomo 的 `fake-ip-range` 必须与其他实例不同，并在 RouterOS 中明确指向对应 Mihomo veth：

```routeros
/ip route add dst-address=<FAKE_IP_CIDR> gateway=<MIHOMO_IP> \
  routing-table=main comment="ConfigFlow Mihomo fake-ip"
```

该命令只是示例；先读取现有同目标路由、表、距离和检查网关，避免重复或覆盖主/备实例。fake-ip 之外的真实 IP 回流还涉及连接跟踪、SNAT 和策略路由，不能把 iKuai 的 nftables 回流片段直接复制到 RouterOS。RouterOS 版应先只读确认：

```routeros
/ip firewall nat print detail
/ip firewall mangle print detail
/ip route print detail where dst-address~"<FAKE_IP_PREFIX>"
/routing rule print detail
```

除非另行授权，不修改 NAT、mangle、filter、策略路由、VLAN 或管理访问。若确需回流，先导出配置，明确连接标记、路由表、返回下一跳和回滚命令，再分 TCP/UDP 进行隔离验收。

## 8. 验收矩阵

每个实例都要独立记录，不能用 heartbeat 代替数据面验收：

```sh
# DNS UDP/TCP
for q in www.baidu.com www.google.com; do
  dig +time=3 +tries=1 @<DNS_IP> "$q" A +short
  dig +tcp +time=3 +tries=1 @<DNS_IP> "$q" A +short
done

# Mihomo HTTP/SOCKS（按实际监听端口选择；不要假设 HTTP 端口存在）
curl --proxy http://<MIHOMO_IP>:<MIXED_PORT> \
  --max-time 15 -o /dev/null -w '%{http_code} %{exitcode}\n' \
  https://www.gstatic.com/generate_204
curl --proxy socks5h://<MIHOMO_IP>:<MIXED_PORT> \
  --max-time 15 -o /dev/null -w '%{http_code} %{exitcode}\n' \
  https://www.gstatic.com/generate_204
```

还要读取 ConfigFlow Agent 列表，确认每个目标 Agent：

- 版本为候选版本；
- `status=online`、`service_status=active`；
- 至少跨一个完整 heartbeat interval 的时间戳持续前进；
- Agent ID、token、Profile 绑定未改变；
- Mihomo、MosDNS/OxiDNS 与 Agent 进程分别为 RUNNING；
- fake-ip 返回地址属于该实例专属网段；
- RouterOS 路由和防火墙策略与迁移前一致（除已明确授权的条目）；
- 目标 DNS 的 UDP、TCP、HTTP/SOCKS 结果均单独记录。

最后从 LAN 客户端或等价路径验证 RouterOS 的实际 DNS 转发和 AdGuard hop。直接访问容器 IP 只能证明容器监听，不能证明 NAT、策略路由或 AdGuard 链路正常。

## 9. 回滚

失败优先恢复数据面，再查原因：

1. 读取最后阶段日志、容器运行/开机标志和 RouterOS 当前 image-id；
2. 停止新容器，恢复旧容器 `start-on-boot=yes`，启动旧容器；
3. 恢复前必须确认旧配置、规则目录、身份目录和 cache 是同一份已验证 bundle，不要使用无关历史配置；
4. 若是 ConfigFlow 事务发布失败，使用当前部署记录的 rollback/recover 接口，不要把旧 YAML 直接 SFTP 覆盖；
5. 重新验证 DNS UDP/TCP、Mihomo HTTP/SOCKS、ConfigFlow heartbeat 和 RouterOS 路由；
6. 保留失败 deployment、旧镜像 TAR、备份和哈希报告，不要删除容器条目或 root-dir。

镜像回滚本身不是成功证明。必须重新完成相同的应用层 DNS、代理和管理面验收。若旧容器恢复后仍不健康，停止序列升级并报告未恢复，不要继续切换其他实例。

## 10. 实测记录格式

建议为每次迁移保存一份权限为 `0700` 的私有报告，包含：时间、ConfigFlow/Agent 版本与摘要、各实例容器 ID、Agent ID（token 不写入报告）、配置和 manifest 哈希、RouterOS 导出哈希、deployment 状态、DNS UDP/TCP、HTTP/SOCKS、AdGuard hop、heartbeat 样本和回滚结果。报告中不保存密码、订阅 URL、JWT、Agent token 或完整环境变量。

## 11. 主备 OxiDNS 正式迁移验收示例

以下是主备均完成核心切换后的验证边界，而不是仅镜像升级的记录：

| 检查项 | 备机 | 主机 |
|---|---|---|
| 原身份与原 Profile | 保留，无新增注册记录 | 保留，无新增注册记录 |
| 注册 `service_type` | `oxidns` | `oxidns` |
| 核心与 Agent | Mihomo/OxiDNS 及各自 Agent RUNNING | Mihomo/OxiDNS 及各自 Agent RUNNING |
| 旧核心 | MosDNS/Agent STOPPED | MosDNS/Agent STOPPED |
| 正式受管发布 | 同一 deployment `ready → native check → activate → succeeded` | 同一 deployment `ready → native check → activate → succeeded` |
| 交付文件 | 主配置 + 10 个规则文件，逐文件 SHA-256 一致 | 主配置 + 10 个规则文件，逐文件 SHA-256 一致 |
| UDP/TCP DNS | 国内真实 IP、代理域名为备机所属 fake-ip | 国内真实 IP、代理域名为主机所属 fake-ip |
| HTTP/SOCKS | 三个 HTTPS 目标均通过 | 三个 HTTPS 目标均通过 |
| 保持项 | Mihomo 静态文件、Selector、fake-ip CIDR、路由/NAT/filter/mangle 不变 | 同左 |

独立验收还须再次读取 live registry、active manifest、身份文件、当前 Supervisor 和实际流量，并跨完整 heartbeat interval 比较时间戳；不要只读部署脚本刚生成的“成功”文件。额外的 LAN DNS/AdGuard 入口结果应单列：AdGuard 返回主机 fake-ip 是主机所有权的证据，不能要求其属于备机 CIDR；RouterOS 网关递归返回真实 IP 也不能当作 OxiDNS fake-ip 链路的通过证据。上游恢复后的成功不证明历史超时已找到根因。此示例未声称做过故障注入、自动故障切换或 RouterOS 回流规则变更。

本项目一次已验证的维护镜像为 Agent `1.7.1-go`；实际部署仍以运行时 image-id、二进制 SHA-256、ConfigFlow deployment `succeeded`、DNS/代理结果和 heartbeat 回读为准。未执行的 RouterOS 回流或防火墙修改不应在文档中标记为已验证。
