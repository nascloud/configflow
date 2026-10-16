# 家庭网络：iKuai 虚拟机部署 OxiDNS + mihomo 回流处理

本文记录在 iKuai 路由器的虚拟机里部署 OxiDNS、接入 ConfigFlow 管理，并与旁路 mihomo（fake-ip 模式）配合，同时解决 mihomo 看不到真实客户端 IP（回流 / SNAT）问题的完整过程，包含实际踩过的坑。

文中地址均为示例，请替换成自己的网络规划：

| 角色 | 示例地址 | 说明 |
|---|---|---|
| iKuai 路由器 | `192.168.1.1` | 网关，LAN 口 `lan1` |
| ConfigFlow | `192.168.1.2:5001` | Docker 部署 |
| mihomo | `192.168.1.4` | iKuai 虚拟机，TUN 模式，DNS 监听 `:1053` |
| OxiDNS | `192.168.1.6` | iKuai 虚拟机，替换原 MosDNS |
| fake-ip 网段 | `198.18.0.0/16` | mihomo `fake-ip-range` |

## 1. 整体架构

```
客户端 ──DNS──▶ iKuai ──转发──▶ OxiDNS(:53)
                                 ├─ 直连规则集 / 国内 IP ──▶ 国内 DoH（真实 IP）
                                 └─ 代理规则集 / 未匹配   ──▶ mihomo :1053（fake-ip）

客户端 ──访问 198.18.x.x──▶ iKuai（静态路由，下一跳 mihomo）──▶ mihomo 按策略组出站
                              ◀──── 回包经路由表 104 强制回到 iKuai ────┘
```

要点：

- **凡是在 mihomo 里有路由策略的规则集，都放进 OxiDNS 的“代理规则集”**，交给 mihomo 返回 fake-ip；只有策略为“全球直连”的规则集放“直连规则集”。否则 DNS 返回真实 IP，流量绕过 mihomo，分流/换节点不生效。
- 代理路径必须 **fail-closed**：mihomo 不可用时宁可解析失败，也不能回落到公共 DNS 拿真实 IP（会以家宽 IP 直连，造成泄露）。

## 2. 在 iKuai 创建虚拟机

路径：**高级服务 → 虚拟机 → 添加**。参考配置（1 核 / 512MB 足够）：

- 安装磁盘：数据盘；系统类型 Linux；CPU 1 核；内存 512 MB
- 设备管理 → 添加 → 磁盘：新建 1 GB，开启半虚拟化
- 光驱：Alpine `alpine-virt-*.iso`（可在“磁盘管理 → 文件管理”上传）
- 网卡：桥接 `lan1`，MAC 自动生成
- VNC 端口：选未占用端口；开机自启、硬件加速开启；UEFI 关闭

> 首次安装时**不要**把磁盘设为启动盘，让虚拟机从 ISO 启动；系统装好后再设为启动盘并清空光驱。iKuai 在只有一块磁盘时会自动标记为启动盘。

## 3. 安装 Alpine

### 3.1 VNC 控制台的输入坑

iKuai 网页 VNC 对需要 Shift 的符号转换不可靠：`&`、`|`、`>`、`:`、`"`、`?`、`_` 等可能被错打或丢失（大写字母正常）。建议只在 VNC 里做最少的事——开网络和 SSH，然后改用 SSH：

```sh
# 以 root 登录 Live 系统（无密码）
setup-interfaces -a -r          # DHCP 获取地址
setup-sshd -c openssh
mkdir -p /root/.ssh; vi /root/.ssh/authorized-keys   # 粘贴公钥后 Esc，再 ZZ 保存
# 文件名里的下划线打不出来时，用 printf 八进制转义改名（反引号内需要四个反斜杠）：
mv /root/.ssh/authorized-keys `printf /root/.ssh/authorized\\\\137keys`
```

### 3.2 用应答文件无人值守安装

SSH 登录 Live 系统后：

