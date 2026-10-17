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
- ConfigFlow 是配置源。正式配置必须使用 Agent 的 `push-config` 事务发布，不能用 SFTP 直接覆盖受管 YAML；SFTP 只用于镜像 TAR、备份和只读哈希回读。

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
/export hide-sensitive file=cf-prechange
/system backup save dont-encrypt=no name=cf-prechange
/container print detail without-paging
/interface veth print detail
/ip address print detail
/ip route print detail without-paging
/routing rule print detail without-paging
/ip firewall nat print detail without-paging
/ip firewall filter print detail without-paging
```

同时保存 OxiDNS/Mihomo 容器的 `root-dir`、`interface`、`envlists`、`mountlists`、`start-on-boot` 和镜像摘要。不要把 `env`、Agent token、ConfigFlow 密码或 API 密码粘贴到工单或聊天中。

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
6. 读取 `active-manifest.json`，核对 deployment ID、每个受管文件 SHA-256、OxiDNS native check、Supervisor 状态和运行流量。

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

本项目一次已验证的维护镜像为 Agent `1.7.1-go`；实际部署仍以运行时 image-id、二进制 SHA-256、ConfigFlow deployment `succeeded`、DNS/代理结果和 heartbeat 回读为准。未执行的 RouterOS 回流或防火墙修改不应在文档中标记为已验证。
