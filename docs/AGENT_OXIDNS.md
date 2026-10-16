# OxiDNS Agent 部署

OxiDNS 使用 Go Agent 的 deployment protocol 1，与 MosDNS 保留为两个独立服务类型。旧 Shell Agent 不支持 OxiDNS 事务发布。

## 发布与回滚

1. 服务端从 Agent 绑定的配置快照生成配置，获取全部规则并计算文件摘要。
2. Agent 解包至隔离暂存目录，检查清单、路径和文件摘要。
3. 使用已安装核心执行 `oxidns check -c <暂存配置> -d <暂存目录>`。缺少功能的 minimal/standard 核心会在这里拒绝不兼容配置；特色功能建议 full 版本。
4. 校验成功后停止服务、备份受管理文件、替换配置并启动服务。
5. 检查服务状态、实际 DNS A 查询；启用明文 HTTP API 时同时检查 `/api/readyz`，从配置中读取 Basic 认证。健康失败自动恢复旧配置及旧规则，再检查旧服务。

API 启用 TLS 时默认使用服务状态和实际 DNS 查询作为健康条件，不声称已验证 HTTPS 管理接口，也不跳过证书校验。需要额外验证 HTTPS 时，可设置证书名称与系统信任链均有效的 `health_url`；自签名证书需在主机安装可信 CA。mTLS 健康接口须另行提供可用检查入口。

管理 API 的用户名和密码不会被发送给不匹配的自定义健康 URL，原生校验错误也不回显包含凭据的核心输出。

动态规则和查询记录等文件使用 `state/`。该目录不进入下载包、发布清单或配置回滚；已有运行数据会保留。缓存 dump 也不作为规则文件发布。恢复配置不能撤销 RouterOS 上已经发生的外部变化，或已记录的查询历史。

### 手动回滚已成功发布的配置

Agent 页面“发布记录”中的“回滚到发布前”用于恢复最近成功发布前的备份。服务端入口为 `POST /api/agents/{agent_id}/deployments/{deployment_id}/rollback`，通过原部署状态接口轮询结果。已受理任务保留相同部署 ID，带 `manual_rollback: true`，状态从 `rolling_back` 进入 `rolled_back`；此时 `success: true`。自动失败回滚仍用 `success: false` 区分。

只允许当前配置方案下最新且仍在运行清单中的成功部署。Agent 在停止服务前检查操作锁、备份摘要、实际配置路径及当前文件摘要。较旧发布、缺失备份、正在升级或被外部任务改写的配置/规则会拒绝回滚并说明原因。若每日规则更新任务已改写规则，需先处理该差异；不能用旧发布记录覆盖未知更改。

回滚恢复受管理文件及发布前的服务启动状态，并对原本运行的服务重新执行健康检查。备份中的旧服务无法恢复健康时状态为 `rollback_failed`，保留恢复记录并阻止新的发布；修复原因后 Agent 启动恢复流程会重试。成功回滚会清除 ConfigFlow 的“当前方案已同步”标记，因为恢复的文件可能来自接入 ConfigFlow 之前。

完整发布不接受外部 `include` 配置。请把插件写入主配置；`files`、`file`、`persistent.files` 引用的输入文件必须在包中。TLS 证书和 WebUI 静态资源由主机或镜像单独提供。

## 本机安装

Agent 安装页面选择 OxiDNS，默认核心配置路径 `/etc/oxidns/config.yaml`、服务名 `oxidns`、二进制 `oxidns`。使用官方便携安装布局时，请把配置路径改为实际路径，例如 `/opt/oxidns/config.yaml`。核心服务的 `-d` 工作目录必须与配置文件所在目录一致；systemd 安装和每次发布均检查此条件。官方 Debian 包采用 `/etc/oxidns` 配置、`/var/lib/oxidns` 工作目录的分离布局，需先调整核心服务与资源布局再接入，安装器会明确拒绝直接覆盖该布局。OpenRC 或自定义命令由管理员保证同目录运行。

systemd 检查也会核对 `-c`/`--config` 实际文件与 Agent 的 `config_path`，避免同目录中的两个文件造成“推送成功但服务仍使用旧配置”。相对配置路径按 OxiDNS 的实际工作目录解析；省略 `-c` 时按 `config.yaml` 检查。

安装器沿用现有单个 `configflow-agent` 系统服务。检测到另一核心的 Agent 配置时，会拒绝覆盖；可先卸载原 Agent，或用不同 Docker 实例。Agent 端口与 DNS 核心端口应避免冲突。

## Docker

统一镜像新增 `SERVICE_TYPE=oxidns` 和 `ENABLE_OXIDNS=true`，Agent 默认端口 `8082`。示例见 [docker-compose-oxidns.yaml](../docker/docker-compose-oxidns.yaml)。使用新增功能前必须构建或拉取包含本次适配的新 Agent 镜像。

- OxiDNS full 核心固定官方 `v1.6.1` 多架构镜像摘要，支持 `linux/amd64` 和 `linux/arm64`。
- `/etc/oxidns` 持久化配置、规则、事务备份与 `state/`；`/opt/configflow-agent` 持久化 Agent 身份。
- 默认映射 DNS UDP/TCP 53 及 Agent 8082；管理 API 9199 需要配置认证、监听地址和端口映射。
- WebUI 文件在 `/usr/share/oxidns/webui`，在 OxiDNS API 设置中填写该路径。
- Docker 同时启用 Mihomo 与 OxiDNS 时，Mihomo DNS 调整至 1053。MosDNS 与 OxiDNS 默认均使用 53，同一容器安装入口会拒绝同时启用二者；分别用实例与不同端口部署。
- 核心日志写入 `/var/log/supervisor/oxidns.log` 与 `oxidns.err.log`。原生运行优先读取配置中的日志文件，否则读取 OxiDNS systemd journal。

若已有外部规则更新定时任务，选择由外部任务或 OxiDNS 原生定时更新负责规则，避免两个任务同时写同一组文件。
