# 部署指南

事务发布支持 Mihomo、MosDNS，以及通过 Shell 安装或 Docker 运行的 Go Agent。ConfigFlow 与 Agent 都应更新到支持发布协议 1 的版本；旧版 `agent.sh` 不支持该协议，界面会要求升级，发布不会退回逐文件覆盖方式。

## 完整发布流程

```text
ConfigFlow：固定配置快照 → 验证与准备完整资源 → tar.gz 打包 → 发送
Go Agent：接收 → 独立暂存区解包 → 文件清单校验 → 配置校验 → 准备就绪
激活：磁盘检查 → 停服务 → 完整备份 → 替换 → 启动 → 健康检查 → succeeded
失败：恢复原文件、权限和属主 → 恢复原运行状态 → 检查回滚结果
```

ConfigFlow 准备主配置、规则、Provider 和受管自定义文件。必需下载失败会停止准备；文本与二进制资源都按原始字节处理，合法空规则可以存在。发布包清单包含发布 ID、目标 Agent、服务类型、路径、大小和 SHA-256；Agent 同时检查压缩包摘要、文件摘要及路径边界。

预检查在旧服务运行时完成。Mihomo 使用目标机器实际安装的同一个二进制执行 `-t`；MosDNS 检查 YAML 和受管文件引用，再由启动后的 DNS 检查验证运行结果。备份在停服务后执行，避免运行中的 Provider 更新造成备份不一致。备份失败不会继续替换。

启动后的检查既看服务管理器状态，也检查实际功能。Mihomo 会检查可用的控制接口、声明的 Provider，以及配置启用的代理监听端口；MosDNS 执行 DNS 查询。默认健康检查窗口为 30 秒，需要连续通过检查才确认成功。

只有 `succeeded` 表示本次文件已安装且服务检查通过，ConfigFlow 才据此确认本次发布的配置版本。`receiving`、`verifying`、`ready` 和 HTTP 202 都只是中间状态。连接超时后按同一发布 ID 查询结果；不要把未收到响应当成已回滚。

## 前端暂存、应用与结果

Agent 管理中的“推送配置”默认立即激活，并显示准备、校验、备份、替换、启动检查及回滚阶段。

通过后端发布接口 `POST /api/agents/<agent_id>/push-config` 传入 `restart: false` 时，只上传并校验文件，停在 `ready`，正式文件和当前服务保持原状。前端显示“已校验，待激活”和“应用并启动”；点击后重新核对暂存文件，再进入停服、备份、替换流程。暂存不是一次成功发布。

| 状态 | 含义与处理 |
| --- | --- |
| `succeeded` | 发布成功，启动检查通过 |
| `failed` | 本次发布失败；查看失败阶段和原因 |
| `rolled_back` | 本次发布失败，已恢复发布前的文件及相应运行状态 |
| `recovery_pending` | 开机阶段已恢复旧文件，等待正常 Agent 完成服务检查 |
| `rollback_failed` | 回滚尚未完成，需要处理；界面提供“查询恢复状态” |
| 结果待确认 | 通信暂时失败，前端继续查询同一发布 ID |

同一服务的发布、服务重启、Agent 更新和卸载会互斥。存在未解决的恢复事务时，这些变更操作被阻止。

## Shell 安装与升级

在 ConfigFlow 的 Agent 管理生成 Go Agent 安装命令（`agent_type=go`），按提示选择安装或升级。脚本支持 Linux amd64、arm64、armv7，自动识别 systemd 或 OpenRC。Mihomo/MosDNS 核心及对应系统服务需提前安装。

Shell 命令安装的仍是 Go 单二进制。发布运行时不需要安装 Python、tar/gzip、Redis 或数据库；安装阶段下载二进制需要 wget 或 curl。二进制暂存在 `/usr/local/bin` 内的唯一临时文件，适用于 `/tmp` 挂载为 `noexec` 的主机，并通过同一文件系统内的重命名替换。