```sh
cat > /tmp/answers <<'EOF'
KEYMAPOPTS=none
HOSTNAMEOPTS=oxidns
DEVDOPTS=mdev
INTERFACESOPTS="auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp
"
DNSOPTS=none
TIMEZONEOPTS="-z Asia/Shanghai"
PROXYOPTS=none
APKREPOSOPTS="https://mirrors.ustc.edu.cn/alpine/v3.21/main https://mirrors.ustc.edu.cn/alpine/v3.21/community"
USEROPTS=none
SSHDOPTS="-c openssh"
ROOTSSHKEY="<你的 SSH 公钥>"
NTPOPTS="-c chrony"
DISKOPTS="-m sys /dev/vda"
LBUOPTS=none
APKCACHEOPTS=none
EOF
ERASE_DISKS=/dev/vda setup-alpine -e -f /tmp/answers
```

装完后设置 root 密码（供 VNC 应急登录），关机；在 iKuai 编辑虚拟机：磁盘设为启动盘、清空光驱，再开机。

如需允许 root 用密码 SSH 登录，在 `/etc/ssh/sshd_config` 设置 `PermitRootLogin yes`、`PasswordAuthentication yes` 后 `rc-service sshd restart`（`sshd -t` 先校验）。

## 4. 安装 OxiDNS

```sh
apk add curl ca-certificates
curl -fsSL https://oxidns.org/install.sh | sh     # Alpine 无 sudo，直接以 root 执行
```

脚本会安装到 `/opt/oxidns`、注册 OpenRC 服务 `oxidns` 并启动。执行前建议先下载阅读脚本内容。

## 5. 接入 ConfigFlow Agent

1. ConfigFlow 需为支持 OxiDNS Go Agent 的版本（过旧版本会提示 *Go Agent transactional deployment supports mihomo and mosdns*，先更新镜像）。
2. **Agent → 添加**：安装方式 Shell，服务类型 OxiDNS，端口如 `10800`，配置文件路径 **`/opt/oxidns/config.yaml`**（OxiDNS 服务的 `-d` 工作目录必须与配置所在目录一致）。
3. 在虚拟机上执行生成的命令，选择 `1) 安装或升级`。
4. Agent 记录的地址**不会随心跳自动更新**。虚拟机改 IP 后，需要在 Agent 页面（或 `PUT /api/agents/<id>` 的 `host` 字段）手动改成新地址，否则推送和监控仍连旧地址。

## 6. 在 ConfigFlow 中配置 OxiDNS

路径：**配置生成 → OxiDNS → 设置**。

### 6.1 核心设置

- 点击“填入已验证性能参数”。
- 工作线程数按 vCPU 数设置（1 核填 1）。
- 屏蔽查询类型保留 `65`（HTTPS 记录）和 `255`，避免 HTTPS 记录里的 IP 提示绕过 fake-ip。

### 6.2 分流规则

- **直连规则集**：仅放 mihomo 中策略为“全球直连”的规则集（如 Direct、ChinaMax、Private、Lan 等）。
- **代理规则集**：其余所有在 mihomo 里有策略的规则集，包括国内 App 但需要换节点的（小红书、抖音等）、苹果、微软、REJECT 类。
- 单条 IP / GEOIP 规则对按域名分流的 DNS 无意义，可不选。
- **自定义 Hosts**：迁移原 MosDNS 的 hosts（格式 `域名 IP`，每行一条），例如：

```yaml
- nas.example.com 192.168.1.5
- router.example.com 192.168.1.1
```

### 6.3 DNS 上游（最关键）

| 项 | 推荐值 | 原因 |
|---|---|---|
| 直连 DNS | `https://223.5.5.5/dns-query`、`https://120.53.53.53/dns-query` | **必须用 DoH**，见下方“环路” |
| 代理 DNS / Fake-IP 上游 | `udp://192.168.1.4:1053` | mihomo 的 fake-ip DNS |
| 代理响应 TTL | 1 | 与 fake-ip 配合 |
| 备用 DNS | `udp://192.168.1.4:1053`（同样指向 mihomo），并发 1 | fail-closed，绝不回落公共 DNS |

