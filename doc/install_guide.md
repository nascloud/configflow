# Config Flow 安装与入门指南

## 开始之前
- 确认机器上已安装 Docker（Windows、macOS、Linux 均适用）。
- 预留一个可访问的端口，例如 `80`，便于浏览器访问。
- 建议准备一个空文件夹，用来保存 Config Flow 的数据备份。

---

## 快速部署
> 目标：把服务跑起来，确保能打开页面。

在准备好的文件夹中创建 `docker-compose.yml`：
```yaml
version: '3.8'
services:
  config-flow:
    image: thsrite/config-flow:latest
    ports:
      - "80:80"
    volumes:
      - ./data:/data
    environment:
      - ADMIN_USERNAME=admin
      - ADMIN_PASSWORD=admin123
      - JWT_SECRET_KEY=your-secret-key-please-change-in-production
    restart: unless-stopped
```

执行 `docker-compose up -d` 启动服务。

启动完成后：
- 访问 `http://localhost`，出现登录页说明部署成功。
- `./data` 保存 ConfigFlow 数据（含内置 Sub-Store 数据 `./data/sub-store`），方便以后迁移或备份。

> 认证是可选的：不设置 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 即无需登录直接使用；设置后才开启登录，此时请把 `ADMIN_PASSWORD` 和 `JWT_SECRET_KEY` 替换为更安全的值。
> 镜像已内置 [Sub-Store](https://github.com/sub-store-org/Sub-Store) 后端，无需另外部署；可在「系统设置 → 第三方依赖」检测并在线更新。如需改用自己的 Sub-Store，在「系统设置」填写其 API 地址即可。
> 从旧版升级：可删除 compose 中的 `sub-store` 服务和 `SUB_STORE_URL` 环境变量，改用内置 Sub-Store（保留 `SUB_STORE_URL` 则继续使用外部 Sub-Store）。

---

## 首次配置流程
> 目标：导入资源、设置策略、生成第一份配置。

1. **导入资源**
   - 「订阅管理」：添加机场订阅链接。
   - 「节点管理」：适合逐条录入或批量导入单个节点。
   - 在顶部选择当前配置，资源在下一步的策略组中直接引用。
2. **整理策略**
   - 在「策略管理」按用途建立策略组，例如“办公直连”“全局代理”。
   - 需要分流的规则，可提前在「规则仓库」整理常用规则集。
3. **生成配置**
   - 进入「规则配置」，把规则与策略组关联。
   - 前往「配置生成」，选择目标格式（Mihomo / Surge / MosDNS），预览后下载并导入客户端。

完成以上三步，就能在客户端看到新的连接策略。

> 提示：新建配置不会自动使用全部共享资源；先在策略组直接选择资源并编排规则。需要「前置 → 落地」时新增代理链类型（仅 Mihomo），原节点仍可单独使用。共享资源修改会影响所有引用者。

---

## 功能速览
- **订阅管理**：统一管理多个订阅源，通过 Sub-Store 解析订阅和转换节点格式。
- **节点管理**：支持常见协议，批量导入、启停，通过 Sub-Store 自动转换节点格式。
- **策略管理**：URL-Test、Fallback、Load-Balance 等策略，支持从订阅、节点中选择，支持跟随其他策略组（复用其他策略组的策略）。
- **规则仓库与规则配置**：集中维护规则集，并按顺序控制流量去向。
- **配置生成**：预览与下载当前配置；完整 JSON 备份、脱敏导出、恢复与重置统一位于「系统设置」，作用于整个系统。

---

## 常见问题
**Q：没有 Docker 经验怎么办？** 安装 Docker Desktop 或 Docker Engine 后，直接复制上面的命令即可运行。

**Q：想让其他人访问该服务？** 启动命令中的 `-p 80:80` 可调整为服务器对外端口，例如 `-p 8080:80`，再开放防火墙即可。

**Q：换电脑或重装系统会丢配置吗？** 不会，`data` 文件夹已经挂载在本地，复制该文件夹即可恢复。

**Q：如何升级？** 先备份完整数据目录（包括 `system.json`、`profiles/` 和 `.bak`，不能只备份根 `config.json`），确认 Compose 的 `/data` 仍挂载原宿主机目录，再执行 `docker-compose pull && docker-compose up -d`。旧版多配置会自动迁移并保留 Agent 绑定；遇到迁移错误请保留原数据和日志，不要重置。详见[升级、备份与恢复](design/multi-config-management.md#升级备份与恢复)。

---

现在就启动 Config Flow，完成部署与首份配置，后续即可根据业务需要继续拓展规则和策略。
