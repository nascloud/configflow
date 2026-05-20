# ConfigFlow 代码审查改进方案

## 概述

基于代码审查结果，对 ConfigFlow 项目进行三批改进：Bug 修复、代码质量优化、安全加固。

---

## 第一批：Bug 修复

### 1. 配置导入数据丢失

**问题：** `backend/routes/config.py:219-221` 中 `import_config()` 直接调用 `global_config.clear()` + `global_config.update(request.json)`。如果请求 JSON 缺少某些顶层字段（如 `subscription_aggregations`、`surge`、`mosdns`），这些字段会被清空，导致配置损坏。

**方案：** 新增 `safe_import_config(new_data)` 函数：
1. 获取 `get_default_config()` 作为基底字典
2. 用 `new_data` 的顶层字段覆盖基底字典中对应字段
3. 将合并结果更新到 `config_data`
4. 调用 `save_config()`

**涉及文件：** `backend/routes/config.py`、`backend/common/config.py`

### 2. 裸 `except:` → `except Exception:`

**问题：** 代码中 7 处使用 `except:`，会误吞 `KeyboardInterrupt`、`SystemExit` 等信号。

**涉及位置：**
- `backend/routes/config.py:39, 91, 139, 207`
- `backend/converters/mihomo.py:124`
- `backend/converters/mosdns.py:218, 537, 1058-1059`

**方案：** 全部替换为 `except Exception:`。其中 `config.py:207` 的 `except: pass` 改为记录 `logger.warning` 后再 `pass`。

### 3. `subscription_aggregations` 加入默认配置

**问题：** `get_default_config()`（`backend/common/config.py:26-59`）不包含 `subscription_aggregations` 键。虽然大部分代码用 `.get()` 安全访问，但存在未发现的 `KeyError` 风险。

**方案：** 在 `get_default_config()` 返回值中添加 `'subscription_aggregations': []`。

---

## 第二批：代码质量

### 4. 消除 `_fix_proxy_fields` 重复

**问题：** `backend/converters/mihomo.py:1073-1085` 和 `backend/utils/sub_store_client.py:195-203` 有完全相同的函数定义。

**方案：** 新建 `backend/utils/proxy_utils.py`，将 `_fix_proxy_fields` 提取到该模块。两个原位置改为从此模块导入。保持 `sub_store_client.py` 中的名称兼容性（可通过 `from backend.utils.proxy_utils import _fix_proxy_fields` 引用）。

### 5. 消除 MosDNS 冗余的 plugins 赋值

**问题：** `backend/converters/mosdns.py:1107` 和 `1122` 两次执行 `mosdns_config['plugins'] = plugins`，后者完全冗余。

**方案：** 删除第 1122 行的 `mosdns_config['plugins'] = plugins`。

### 6. `package.json` 版本锁定

**问题：** `frontend/package.json` 中所有依赖使用 `^` 前缀，不同构建环境可能拉取不同版本。

**方案：** 删除 `^` 改为精确版本号，生成并提交 `package-lock.json`（或确认已提交的 lock 文件存在）。

### 7. `DATA_DIR` 重复逻辑消除

**问题：** `backend/app.py:45-47` 和 `backend/common/config.py:15-17` 有完全相同的 `DATA_DIR` 回退逻辑。

**方案：** `backend/common/config.py` 中已定义 `DATA_DIR`，`app.py` 改为 `from backend.common.config import DATA_DIR`，删除重复的 3 行回退逻辑。

---

## 第三批：安全加固

### 8. JWT 默认密钥检查增强

**问题：** `backend/common/auth.py:157` 只检查了 `'your-secret-key-change-this-in-production'` 和 `'your-secret-key-please-change-in-production'` 两个特定字符串。密钥强度检查不够严格。

**方案：** 强化 `validate_required_env_vars()` 中的生产模式检查，替换原有仅检查两个特定字符串的逻辑：
- 密钥长度 < 32 个字符 → 拒绝启动
- 密钥包含 `your-secret-key` 子串 → 拒绝启动
- 开发模式行为不变（保持宽松）

### 9. 临时文件删除失败告警

**问题：** `backend/routes/config.py:207` 的 `except: pass` 静默忽略临时文件删除失败。

**方案：** 改为 `except Exception as e: logger.warning(...)`，记录 `os.unlink` 失败的警告日志。

### 10. Agent 注册可选验证机制

**问题：** `/api/agents/register` 是公开端点，无身份验证。攻击者可注册虚假 Agent。

**方案：** 添加 `AGENT_REGISTRATION_KEY` 环境变量支持。当配置该变量时：
- Agent 首次注册需在请求体中携带 `registration_key` 字段
- 匹配则允许注册，不匹配返回 403
- 未配置该变量时保持向后兼容

---

## 实施顺序

1. 新建共享工具模块 `proxy_utils.py`（解决第 4 项，这是其他改动的基础）
2. 修复配置导入（第 1 项）
3. 替换裸 `except`（第 2 项） + 临时文件告警（第 9 项）
4. 补充默认配置（第 3 项）
5. 消除重复逻辑（第 5、7 项）+ 锁版本（第 6 项）
6. 安全加固（第 8、10 项）

## 涉及文件清单

| 文件 | 改动项 |
|------|--------|
| `backend/common/config.py` | 1, 3, 7 |
| `backend/routes/config.py` | 1, 2, 9 |
| `backend/converters/mihomo.py` | 2, 4 |
| `backend/converters/mosdns.py` | 2, 5 |
| `backend/utils/sub_store_client.py` | 4 |
| `backend/utils/proxy_utils.py` | **新建** |
| `backend/common/auth.py` | 8 |
| `backend/routes/agents.py` | 10 |
| `backend/app.py` | 7 |
| `frontend/package.json` | 6 |
