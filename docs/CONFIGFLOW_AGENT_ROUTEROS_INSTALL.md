# ConfigFlow + Agent + RouterOS 安装指南

在 NAS 上管理配置，在 RouterOS 上运行代理和 DNS 服务。Agent 已包含在服务镜像中，不需要另装。

```text
NAS：ConfigFlow ── 管理 ──→ RouterOS：Mihomo 主、Mihomo 备、OxiDNS
客户端 ── DNS ──→ OxiDNS ── 直连域名 → 公共 DNS
                       └─ 代理域名 → Mihomo 主 / 备
```

**安装顺序：部署 ConfigFlow → 添加服务 → 发布配置 → 测试一台设备 → 全网启用。**

RouterOS 操作提供 **命令行** 和 **WinBox 手动配置** 两种方式，任选一种即可，配置结果相同。不要重复执行两套安装操作。

## 开始前

- NAS 已安装 Docker Compose；RouterOS 7 已安装 Container 软件包、启用容器功能，并有可写磁盘。
- 准备可用的代理订阅。本文默认使用 `latest` 镜像，无需选择版本。
- 保存 RouterOS 配置备份，记录原来的 DHCP DNS。已有服务时，先保留原服务。
- 管理端口仅对可信内网开放，不直接映射到公网。

下面的 IP 都是**示例**，请按自己的网段替换，并确认没有地址冲突：

| 设备 / 服务 | 示例地址 | 用途 |
|---|---|---|
| RouterOS | `192.168.50.254` | 网关、DHCP |
| NAS | `192.168.50.100:5801` | ConfigFlow 管理页面 |
| Mihomo 主 | `192.168.50.28` | 主代理；DNS 端口 `1053` |
| Mihomo 备 | `192.168.50.30` | 备用代理；DNS 端口 `1053` |
| OxiDNS | `192.168.50.251` | 客户端 DNS；端口 `53` |

RouterOS 的 LAN 网桥以下用 `bridge1`、存储目录用 `disk1/configflow` 举例，也需替换成实际名称。安装期间先不要修改客户端 DNS。

## 1. 在 NAS 安装 ConfigFlow

新建一个 `configflow` 文件夹，在其中保存 `compose.yaml`。镜像地址无需修改，只需填写管理员用户名、随机密码和随机密钥：

```yaml
services:
  config-flow:
    image: thsrite/config-flow:latest
    ports:
      - "5801:80"
    volumes:
      - ./data:/data
    environment:
      DATA_DIR: /data
      ADMIN_USERNAME: 'replace-with-your-admin-user'
      ADMIN_PASSWORD: 'replace-with-a-random-password'
      JWT_SECRET_KEY: 'replace-with-at-least-32-random-chars'
    restart: unless-stopped
```

在该文件夹中执行：

```sh
docker compose config --quiet
docker compose pull
docker compose up -d
```

打开 `http://192.168.50.100:5801`，使用刚设置的账号登录。看到管理页面即可继续；`data` 文件夹需要长期保留。Sub-Store 已内置，无需另装。

## 2. 在 RouterOS 添加三个服务

### 准备镜像