> **⚠️ 环路坑**：若 iKuai 劫持了 LAN 内所有 53 端口查询并转发给 OxiDNS，而 OxiDNS 的直连上游用的是 `udp://x.x.x.x:53`，那么 OxiDNS 发往公网的查询会被 iKuai 截回给 OxiDNS 自己，形成查询风暴：请求量暴涨、约一半查询失败、平均响应数秒。DoH 走 443 端口不受影响。
>
> 检测方法：在 OxiDNS 主机上查询一个**只存在于自定义 hosts 里**的域名：
> ```sh
> dig @223.5.5.5 nas.example.com +short
> ```
> 如果返回的是 hosts 里的内网地址，说明 UDP 53 被劫持回了自己。

> 备用 DNS 不能直接清空：模板里 `forward_fallback` 插件仍存在，上游为空会校验失败。指向 mihomo 即可达到“去掉备用”的效果。

### 6.4 优选 IPv4 要配探测上游

快捷写法 `exec: prefer_ipv4` 是兼容模式，可能让后续处理链执行两次。在 YAML 视图中改为完整插件，并**让探测走 mihomo**（若探测走国内 DNS，代理域名的 AAAA 查询会额外把 A 记录查询发给国内 DNS，造成泄露）：

```yaml
- tag: prefer_v4
  type: prefer_ipv4
  args:
    probe_executor: forward_remote
    cache: true
    cache_ttl: 3600
# sequence_main 中把 `- exec: prefer_ipv4` 改为：
#   - exec: $prefer_v4
```

改完后“常用设置”里“优先 IPv4”开关会显示为关闭（表单识别不了完整写法），配置实际生效，不要再去打开它。

### 6.5 缓存、API、查询记录

- **缓存持久化**：开启，文件 `./state/cache.dump`，间隔 600 秒。每次推送都会重启 OxiDNS，持久化可避免冷缓存。
- **WebUI / 日志 → 启用 API**：ConfigFlow 的“OxiDNS 监控”依赖它，监听 `127.0.0.1:9199` 即可（Agent 在本机访问）。
- **查询记录**：谨慎开启。fake-ip TTL 为 1 秒，客户端会频繁重查，家庭环境每秒几十次查询，SQLite 每分钟可增长约 2 MB，1 GB 小盘几小时就会写满。若客户端 DNS 经 iKuai 转发，记录里的来源几乎都是路由器地址，排查价值有限。没有“只记录错误”的选项；需要看错误时，用日志（级别 `warn`，写文件并轮转）。
  - 清空历史：`curl -X DELETE http://127.0.0.1:9199/api/plugins/query_recorder/records`
  - 关闭前建议先清空历史，否则停止服务时刷盘过久，Agent 可能判定“停止后服务仍在运行”而回滚发布。

### 6.6 发布

保存草稿 → 生成 → 推送。推送返回 202 只代表已受理，必须等部署状态变为 `succeeded`（可在 Agent 页查看发布记录，或轮询 `GET /api/agents/<id>/deployments/<deployment_id>`）。若上一次部署停在中间状态，下一次推送会因“未完成的发布”返回 409。

## 7. mihomo 的 fake-ip-filter

OxiDNS 交给 mihomo 的都是“应走代理”的域名，它们必须拿到 fake-ip。如果 `fake-ip-filter` 里放了与代理规则集重叠的大集合（典型如 ChinaMax，它包含 stripe.com、xiaohongshu.com 等），这些域名会拿到真实 IP，流量绕过 mihomo。

推荐（blacklist 模式，去掉 ChinaMax 这类大而杂的集合）：

```yaml
dns:
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  fake-ip-filter-mode: blacklist
  fake-ip-filter:
    - "rule-set:private_direct"
    - "rule-set:Direct"
    - "rule-set:Tracker"
    - "rule-set:PrivateTracker"
    - "rule-set:AmazonCN"
    - "rule-set:Private"
    - "rule-set:Lan"
```

> **⚠️ 不要用 `fake-ip-filter-mode: rule` 逐条镜像 rules**：实测（mihomo v1.19.x）在 classical 规则集较多时，每次分配 fake-ip 都会触发真实解析，导致 mihomo DNS 延迟从个位数毫秒升到数百毫秒，约一半查询出错/超时。用 `mihomo -t` 只能校验语法，校验不出性能问题，改完一定要实测 DNS 延迟和出错率。

