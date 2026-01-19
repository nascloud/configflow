# 部署指南

Sublink Agent 支持多种部署方式，推荐使用 Docker 部署以获得最佳的隔离性和可维护性。

## 系统要求

- Linux 系统（推荐 Ubuntu 20.04+ 或 CentOS 8+）
- Docker 20.10+（如果使用 Docker 部署）
- 至少 512MB 内存
- 至少 100MB 磁盘空间

## Docker 部署（推荐）

### 1. 安装 Docker 和 Docker Compose

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install docker.io docker-compose

# CentOS/RHEL
sudo yum install docker docker-compose

# 启动 Docker 服务
sudo systemctl start docker
sudo systemctl enable docker
```

### 2. 克隆项目代码

```bash
git clone <repository-url>
cd go-agent
```

### 3. 配置环境变量

编辑 [docker-compose.yaml](file:///vol1/1000/docker/go-agent/docker-compose.yaml) 文件，修改环境变量：

```yaml
environment:
  # --- 通用配置 ---
  - TZ=Asia/Shanghai
  - SERVER_URL=http://your-server-address:port  # 修改为中心服务器地址
  - HEARTBEAT_INTERVAL=15
  - AGENT_IP=192.168.50.100  # 修改为本机外网IP

  # --- Mihomo 控制 ---
  - ENABLE_MIHOMO=true
  - AGENT_MIHOMO_NAME=mihomo
  - AGENT_MIHOMO_PORT=58080

  # --- MosDNS 控制 ---
  - ENABLE_MOSDNS=true
  - AGENT_MOSDNS_NAME=mosdns
  - AGENT_MOSDNS_PORT=58081
```

### 4. 启动服务

```bash
docker-compose up -d
```

### 5. 查看日志

```bash
# 查看所有服务日志
docker-compose logs -f

# 查看特定服务日志
docker-compose logs -f appliance
```

### 6. 停止服务

```bash
docker-compose down
```

## 手动部署

### 1. 安装依赖

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install supervisor

# CentOS/RHEL
sudo yum install supervisor
```

### 2. 编译程序

```bash
# 安装 Go 1.21+
wget https://go.dev/dl/go1.21.0.linux-amd64.tar.gz
sudo tar -C /usr/local -xzf go1.21.0.linux-amd64.tar.gz
export PATH=$PATH:/usr/local/go/bin

# 编译
go build -o sublink-agent .
```

### 3. 创建配置文件

创建配置目录：
```bash
sudo mkdir -p /opt/sublink-agent
```

创建配置文件（根据需要选择 Mihomo 或 MosDNS）：

**Mihomo 配置文件 [/opt/sublink-agent/config-mihomo.json](file:///opt/sublink-agent/config-mihomo.json)：**
```json
{
  "server_url": "http://your-server-address:port",
  "agent_name": "mihomo-agent",
  "agent_host": "0.0.0.0",
  "agent_port": 8080,
  "service_type": "mihomo",
  "service_name": "mihomo",
  "config_path": "/etc/mihomo/config.yaml",
  "restart_command": "supervisorctl restart mihomo",
  "heartbeat_interval": 60
}
```

**MosDNS 配置文件 [/opt/sublink-agent/config-mosdns.json](file:///opt/sublink-agent/config-mosdns.json)：**
```json
{
  "server_url": "http://your-server-address:port",
  "agent_name": "mosdns-agent",
  "agent_host": "0.0.0.0",
  "agent_port": 8081,
  "service_type": "mosdns",
  "service_name": "mosdns",
  "config_path": "/etc/mosdns/config.yaml",
  "restart_command": "supervisorctl restart mosdns",
  "heartbeat_interval": 60
}
```

### 4. 配置 Supervisor

复制 Supervisor 配置文件：
```bash
sudo mkdir -p /etc/supervisor/conf.d
sudo cp supervisor/*.conf /etc/supervisor/conf.d/
```

### 5. 启动服务

```bash
# 启动 Supervisor
sudo systemctl start supervisor
sudo systemctl enable supervisor

# 启动 Agent
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start agent-mihomo  # 或 agent-mosdns
```

## 配置说明

### 端口映射

在 Docker 部署中，以下端口需要映射：

| 容器端口 | 主机端口 | 说明 |
|---------|---------|------|
| 58080 | 58080 | Mihomo Agent 端口 |
| 58081 | 58081 | MosDNS Agent 端口 |
| 7890 | 57890 | Mihomo Mix Proxy |
| 9090 | 59090 | Mihomo SOCKS Proxy |
| 53/udp | 55553/udp | MosDNS DNS 服务 |

### 数据卷挂载

Docker 部署中会挂载以下目录：

- `./config/mihomo:/etc/mihomo`：Mihomo 配置目录
- `./config/mosdns:/etc/mosdns`：MosDNS 配置目录

请确保这些目录在主机上存在并有适当的权限。

### 环境变量

| 变量名 | 说明 | 默认值 |
|-------|------|--------|
| `SERVER_URL` | 中心服务器地址 | 无 |
| `HEARTBEAT_INTERVAL` | 心跳间隔（秒） | 60 |
| `AGENT_IP` | Agent 外网 IP | 自动检测 |
| `ENABLE_MIHOMO` | 是否启用 Mihomo | false |
| `ENABLE_MOSDNS` | 是否启用 MosDNS | false |

## 故障排除

### 1. Docker 容器无法启动

检查日志：
```bash
docker-compose logs appliance
```

常见问题：
- 端口被占用：修改 [docker-compose.yaml](file:///vol1/1000/docker/go-agent/docker-compose.yaml) 中的端口映射
- 环境变量配置错误：检查 `SERVER_URL` 等配置

### 2. Agent 无法注册到中心服务器

检查网络连接：
```bash
# 在容器内测试
docker-compose exec appliance ping your-server-address
```

检查配置：
- 确认 `SERVER_URL` 配置正确
- 确认中心服务器正常运行

### 3. 服务无法重启

检查 Supervisor 配置：
```bash
# 在容器内检查
docker-compose exec appliance supervisorctl status
```

确认服务名称与配置文件中的 `service_name` 一致。

### 4. 日志无法获取

检查日志目录权限：
```bash
# 在容器内检查
docker-compose exec appliance ls -la /var/log/supervisor/
```

确保日志目录存在且有读取权限。

## 升级指南

### Docker 部署升级

```bash
# 拉取最新代码
git pull

# 重新构建镜像
docker-compose build

# 重启服务
docker-compose down
docker-compose up -d
```

### 手动部署升级

```bash
# 拉取最新代码
git pull

# 重新编译
go build -o sublink-agent .

# 重启服务
sudo supervisorctl restart agent-mihomo  # 或 agent-mosdns
```

## 安全建议

1. **使用 HTTPS**：在生产环境中，建议使用 HTTPS 加密通信
2. **防火墙配置**：只开放必要的端口
3. **定期更新**：定期更新 Agent 和相关服务
4. **权限控制**：确保配置文件和日志文件的权限设置正确
5. **备份配置**：定期备份重要配置文件