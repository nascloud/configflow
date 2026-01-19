# API 接口文档

Sublink Agent 提供了一套 RESTful API 接口，允许中心服务器远程管理服务。

## 认证机制

除健康检查接口外，所有 API 接口都需要在请求头中包含 Authorization 字段：

```
Authorization: Bearer <your-token>
```

Token 在 Agent 注册成功后自动生成并保存在配置文件中。

## 响应格式

所有 API 接口的响应都采用 JSON 格式：

```json
{
  "success": true,
  "message": "操作成功"
}
```

或

```json
{
  "success": false,
  "message": "错误信息"
}
```

## 接口列表

### 健康检查

检查 Agent 是否正常运行。

```
GET /health
```

**响应示例：**
```json
{
  "status": "ok"
}
```

### 更新配置

更新被管理服务的配置文件。

```
POST /api/config/update
Content-Type: application/json
```

**请求参数：**

| 参数 | 类型 | 必须 | 说明 |
|------|------|------|------|
| config | string | 是 | 服务配置内容 |
| directories | array | 否 | 需要创建的目录列表（仅 MosDNS） |
| ruleset_downloads | array | 否 | 规则集下载配置（仅 MosDNS） |

**请求示例（通用）：**
```json
{
  "config": "proxies:\n  - name: \"proxy\"\n    type: ss\n    server: server\n    port: 443\n    cipher: aes-128-gcm\n    password: \"password\""
}
```

**请求示例（MosDNS）：**
```json
{
  "config": "log:\n  level: info\n  file: \"/var/log/mosdns.log\"",
  "directories": [
    "rules",
    "geodata"
  ],
  "ruleset_downloads": [
    {
      "name": "geosite",
      "url": "https://example.com/geosite.dat",
      "local_path": "/etc/mosdns/geosite.dat"
    }
  ]
}
```

**响应示例：**
```json
{
  "success": true,
  "message": "Config updated"
}
```

### 重启服务

重启被管理的服务。

```
POST /api/restart
```

**响应示例：**
```json
{
  "success": true,
  "message": "Service restarted"
}
```

### 获取日志

获取服务的运行日志。

```
GET /api/logs?lines=100
```

**请求参数：**

| 参数 | 类型 | 必须 | 说明 |
|------|------|------|------|
| lines | integer | 否 | 获取日志行数，默认 100 |

**响应示例：**
```json
{
  "success": true,
  "logs": "2023-01-01 12:00:00 INFO Starting service\n2023-01-01 12:00:01 INFO Service started",
  "error_logs": "2023-01-01 12:00:00 ERROR Failed to load config",
  "service_name": "mihomo"
}
```

### 卸载服务

卸载 Agent 及相关服务。

```
POST /api/uninstall
```

**响应示例：**
```json
{
  "success": true,
  "message": "Uninstall completed"
}
```

## 基于端口的日志接口

根据请求端口自动返回对应服务的日志。

```
GET /logs?lines=100
```

此接口会根据请求的端口自动判断服务类型：
- 58080 端口：返回 Mihomo 日志
- 58081 端口：返回 MosDNS 日志

## 错误响应

当请求失败时，API 会返回相应的错误码和错误信息：

```json
{
  "success": false,
  "message": "错误描述"
}
```

常见的错误情况：
- 400：请求参数错误
- 401：认证失败
- 404：接口不存在
- 500：服务器内部错误

## 使用示例

### 更新 Mihomo 配置

```bash
curl -X POST \
  http://localhost:8080/api/config/update \
  -H 'Authorization: Bearer your-token' \
  -H 'Content-Type: application/json' \
  -d '{
    "config": "proxies:\n  - name: \"proxy\"\n    type: ss\n    server: server\n    port: 443\n    cipher: aes-128-gcm\n    password: \"password\""
  }'
```

### 重启服务

```bash
curl -X POST \
  http://localhost:8080/api/restart \
  -H 'Authorization: Bearer your-token'
```

### 获取日志

```bash
curl -X GET \
  "http://localhost:8080/api/logs?lines=50" \
  -H 'Authorization: Bearer your-token'
```