## 8. 无损替换旧 DNS（沿用原 IP）

目标：OxiDNS 接管原 MosDNS 的 IP，客户端无需改 DNS 设置。

1. 切换前在新机器上验证完毕（hosts、国内/代理域名、泄露测试）。
2. 新机器预先写好静态配置 `/etc/network/interfaces`（`address 192.168.1.6/24`、`gateway 192.168.1.1`），暂不生效。
3. 切换（尽量在一两秒内完成）：
   - 旧机器：`kill -9 $(pidof udhcpc)`；`ip addr del 192.168.1.6/24 dev eth0`；停止旧服务并关机。
   - 新机器：`kill -9 $(pidof udhcpc)`；`ip addr add 192.168.1.6/24 dev eth0`；`arping -U -c 3 -I eth0 192.168.1.6`；删除旧 DHCP 地址并补默认路由。
4. iKuai：**DHCP 静态分配**里把该 IP 的 MAC 改成新机器；旧虚拟机关闭“开机自启”（否则旧机开机会通过 DHCP 拿回同一 IP 造成冲突）。
5. ConfigFlow 中把 Agent 的 host 改为新 IP。

> **⚠️ 踩坑**：不要用 `pkill udhcpc`（SIGTERM）。busybox udhcpc 退出时会执行 deconfig，清空网卡上的**所有**地址，连刚加上的新 IP 一起删掉，导致 DNS 中断，只能从 VNC 控制台手动恢复。`kill -9` 不会触发 deconfig。切换时建议另开一个循环持续 `dig @192.168.1.6`，实时观察是否断流。

## 9. mihomo 回流：让 mihomo 看到真实客户端 IP

### 9.1 现象与原因

iKuai 用静态路由把 `198.18.0.0/16`（以及 Telegram 等直连 IP 段）的下一跳指向同网段的 mihomo。流量进出都是 `lan1`，iKuai 对这类回流流量做 SNAT，mihomo 面板里所有连接的来源都显示成路由器地址。

只关闭 SNAT 不够：mihomo 回包会直接发给同网段客户端、不经过 iKuai，来回路径不对称，iKuai 连接跟踪可能丢弃后续包。所以需要在 mihomo 主机上把回包**强制送回 iKuai**。

### 9.2 iKuai：对代理网段免 SNAT

**网络配置 → NAT 规则 → 添加**：动作“过滤”，源地址为除 mihomo 外的 LAN 地址（如 `192.168.1.1-192.168.1.3`、`192.168.1.5-192.168.1.255`），目的地址为 `198.18.0.0/16` 及 Telegram 段（`91.108.4.0/22`、`91.108.8.0/22`、`91.108.12.0/22`、`91.108.16.0/22`、`91.108.20.0/22`、`91.108.56.0/22`、`91.105.192.0/23`、`149.154.160.0/20`、`185.76.151.0/24`），协议任意。

> 建议先完成 9.3 再启用这条规则。9.3 单独部署时，回包本来就是发往路由器的，行为不变，可以无风险地先上线。

### 9.3 mihomo 主机：连接标记 + 策略路由（TUN 模式版）

TUN 模式（`auto-route` + `auto-redirect`）下，TCP 与 UDP 的回包路径不同，规则必须两条都覆盖：

- **TCP**：在 prerouting 被重定向到 mihomo 本地端口，回包由本地进程发出，经过 **output** 链。
- **UDP**：经路由进入 TUN 网卡 `Meta`，回包从 `Meta` 出来再转发，**不经过 output 链**，需要在 prerouting 对来自 `Meta` 的包恢复标记。

标记不要用 `0x4`：mihomo auto-redirect 默认 mark `0x2024` 含 `0x4` 位，会误伤 mihomo 自身流量。下面用 `0x400000`。

`/etc/mihomo-return.nft`：