重新安装时，相同 Agent 名称和服务类型会继承原注册 ID 与 Token，也会从旧版 `/opt/configflow-agent/config.json` 迁移身份。已有未完成事务会先在旧配置目录完成恢复，再更新安装配置。自定义服务生命周期需明确提供停止、启动和状态命令，不能只提供任意重启命令。

安装器生成启动恢复门禁 `configflow-recover-mihomo` 或 `configflow-recover-mosdns`：

- systemd 使用一次性服务及 `RemainAfterExit=yes`；核心的 drop-in 通过 `Requires`、`After` 依赖门禁。事务内的普通核心重启不会重复执行开机恢复。
- OpenRC 使用恢复服务，并在核心 `/etc/conf.d/<service>` 的标记区域追加 `rc_need`，保留已有其他设置。
- 卸载先清理恢复依赖，再删除 Agent 二进制，保留核心配置与发布备份。

原生安装默认配置目录是 `/opt/configflow-agent`，默认 Agent API 端口为 8080，心跳间隔 30 秒。安装时可指定端口和核心配置路径，服务生命周期及健康检查字段见 [配置说明](CONFIG.md)。

## Docker 部署与升级

使用项目的 [AIO Compose 示例](../../../../docker/docker-compose-aio.yml)，或 Agent 管理生成的 Compose 配置；也可参考 [Mihomo 单服务示例](../../../../docker/docker-compose-mihomo.yaml) 和 [MosDNS 单服务示例](../../../../docker/docker-compose-mosdns.yaml)。需要已安装 Docker，使用 Compose 文件时还需 Docker Compose。

Docker 镜像包含 Go Agent、两个核心、Supervisor 和其现有 Python 运行环境，不需要为事务发布额外安装软件包。入口会在创建默认配置、启动 Supervisor 之前执行恢复；恢复失败会阻止容器继续启动核心。

保留完整服务目录和 Agent 身份目录：

```yaml
volumes:
  - ./agent_data/agent:/opt/configflow-agent
  - ./agent_data/mihomo:/etc/mihomo
  - ./agent_data/mosdns:/etc/mosdns
```

单服务部署只需挂载对应服务目录和 Agent 目录。服务目录包含 `.configflow-deployments/`，必须与配置一起持久化；只挂载 `config.yaml` 不满足发布和恢复要求。

| 设置 | 默认或示例行为 |
| --- | --- |
| `SERVICE_TYPE=mihomo` 或 `mosdns` | 单服务模式，Agent API 默认 8080 |
| `ENABLE_MIHOMO` / `ENABLE_MOSDNS` | AIO 模式下显式设为 `true` 才启用对应服务 |
| `AGENT_MIHOMO_PORT` / `AGENT_MOSDNS_PORT` | AIO 默认 8080 / 8081 |
| `SERVER_URL` | 必填 ConfigFlow 服务地址 |
| `AGENT_IP` | 可选，填写可被 ConfigFlow 访问的 Agent 地址 |
| `HEARTBEAT_INTERVAL` | Dockerfile 默认 30 秒；部分 Compose 示例显式设为 60 秒 |
| `DEPLOYMENT_HEALTH_TIMEOUT` | 默认 30 秒 |
| `MIHOMO_HEALTH_*` / `MOSDNS_HEALTH_*` | 覆盖 HTTP/DNS 探测目标，字段见配置说明 |

AIO 同时启用两个服务时，新建的默认配置让 MosDNS 使用 53、Mihomo DNS 使用 1053，避免首次启动冲突。代理端口、控制接口和 DNS 端口应按配置映射；9090 是 Mihomo 控制接口端口。

在 Compose 文件所在目录启动和查看日志：

```sh
docker compose up -d
docker compose logs --tail=100
```

升级时更新 Agent 镜像并重新创建容器，保留上述数据卷及启动环境。源代码自建镜像使用仓库内 `docker/Dockerfile.agent`；无需在运行容器内安装 Go 编译器。

