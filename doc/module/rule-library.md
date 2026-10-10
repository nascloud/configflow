# 规则仓库

规则仓库是所有配置共用的规则来源目录，集中保存名称、URL/内容与格式。各配置通过 `library_rule_id` 引用，分别保存策略目标、启用状态与顺序；来源更新会动态作用于全部引用者，不复制来源字段到各配置。

操作区中，内容类型规则集的「添加规则」使用加号并排在第一位；「加入当前配置」使用纸飞机发送图标，将规则集加入当前配置的策略规则。列表、卡片和批量加入入口使用一致的图标含义。

## 规则下载代理

在「系统设置 → 服务配置 → 规则下载代理」填写 HTTP/HTTPS 代理地址，例如 `http://192.168.0.3:7890`，失去焦点后自动保存。所有配置空间共用此设置，后续规则下载、缓存更新、连通性测试、MosDNS 转换与后端推送预下载都会使用它，无需修改系统或 Docker DNS。

支持 `http://用户名:密码@主机:端口`，用户名和密码中的特殊字符需要 URL 编码。代理必须能从 ConfigFlow 容器访问；容器中的 `127.0.0.1` 指容器自身。代理不可用时不会自动改走直连；已有的规则缓存兜底逻辑保持不变。清空并保存后恢复现有网络方式，包括原有环境代理设置。

「GitHub 代理域名」仍是下载地址的镜像前缀，可与规则下载代理同时使用。自身 API 回调绕过外部代理，远程规则使用正常域名解析，兼容 Fake-IP 与内网规则地址，并继续校验 HTTPS 证书。本设置只控制 ConfigFlow 后端拉取，不会设置订阅解析服务、客户端或远端 Agent 自行下载时的代理。

完整备份包含代理设置；脱敏导出会移除代理地址及其中的凭据。

## 主要功能

### 1. 规则类型

支持三种规则集类型：

**domain（域名规则）**
- 包含域名列表的规则集
- 支持精确域名和域名后缀匹配
- 最常用的规则类型

**ipcidr（IP段规则）**
- 包含 IP 地址段（CIDR）的规则集
- 用于基于 IP 的分流
- 支持 IPv4 和 IPv6

**classical（传统规则）**
- 包含多种规则类型混合的规则集
- 兼容 Clash 传统规则格式
- 可以包含 DOMAIN、DOMAIN-SUFFIX、IP-CIDR 等多种规则

### 2. 规则来源类型

**URL 类型**
- 从远程 URL 获取规则内容
- 支持标准的 Clash rule-provider 格式
- 适合使用开源规则集

**内容类型**
- 直接在界面中编辑规则内容
- 规则内容存储在本地数据库
- 适合自定义规则集

### 3. 添加规则

**添加 URL 类型规则：**
```
名称：微信直连
类型：domain
来源：URL
URL：https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/wechat.txt
```

**添加内容类型规则：**
```
名称：公司内网
类型：domain
来源：content
内容：
  - company.com
  - internal.company.com
  - mail.company.com
```

### 4. 批量导入规则

支持从 YAML 格式的 rule-providers 配置批量导入。

**YAML 格式示例：**
```yaml
rule-providers:
  reject:
    type: http
    behavior: domain
    url: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/reject.txt"
    interval: 86400

  proxy:
    type: http
    behavior: domain
    url: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/proxy.txt"
    interval: 86400

  direct:
    type: http
    behavior: domain
    url: "https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/direct.txt"
    interval: 86400
```

导入后会自动创建三个规则：`reject`、`proxy`、`direct`

### 5. 规则引用跟踪

每个规则会显示被哪些规则配置引用：
- 显示引用数量
- 点击可查看引用详情
- 删除被引用的规则会返回 `409` 并列出配置名称，必须先在相应配置移除引用

### 6. 规则管理操作

**编辑规则**
- 修改规则名称
- 修改规则 URL 或内容
- 修改规则类型

**删除规则**
- 删除规则会同时删除规则文件
- 被任何配置引用时拒绝删除，不自动清理其他配置的规则编排

**启用/禁用规则**
- 禁用规则不会出现在生成的配置中
- 可以暂时禁用而不删除

**拖拽排序**
- 调整规则在仓库中的显示顺序
- 不影响规则配置中的优先级

## 使用场景

**场景1：使用开源规则集**
```
1. 添加 Loyalsoldier 规则集合
   - 直连规则：https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/direct.txt
   - 代理规则：https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/proxy.txt
   - 拒绝规则：https://cdn.jsdelivr.net/gh/Loyalsoldier/clash-rules@release/reject.txt

2. 在规则配置中引用
   - 创建规则集，从仓库选择
   - 配置对应的策略
```

**场景2：自定义企业规则**
```
名称：公司内网域名
类型：domain
来源：content
内容：
  - gitlab.company.local
  - jenkins.company.local
  - nexus.company.local
  - wiki.company.local
```

**场景3：批量导入规则**
```yaml
# 准备好 rule-providers YAML 配置
rule-providers:
  google:
    behavior: domain
    url: "https://example.com/google.txt"

  telegram:
    behavior: domain
    url: "https://example.com/telegram.txt"

  streaming:
    behavior: domain
    url: "https://example.com/streaming.txt"

# 点击批量导入，粘贴上述YAML
# 系统会自动创建三个规则
```
