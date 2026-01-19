# 配置文件详解

Sublink Agent 支持多种配置方式，可以适应不同的部署环境和需求。

## 配置加载顺序

Agent 启动时按以下顺序查找配置文件：

1. 命令行参数指定的配置文件：`-config=/path/to/config.json`
2. 自动检测配置文件（按优先级排序）：
   - `/opt/sublink-agent/config-mihomo.json`（Mihomo 专用配置）
   - `/opt/sublink-agent/config-mosdns.json`（MosDNS 专用配置）
   - `/opt/sublink-agent/config.json`（通用配置）

## 基础配置字段

### server_url
- **类型**：字符串
- **必须**：是
- **说明**：中心服务器的 URL 地址
- **示例**：`"http://192.168.1.100:8080"`

### agent_name
- **类型**：字符串
- **必须**：是
- **说明**：Agent 的名称，在中心服务器上用于标识该 Agent
- **示例**：`"mihomo-agent-01"`

### agent_host
- **类型**：字符串
- **必须**：是
- **说明**：Agent 监听的主机地址
- **示例**：`"0.0.0.0"`（监听所有接口）或 `"127.0.0.1"`（仅本地）

### agent_port
- **类型**：整数
- **必须**：是
- **说明**：Agent 监听的端口号
- **示例**：`8080`

### agent_ip
- **类型**：字符串
- **必须**：否
- **说明**：Agent 的外网 IP 地址，用于注册时告知中心服务器
- **示例**：`"203.0.113.1"`

### service_type
- **类型**：字符串
- **必须**：是
- **说明**：服务类型，目前支持 `mihomo` 和 `mosdns`
- **示例**：`"mihomo"`

### service_name
- **类型**：字符串
- **必须**：是
- **说明**：服务名称，必须与 Supervisor 中配置的服务名称一致
- **示例**：`"mihomo"`

### config_path
- **类型**：字符串
- **必须**：是
- **说明**：被管理服务的配置文件路径
- **示例**：`"/etc/mihomo/config.yaml"`

### restart_command
- **类型**：字符串
- **必须**：是
- **说明**：重启服务的命令
- **示例**：`"supervisorctl -c /etc/supervisor/supervisord.conf restart mihomo"`

### heartbeat_interval
- **类型**：整数
- **必须**：否
- **默认值**：30
- **说明**：心跳间隔（秒）
- **示例**：`60`

### agent_id
- **类型**：字符串
- **必须**：否
- **说明**：Agent ID，注册成功后自动生成
- **示例**：`"agent-1234567890"`

### token
- **类型**：字符串
- **必须**：否
- **说明**：认证 Token，注册成功后自动生成
- **示例**：`"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."`

## MosDNS 特殊配置字段

### directories
- **类型**：字符串数组
- **必须**：否
- **说明**：需要创建的目录列表，用于存放规则文件等
- **示例**：
  ```json
  "directories": [
    "rules",
    "geodata",
    "cache"
  ]
  ```

### ruleset_downloads
- **类型**：对象数组
- **必须**：否
- **说明**：规则集下载配置
- **字段**：
  - `name`：规则集名称
  - `url`：下载地址
  - `local_path`：本地保存路径

- **示例**：
  ```json
  "ruleset_downloads": [
    {
      "name": "geosite",
      "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/v2ray-rules-dat@release/geosite.dat",
      "local_path": "/etc/mosdns/geosite.dat"
    },
    {
      "name": "geoip",
      "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/v2ray-rules-dat@release/geoip.dat",
      "local_path": "/etc/mosdns/geoip.dat"
    }
  ]
  ```

## 环境变量配置

在 Docker 部署中，可以通过环境变量来配置 Agent：

### 通用环境变量

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `SERVER_URL` | 中心服务器地址 | 无 |
| `HEARTBEAT_INTERVAL` | 心跳间隔（秒） | 60 |
| `AGENT_IP` | Agent 外网 IP | 自动检测 |

### Mihomo 相关环境变量

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `ENABLE_MIHOMO` | 是否启用 Mihomo | false |
| `AGENT_MIHOMO_NAME` | Mihomo Agent 名称 | mihomo-agent |
| `AGENT_MIHOMO_PORT` | Mihomo Agent 端口 | 8080 |

### MosDNS 相关环境变量

| 环境变量 | 说明 | 默认值 |
|---------|------|--------|
| `ENABLE_MOSDNS` | 是否启用 MosDNS | false |
| `AGENT_MOSDNS_NAME` | MosDNS Agent 名称 | mosdns-agent |
| `AGENT_MOSDNS_PORT` | MosDNS Agent 端口 | 8081 |

## 配置示例

### Mihomo 配置示例

```json
{
  "server_url": "http://192.168.1.100:8080",
  "agent_name": "mihomo-agent-01",
  "agent_host": "0.0.0.0",
  "agent_port": 8080,
  "agent_ip": "203.0.113.1",
  "service_type": "mihomo",
  "service_name": "mihomo",
  "config_path": "/etc/mihomo/config.yaml",
  "restart_command": "supervisorctl -c /etc/supervisor/supervisord.conf restart mihomo",
  "heartbeat_interval": 60
}
```

### MosDNS 配置示例

```json
{
  "server_url": "http://192.168.1.100:8080",
  "agent_name": "mosdns-agent-01",
  "agent_host": "0.0.0.0",
  "agent_port": 8081,
  "agent_ip": "203.0.113.1",
  "service_type": "mosdns",
  "service_name": "mosdns",
  "config_path": "/etc/mosdns/config.yaml",
  "restart_command": "supervisorctl -c /etc/supervisor/supervisord.conf restart mosdns",
  "heartbeat_interval": 60,
  "directories": [
    "rules",
    "geodata"
  ],
  "ruleset_downloads": [
    {
      "name": "geosite",
      "url": "https://cdn.jsdelivr.net/gh/Loyalsoldier/v2ray-rules-dat@release/geosite.dat",
      "local_path": "/etc/mosdns/geosite.dat"
    }
  ]
}
```

## 配置更新

配置文件会在以下情况下自动更新：

1. **注册成功后**：Agent 会将获得的 `agent_id` 和 `token` 保存到配置文件中
2. **API 更新**：通过 `/api/config/update` 接口更新服务配置时，会备份旧配置并写入新配置

配置文件更新时会自动创建备份，备份文件名为 `原文件名.backup.时间戳`。