```nft
destroy table inet mihomo_return
table inet mihomo_return {
	set proxy_dst {
		type ipv4_addr
		flags interval
		elements = { 198.18.0.0/16, 91.108.4.0/22, 91.108.8.0/22, 91.108.12.0/22,
			     91.108.16.0/22, 91.108.20.0/22, 91.108.56.0/22, 91.105.192.0/23,
			     149.154.160.0/20, 185.76.151.0/24 }
	}

	chain prerouting {
		type filter hook prerouting priority mangle; policy accept;
		iifname "eth0" ip daddr @proxy_dst ct state new ct mark set ct mark | 0x400000 comment "return-mark"
		iifname "Meta" ct mark & 0x400000 == 0x400000 meta mark set meta mark | 0x400000 comment "return-restore-tun"
	}

	chain output {
		type route hook output priority mangle; policy accept;
		ct mark & 0x400000 == 0x400000 meta mark set meta mark | 0x400000 comment "return-restore-local"
	}
}
```

`/etc/local.d/mihomo-return.start`（Alpine OpenRC 开机执行，需 `rc-update add local default`）：

```sh
#!/bin/sh
nft -f /etc/mihomo-return.nft
ip route replace default via 192.168.1.1 dev eth0 table 104
ip route replace 192.168.1.0/24 via 192.168.1.1 dev eth0 table 104   # 同网段客户端的回包也先送到 iKuai
ip rule del priority 104 2>/dev/null
ip rule add fwmark 0x400000/0x400000 lookup 104 priority 104
sysctl -qw net.ipv4.conf.all.send_redirects=0 net.ipv4.conf.eth0.send_redirects=0
```

`/etc/local.d/mihomo-return.stop`（回滚用）：

```sh
#!/bin/sh
ip rule del priority 104 2>/dev/null
ip route flush table 104 2>/dev/null
nft destroy table inet mihomo_return 2>/dev/null
```

依赖：`apk add nftables iproute2`。使用独立的 nft 表，不触碰 mihomo 自己的 `inet mihomo` 表；mihomo 重启后规则依然保留。不要启用 Alpine 的 `nftables` 服务，它默认会 `flush ruleset`，把 mihomo 运行时生成的表一起清掉。

上线顺序：`nft -c -f /etc/mihomo-return.nft` 校验语法 → 执行 start 脚本 → 确认访问正常 → 再启用 9.2 的 NAT 过滤规则。

### 9.4 验证

在 mihomo 主机上通过 external-controller 查看连接来源：

```sh
curl -s -H "Authorization: Bearer <secret>" http://127.0.0.1:<port>/connections \
  | python3 -c "import sys,json,collections;c=json.load(sys.stdin)['connections'];print(collections.Counter(x['metadata']['sourceIP'] for x in c))"
```

新建立的连接应显示各客户端的真实 IP；启用规则前已建立的旧连接会逐渐被替换。

## 10. 验证清单

```sh
# 国内域名 → 真实 IP；代理域名 → 198.18.x.x；hosts → 内网地址
for d in baidu.com claude.ai stripe.com nas.example.com; do dig +short @192.168.1.6 $d; done

# 环路检测（应返回公网结果，不能是 hosts 里的内网地址）
dig @223.5.5.5 nas.example.com +short

# OxiDNS 运行指标（API 在本机）
curl -s http://127.0.0.1:9199/api/metrics | grep -E '^forward_(query|error|timeout)_total'
```

泄露自查：用随机子域名查询代理域名的 A/AAAA，观察 `forward_local` 计数是否增加，或在查询记录中确认这些查询的执行轨迹只经过 mihomo（`forward_remote`），没有经过 `forward_local` / `local_probe`。也可以使用在线检测页面（如 ip.net.coffee）查看 DNS 出口。

## 11. 已知限制

- **未匹配任何规则集的域名**会先发给国内 DNS 探测（国内 IP 则直连，否则交给 mihomo）。如果要求境外陌生域名也不经过国内 DNS，需要把“未匹配”直接交给 mihomo，由 mihomo 按 GEOIP 决定，代价是这部分国内流量也要经过 mihomo。
- mihomo 基础配置通常也会下发给手机客户端；去掉 ChinaMax 的 fake-ip-filter 后，手机上国内域名也会拿到 fake-ip，流量要先经过 mihomo 再按规则直连，功能正常。
- 本文只处理 IPv4 回流；IPv6 需要另行配置同样的连接标记与策略路由。