在有 Docker 的电脑或 NAS 上执行以下两条命令，下载最新 Agent 镜像并只导出目标架构。默认适用于 x86 RouterOS；ARM64 设备将**两处** `linux/amd64` 都改为 `linux/arm64`。`docker save --platform` 需要 Docker API 1.48+，详见 [Docker 镜像导出说明](https://docs.docker.com/reference/cli/docker/image/save/#platform)：

```sh
docker pull --platform linux/amd64 thsrite/config-flow-agent:latest
docker save --platform linux/amd64 -o configflow-agent.tar thsrite/config-flow-agent:latest
```

三个服务共用 `configflow-agent.tar`。下面任选一种方式上传镜像并创建服务；IP、网桥和磁盘路径需按实际环境替换。

### 选择配置方式

<details>
<summary>方式一：命令行（SSH 或 RouterOS Terminal）</summary>

**① 备份并准备上传目录**

以下 `routeros` 命令在 RouterOS 的 SSH 会话或 Terminal 中执行，不能在 NAS 的 Linux Shell 中执行。

```routeros
/export file=cf-before-install
/ip dhcp-server network print detail
/file print
```

记录当前 DNS，确认 `disk1` 是实际磁盘。新建安装目录；已有该目录时跳过这条命令：

```routeros
/file add name="disk1/configflow" type=directory
```

回到保存镜像 TAR 的**电脑或 NAS**，连接 SFTP。用户名、地址和 SSH 端口按实际填写：

```sh
ROS_USER='replace-with-routeros-user'
ROS_HOST='192.168.50.254'
ROS_SSH_PORT='22'
sftp -P "$ROS_SSH_PORT" "$ROS_USER@$ROS_HOST"
```

进入 `sftp>` 后执行。第一条将配置导出保存到本地，第二条上传镜像：

```text
get cf-before-install.rsc
put configflow-agent.tar disk1/configflow/configflow-agent.tar
bye
```

**② 创建网络、目录和容器**

回到 RouterOS。下面脚本仅用于新装，确认没有同名 `cf-main`、`cf-backup`、`cf-dns` 对象或旧服务目录后再执行；不要覆盖已有安装。**先修改开头的参数，再将包括最外层 `{ }` 的整段一起粘贴**，保证变量在同一作用域中。

先检查现有对象；名称或目录已被使用时停止，不能直接执行新装脚本：

```routeros
/container print detail
/interface veth print detail
/container mounts print detail
/container envs print detail
/file print
```

```routeros
{
    :local lanBridge "bridge1"
    :local gateway "192.168.50.254"
    :local serverUrl "http://192.168.50.100:5801"
    :local mainIP "192.168.50.28"
    :local backupIP "192.168.50.30"
    :local dnsIP "192.168.50.251"
    :local prefix "/24"
    :local base "disk1/configflow"
    :local imageFile ($base . "/configflow-agent.tar")

    /interface veth add name=cf-main address=($mainIP . $prefix) gateway=$gateway
    /interface veth add name=cf-backup address=($backupIP . $prefix) gateway=$gateway
    /interface veth add name=cf-dns address=($dnsIP . $prefix) gateway=$gateway
    :foreach name in={"cf-main";"cf-backup";"cf-dns"} do={
        /interface bridge port add bridge=$lanBridge interface=$name
    }

    :foreach dir in={"main-config";"main-state";"backup-config";"backup-state";"dns-config";"dns-state"} do={
        /file add name=($base . "/" . $dir) type=directory
    }
    /container mounts add list=cf-main-config src=($base . "/main-config") dst=/etc/mihomo
    /container mounts add list=cf-main-state src=($base . "/main-state") dst=/opt/configflow-agent
    /container mounts add list=cf-backup-config src=($base . "/backup-config") dst=/etc/mihomo
    /container mounts add list=cf-backup-state src=($base . "/backup-state") dst=/opt/configflow-agent
    /container mounts add list=cf-dns-config src=($base . "/dns-config") dst=/etc/oxidns
    /container mounts add list=cf-dns-state src=($base . "/dns-state") dst=/opt/configflow-agent

    /container envs add list=cf-main key=SERVER_URL value=$serverUrl
    /container envs add list=cf-main key=SERVICE_TYPE value="mihomo"
    /container envs add list=cf-main key=AGENT_NAME value="mihomo-main"
    /container envs add list=cf-main key=AGENT_IP value=$mainIP
    /container envs add list=cf-main key=AGENT_PORT value="8080"

    /container envs add list=cf-backup key=SERVER_URL value=$serverUrl
    /container envs add list=cf-backup key=SERVICE_TYPE value="mihomo"
    /container envs add list=cf-backup key=AGENT_NAME value="mihomo-backup"
    /container envs add list=cf-backup key=AGENT_IP value=$backupIP
    /container envs add list=cf-backup key=AGENT_PORT value="8080"

    /container envs add list=cf-dns key=SERVER_URL value=$serverUrl
    /container envs add list=cf-dns key=SERVICE_TYPE value="oxidns"
    /container envs add list=cf-dns key=AGENT_NAME value="oxidns"
    /container envs add list=cf-dns key=AGENT_IP value=$dnsIP
    /container envs add list=cf-dns key=AGENT_PORT value="8082"

    /container add file=$imageFile name=cf-main hostname=cf-main \
        interface=cf-main envlist=cf-main \
        mountlists=cf-main-config,cf-main-state \
        root-dir=($base . "/main-root") start-on-boot=no
    /container add file=$imageFile name=cf-backup hostname=cf-backup \
        interface=cf-backup envlist=cf-backup \
        mountlists=cf-backup-config,cf-backup-state \
        root-dir=($base . "/backup-root") start-on-boot=no
    /container add file=$imageFile name=cf-dns hostname=cf-dns \
        interface=cf-dns envlist=cf-dns \
        mountlists=cf-dns-config,cf-dns-state \
        root-dir=($base . "/dns-root") start-on-boot=no
}
```

如中途报错，先检查已经创建的对象，不要整段重跑。

**③ 等待解压并启动**

```routeros
/container print detail where name~"^cf-"
```

确认镜像解压完成、容器为 `stopped` 后，逐条启动；每次检查对应容器变成 `running` 再启动下一个：

```routeros
/container start [find where name="cf-main"]
/container start [find where name="cf-backup"]
/container start [find where name="cf-dns"]
/container print detail where name~"^cf-"
```

目录创建命令见 [RouterOS Files 文档](https://help.mikrotik.com/docs/spaces/ROS/pages/2555971/Files)。若设备版本不支持 `/file add type=directory`，可通过 SFTP 的 `mkdir` 创建同名目录后跳过对应建目录命令。

</details>

<details>
<summary>方式二：WinBox 手动配置</summary>

在 **Files → Backup** 保存配置备份并下载到本地；在 **IP → DHCP Server → Networks** 记录原来的 DNS。

在 **Files** 中创建 `disk1/configflow`，上传 `configflow-agent.tar`。然后依次填写以下四组参数；**每个容器使用独立目录**。

**① 网络接口**

在 **Interfaces → VETH** 添加以下接口，Gateway 都填 `192.168.50.254`。随后在 **Bridge → Ports** 将三个接口加入 LAN 网桥 `bridge1`。

| Name | Address |
|---|---|
| `cf-main` | `192.168.50.28/24` |
| `cf-backup` | `192.168.50.30/24` |
| `cf-dns` | `192.168.50.251/24` |

**② 持久化目录**

在 Files 中创建下表的 Src 目录，在 **Container → Mounts** 添加对应挂载。容器的 Root Dir 使用下一张容器表中的独立路径。

| List | Src（RouterOS 目录） | Dst（容器内目录） |
|---|---|---|
| `cf-main-config` | `disk1/configflow/main-config` | `/etc/mihomo` |
| `cf-main-state` | `disk1/configflow/main-state` | `/opt/configflow-agent` |
| `cf-backup-config` | `disk1/configflow/backup-config` | `/etc/mihomo` |
| `cf-backup-state` | `disk1/configflow/backup-state` | `/opt/configflow-agent` |
| `cf-dns-config` | `disk1/configflow/dns-config` | `/etc/oxidns` |
| `cf-dns-state` | `disk1/configflow/dns-state` | `/opt/configflow-agent` |

**③ 环境变量**

在 **Container → Envs** 中逐项添加。表头是 List 名称，每行是 Key 及对应的 Value：

| Key | `cf-main` | `cf-backup` | `cf-dns` |
|---|---|---|---|
| `SERVER_URL` | `http://192.168.50.100:5801` | `http://192.168.50.100:5801` | `http://192.168.50.100:5801` |
| `SERVICE_TYPE` | `mihomo` | `mihomo` | `oxidns` |
| `AGENT_NAME` | `mihomo-main` | `mihomo-backup` | `oxidns` |
| `AGENT_IP` | `192.168.50.28` | `192.168.50.30` | `192.168.50.251` |
| `AGENT_PORT` | `8080` | `8080` | `8082` |

`SERVER_URL` 必须是容器能访问的 NAS 地址，不能填 `localhost`。这里使用单服务模式，不需要再填写 `ENABLE_MIHOMO` 等开关。

**④ 容器**

在 **Container** 中添加三个容器，File 都选择 `disk1/configflow/configflow-agent.tar`：

| 字段 | Mihomo 主 | Mihomo 备 | OxiDNS |
|---|---|---|---|
| Name / Hostname | `cf-main` | `cf-backup` | `cf-dns` |
| Interface | `cf-main` | `cf-backup` | `cf-dns` |
| Root Dir | `disk1/configflow/main-root` | `disk1/configflow/backup-root` | `disk1/configflow/dns-root` |
| 环境变量列表（Envlist / Envlists） | `cf-main` | `cf-backup` | `cf-dns` |
| Mountlists | `cf-main-config,cf-main-state` | `cf-backup-config,cf-backup-state` | `cf-dns-config,cf-dns-state` |

保留默认入口命令。等待镜像解压结束后逐个点击 **Start**，状态应变为 `running`。安装验收通过后，再为三个容器开启 **Start On Boot**。

</details>

回到 ConfigFlow 的 **Agent 管理**，应看到 `mihomo-main`、`mihomo-backup`、`oxidns` 三条在线记录。Agent 自动注册，无需手工填写 Token。

> Agent 管理端口是 `8080 / 8082`，不是 DNS 端口。RouterOS 容器使用各自的 IP，不需要 Docker 端口映射。

## 3. 在 ConfigFlow 发布配置

### Mihomo：主、备各一份

1. 在「订阅管理」添加订阅，或在「节点管理」导入节点。
2. 创建主配置，设置策略组和分流规则；复制一份作为备用配置。
3. 两个 Mihomo Agent 分别绑定主、备配置，检查下面的设置。

| 设置 | 主配置 | 备用配置 |
|---|---|---|
| DNS 启用 | 是 | 是 |
| DNS 监听 | `0.0.0.0:1053` | `0.0.0.0:1053` |
| DNS 模式 | `fake-ip` | `fake-ip` |
| fake-IP 网段 | `100.127.0.0/17` | `100.126.0.0/17` |
| Mixed Port | `7890` | `7890` |
| TUN、自动路由 | 启用 | 启用 |

fake-IP 是供代理识别域名的虚拟地址。**两个实例的网段不能相同，也不能与现有 LAN、VPN 网络重叠。**

### OxiDNS：统一处理客户端 DNS

将 OxiDNS Agent 绑定到主配置，在 OxiDNS 配置页面设置：

| 设置 | 值 |
|---|---|
| UDP / TCP 监听 | 均为 `0.0.0.0:53` |
| 直连上游 | 可用的公共 DNS，例如 `https://223.5.5.5/dns-query` |
| 代理上游 | `192.168.50.28:1053` |
| 代理备用上游 | `192.168.50.30:1053` |
| 设备策略 | 将需要分流的客户端设为智能分流，其余按需直连 |

使用配置页面生成完整配置，不需要手写 DNS 插件。代理域名使用 Mihomo 上游，直连域名使用公共 DNS；备用上游不要填成公共 DNS。

依次为三个 Agent 点击 **推送配置 → 应用并启动**。发布结果均为 `succeeded`、服务状态正常后，再继续下一步。以后的配置修改也从这里发布。

## 4. 先测试一台设备

### 添加两条路由

任选一种方式添加两条静态路由；已有相同路由时不要重复添加。地址须与前面的 Mihomo 配置一致。

<details>
<summary>命令行：检查并添加路由</summary>

先检查已有路由：

```routeros
/ip route print detail where dst-address="100.127.0.0/17"
/ip route print detail where dst-address="100.126.0.0/17"
```

确认已有条目的网关和路由表正确后执行以下脚本；只在 `main` 表中没有对应目标网段时添加，不覆盖或重复创建已有条目：

```routeros
:if ([:len [/ip route find where dst-address="100.127.0.0/17" and routing-table="main"]] = 0) do={
    /ip route add dst-address=100.127.0.0/17 gateway=192.168.50.28 \
        routing-table=main comment="ConfigFlow main fake-IP"
}
:if ([:len [/ip route find where dst-address="100.126.0.0/17" and routing-table="main"]] = 0) do={
    /ip route add dst-address=100.126.0.0/17 gateway=192.168.50.30 \
        routing-table=main comment="ConfigFlow backup fake-IP"
}
```

</details>

<details>
<summary>WinBox：手动添加路由</summary>

在 **IP → Routes** 中添加：

| Dst. Address | Gateway | Routing Table |
|---|---|---|
| `100.127.0.0/17` | `192.168.50.28` | `main` |
| `100.126.0.0/17` | `192.168.50.30` | `main` |

</details>

已有策略路由或 mangle 标记的网络，需要确保这些目标在实际使用的路由表中也能到达对应 Mihomo。

### 检查上网

选一台内网设备，网关仍使用 RouterOS，只把 DNS 手动改为 `192.168.50.251`。将这台设备在 OxiDNS 中设为智能分流，然后检查：

- 直连网站和代理网站都能正常打开。
- OxiDNS 查询记录中能看到这台设备的真实 IP。
- Mihomo 中能看到代理网站的连接。

测试时关闭设备上的其他代理或 VPN，以及浏览器的安全 DNS，避免绕过这条链路。

<details>
<summary>可选：用命令检查 DNS 与代理连接</summary>

在这台测试设备上执行以下命令（需安装 `dig` 和 `curl`）。示例域名应分别命中你的代理规则和直连规则：

```sh
dig @192.168.50.251 www.google.com A +short
dig +tcp @192.168.50.251 www.google.com A +short
dig @192.168.50.251 www.baidu.com A +short
dig +tcp @192.168.50.251 www.baidu.com A +short
```

智能分流设备查询代理域名时应得到主或备用 fake-IP，直连域名应得到真实 IP。再分别检查两个 Mihomo：

```sh
dig @192.168.50.28 -p 1053 www.google.com A +short
dig @192.168.50.30 -p 1053 www.google.com A +short
```

结果应分别属于 `100.127.0.0/17`、`100.126.0.0/17`。验证 fake-IP 的 HTTPS 连接（先将变量替换为上一步的实际 IP；主、备各测一次）：

```sh
FAKE_IP='replace-with-returned-fake-ip'
curl --noproxy '*' --resolve "www.google.com:443:${FAKE_IP}" \
  --max-time 15 -sS -o /dev/null -w '%{http_code}\n' \
  https://www.google.com/generate_204
```

预期返回 `204`，不要用 `-k` 跳过证书检查。

</details>

## 5. 全网启用

单设备测试通过后，再任选一种方式修改 DHCP DNS。**只改 DNS，不改网关。**

<details>
<summary>命令行：切换 DNS 并开启自动启动</summary>

先查看 DHCP 网段及自定义选项：

```routeros
/ip dhcp-server network print detail
/ip dhcp-server print detail
/ip dhcp-server option print detail
/ip dhcp-server option sets print detail
```

将下面的网段和 DNS 改成实际值，确认只匹配目标网段后执行：

```routeros
/ip dhcp-server network set [find where address="192.168.50.0/24"] \
    dns-server=192.168.50.251
```

需要恢复时，用同一条命令将 `dns-server` 改回安装前记录的原值。

客户端续租并确认上网正常后，先检查下面的容器列表，确认每个名称仅对应一个运行中的容器，再为三个容器开启开机启动：

```routeros
/container print detail where name~"^cf-"
/container set [find where name="cf-main"] start-on-boot=yes
/container set [find where name="cf-backup"] start-on-boot=yes
/container set [find where name="cf-dns"] start-on-boot=yes
```

</details>

<details>
<summary>WinBox：切换 DNS 并开启自动启动</summary>

在 **IP → DHCP Server → Networks** 中打开对应网段：

- **DNS Servers** 改为 OxiDNS 地址：`192.168.50.251`。
- **Gateway** 保持原来的 RouterOS 地址，不改为 Mihomo。

客户端续租并确认上网正常后，在 **Container** 中为三个容器勾选 **Start On Boot**。需要恢复时，将 **DNS Servers** 改回原值。

</details>

两种方式都需确认 DHCP 自定义 DNS 选项或设备单独选项没有覆盖此设置。让客户端断开重连或续租，确认实际获得的新 DNS，并再次打开直连、代理网站。手动配置 DNS 的设备需单独修改。

至此安装完成，无需额外添加全网 DNS 劫持规则。

> 主备切换针对新的 DNS 查询；已经缓存的主实例 fake-IP 和已有连接不会自动迁移到备用实例。OxiDNS 本身仍是单实例。

## 遇到问题

| 现象 | 先检查 |
|---|---|
| Agent 没出现或离线 | 容器是否运行、`SERVER_URL` 是否可达、NAS 与 Agent 端口是否被防火墙拦截 |
| DNS 能解析，但代理网站打不开 | fake-IP 路由、Mihomo TUN、代理节点是否可用 |
| 只有部分设备不生效 | 设备实际 DNS、OxiDNS 设备策略、浏览器安全 DNS / 其他代理 |
| 全网启用后无法上网 | 将 DHCP DNS 恢复为原值，让客户端重新获取租约 |
| 配置发布失败 | 查看该次发布记录，修正错误后重发；需要恢复时使用「回滚到发布前」 |

日常备份保留 NAS 的完整 `data`、RouterOS 配置、各服务的配置和 Agent 身份目录。不要手工覆盖 Agent 管理的运行配置。

需要升级、监控或更复杂的分流时再阅读：[Agent 部署与回滚](../backend/agents/go-agent/docs/DEPLOYMENT.md)、[OxiDNS 监控配置](AGENT_OXIDNS.md)、[Docker 部署详解](../doc/module/deployment.md)。