## 磁盘空间与备份保留

每个服务的 `.configflow-deployments/<发布ID>/` 可包含发布包、暂存文件、有效清单、`state.json` 和 `backup/`。事务记录在替换前落盘，备份保留原有文件内容、传统 Unix 权限位（mode）及属主、属组（UID/GID）；回滚也会删除本次新增的受管文件。

当前文件替换与恢复仅支持上述传统权限元数据，不保留额外 ACL、SELinux 标签等扩展权限属性。依赖这些属性才能访问配置或规则的安装方式尚未验证，也不属于当前事务发布的支持范围；必须先适配并验证扩展属性的复制与恢复，再启用事务发布。

清理策略是保留当前处理的事务、当前生效清单指向的事务，以及另外最近两条已完成事务（`succeeded`、`failed`、`rolled_back`）。较旧的这些已完成事务可被清理，其发布包、暂存文件和备份随目录一起删除。`ready`、`rollback_failed` 等未解决事务不会被自动清理，因此这不是固定最多两份备份，也不是按天数保留。

磁盘空间需容纳压缩包、解包内容、旧文件备份以及替换时的临时副本，不能按固定 100 MB 估算。Agent 会在停服前检查备份与替换所需空间；运行期间空间耗尽仍会触发失败处理。需要长期留存的已完成备份应另行保存。

## 失败诊断与恢复

先在 Agent 卡片查看失败阶段、发布错误和回滚错误。需要更多信息时，读取该发布的 `state.json`，并查看 Agent 及核心日志。状态查询接口保留现有鉴权，不要把 Token 贴到日志或共享诊断记录中。

- systemd Agent 日志：`journalctl -u configflow-agent --no-pager -n 100`。
- OpenRC Agent 日志：`/var/log/configflow-agent.log`。
- Docker 日志：`docker compose logs --tail=100`；同时检查容器内 Supervisor 核心日志。
- 发布状态：前端查询，或通过 ConfigFlow 的 `GET /api/agents/<agent_id>/deployments/<发布ID>` 获取。

普通 Agent 启动发现恢复失败时，会保留可用的状态、日志查询 API，并阻止后续变更；这不代表核心已经恢复。启动门禁使用的 `-recover-only` 则在失败时返回非零退出码，使核心停在恢复之前。文件已恢复但服务尚未确认时显示 `recovery_pending`，验证通过后才显示 `rolled_back`。

出现 `rollback_failed` 时：

1. 保留发布目录、备份和日志，按 `rollback_error` 检查磁盘空间、目录权限、文件属主、核心二进制、服务管理器和探测目标。
2. 修复具体原因。若备份缺失或校验值不符，重复重启不能重建备份；应先从独立可信备份修复文件，保留失败现场供核对。
3. 原生安装先停止 Agent，避免与服务操作并发，再执行已实现的恢复命令：

   ```sh
   sudo systemctl stop configflow-agent
   sudo /usr/local/bin/configflow-agent -config /opt/configflow-agent/config-mihomo.json -recover-and-exit
   ```

   OpenRC 用 `sudo rc-service configflow-agent stop` 停止 Agent；MosDNS 使用 `config-mosdns.json`。只有恢复命令成功后，再使用对应服务管理器启动 Agent。
4. Docker 在宿主机修复持久化目录或部署设置后重新启动容器，由入口重新执行恢复。若入口仍失败，继续检查其日志和保留的事务文件。
5. 在前端点击“查询恢复状态”，确认恢复结果和核心实际可用性后再发布。

`-recover-and-exit` 会按事务记录停止服务、恢复文件，并在原服务需要运行时启动及检查它。`-recover-only` 只用于核心尚未启动的门禁阶段，不应对运行中的核心手工执行。不要手工把失败状态改成 `succeeded` 或删除未解决的事务目录来绕过恢复。
