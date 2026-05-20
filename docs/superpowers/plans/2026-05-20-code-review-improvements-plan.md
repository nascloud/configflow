# ConfigFlow 代码审查改进实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复代码审查发现的 10 项问题，分三批完成：Bug 修复、代码质量优化、安全加固

**Architecture:** 所有改动都是小型、独立的修复。先创建共享工具模块，再逐个修复各文件中的问题。无外部依赖变更。

**Tech Stack:** Python 3 (Flask), Vue 3 (前端)

---

## 文件清单

| 文件 | 操作 | 任务 |
|------|------|------|
| `backend/utils/proxy_utils.py` | 新建 | 1 |
| `backend/converters/mihomo.py` | 修改 | 1, 3 |
| `backend/utils/sub_store_client.py` | 修改 | 1 |
| `backend/common/config.py` | 修改 | 2, 6 |
| `backend/routes/config.py` | 修改 | 2, 3, 4 |
| `backend/converters/mosdns.py` | 修改 | 3, 5 |
| `backend/app.py` | 修改 | 6 |
| `frontend/package.json` | 修改 | 7 |
| `backend/common/auth.py` | 修改 | 8 |
| `backend/routes/agents.py` | 修改 | 9 |

---

### Task 1: 创建共享 `proxy_utils.py`

**Files:**
- Create: `backend/utils/proxy_utils.py`
- Modify: `backend/converters/mihomo.py:1088-1115`（修改导入，删除重复函数）
- Modify: `backend/utils/sub_store_client.py:195-204`（改为共享导入）

- [ ] **Step 1: 创建 `backend/utils/proxy_utils.py`**

```python
"""代理工具函数"""


def fix_proxy_fields(proxy: dict) -> dict:
    """补全代理节点的必需字段（如 VLESS 的 encryption: none）。

    某些来源（Sub-Store、手动 YAML）可能缺少必需字段导致 Mihomo 报错。
    """
    if proxy and proxy.get('type') == 'vless':
        if proxy.get('encryption', '') in ('', 'zero', None):
            proxy['encryption'] = 'none'
        reality_opts = proxy.get('reality-opts')
        if isinstance(reality_opts, dict) and 'short-id' not in reality_opts:
            reality_opts['short-id'] = ''
    return proxy
```

- [ ] **Step 2: 删除 `backend/converters/mihomo.py:1073-1085` 的 `_fix_proxy_fields` 函数**

删除以下代码块：
```python
def _fix_proxy_fields(proxy: Dict[str, Any]) -> Dict[str, Any]:
    """补全 Mihomo 代理节点的必需字段。"""
    if proxy and proxy.get('type') == 'vless':
        if proxy.get('encryption', '') in ('', 'zero', None):
            proxy['encryption'] = 'none'
        reality_opts = proxy.get('reality-opts')
        if isinstance(reality_opts, dict) and 'short-id' not in reality_opts:
            reality_opts['short-id'] = ''
    return proxy
```

- [ ] **Step 3: 在 `backend/converters/mihomo.py` 中修改 import 和调用**

在文件顶部添加导入：
```python
from backend.utils.proxy_utils import fix_proxy_fields
```

将 `mihomo.py` 中所有 `_fix_proxy_fields(` 的调用改为 `fix_proxy_fields(`（共 2 处：第 1107 行和第 1114 行）。

- [ ] **Step 4: 修改 `backend/utils/sub_store_client.py`**

将 `sub_store_client.py:195-204` 的 `_fix_proxy_fields` 函数替换为导入：
```python
from backend.utils.proxy_utils import fix_proxy_fields
```

将 `sub_store_client.py` 中所有 `_fix_proxy_fields(` 的调用改为 `fix_proxy_fields(`（共 1 处：第 220 行）。

- [ ] **Step 5: 验证语法正确性**

Run: `python -c "from backend.utils.proxy_utils import fix_proxy_fields; print('OK')"`
Expected: `OK`（无导入错误）

- [ ] **Step 6: 提交**

```bash
git add backend/utils/proxy_utils.py backend/converters/mihomo.py backend/utils/sub_store_client.py
git commit -m "refactor: 提取共享函数 fix_proxy_fields 到 proxy_utils.py"
```

---

### Task 2: 修复配置导入数据丢失 + 补充默认配置

**Files:**
- Modify: `backend/common/config.py`（添加 `safe_import_config()`、补充默认值）
- Modify: `backend/routes/config.py`（调用 `safe_import_config`）

- [ ] **Step 1: 在 `backend/common/config.py` 的 `get_default_config()` 中添加 `subscription_aggregations`**

在 `get_default_config()` 函数中，在 `'system_config': {...}` 之后添加：
```python
'subscription_aggregations': [],
```

- [ ] **Step 2: 在 `backend/common/config.py` 中添加 `safe_import_config()` 函数**

在 `save_config()` 函数之后添加：
```python
def safe_import_config(new_data: Dict[str, Any]) -> None:
    """安全导入配置：与默认配置合并，避免丢失字段。"""
    default = get_default_config()
    merged = {}
    for key in default:
        merged[key] = new_data.get(key, default[key])
    for key in new_data:
        if key not in merged:
            merged[key] = new_data[key]
    config_data.clear()
    config_data.update(merged)
    save_config()
```

- [ ] **Step 3: 修改 `backend/routes/config.py` 的 `import_config()` 函数**

将 `config.py:218-225` 替换为：
```python
@config_bp.route('/import', methods=['POST'])
@require_auth
def import_config():
    try:
        from backend.common.config import safe_import_config
        safe_import_config(request.json)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
```

- [ ] **Step 4: 提交**

```bash
git add backend/common/config.py backend/routes/config.py
git commit -m "fix: 配置导入与默认配置合并，防止字段丢失"
```

---

### Task 3: 替换裸 `except:` 为 `except Exception:`

**Files:**
- Modify: `backend/routes/config.py:39, 91, 139, 207`
- Modify: `backend/converters/mihomo.py:124`
- Modify: `backend/converters/mosdns.py:218, 537, 1058-1059`

- [ ] **Step 1: 替换 `backend/routes/config.py` 中的 4 处 `except:`**

第 39 行、第 91 行、第 139 行的 `except:` → `except Exception:`

第 207 行的 `except:` → `except Exception:`

- [ ] **Step 2: 替换 `backend/converters/mihomo.py` 中的 `except:`**

第 124 行 `except:` → `except Exception:`

- [ ] **Step 3: 替换 `backend/converters/mosdns.py` 中的 3 处 `except:`**

第 218 行、第 537 行、第 1058-1059 行 `except:` → `except Exception:`

- [ ] **Step 4: 提交**

```bash
git add backend/routes/config.py backend/converters/mihomo.py backend/converters/mosdns.py
git commit -m "fix: 所有裸 except 替换为 except Exception，避免误吞系统信号"
```

---

### Task 4: 临时文件删除失败告警

**Files:**
- Modify: `backend/routes/config.py:204-208`

- [ ] **Step 1: 修改临时文件删除的异常处理**

将 `config.py:204-208` 替换为：
```python
            try:
                os.unlink(temp_file)
            except Exception as e:
                logger.warning(f"清理临时文件失败: {temp_file}, 错误: {e}")
```

- [ ] **Step 2: 提交**

```bash
git add backend/routes/config.py
git commit -m "fix: 临时文件删除失败时记录警告日志"
```

---

### Task 5: 消除 MosDNS 冗余 plugins 赋值

**Files:**
- Modify: `backend/converters/mosdns.py:1122`

- [ ] **Step 1: 删除 `mosdns.py` 第 1122 行的冗余赋值**

删除 `mosdns_config['plugins'] = plugins` 的第二次出现（第 1122 行），只保留第 1107 行的赋值。

- [ ] **Step 2: 提交**

```bash
git add backend/converters/mosdns.py
git commit -m "fix: 删除 mosdns 生成器中冗余的 plugins 赋值"
```

---

### Task 6: 消除 DATA_DIR 重复逻辑

**Files:**
- Modify: `backend/app.py:45-47`

- [ ] **Step 1: 将 `app.py` 中的 `DATA_DIR` 逻辑替换为导入**

在 `app.py` 顶部添加导入：
```python
from backend.common.config import DATA_DIR
```

删除 `app.py:45-47` 的 3 行重复逻辑：
```python
DATA_DIR = os.environ.get('DATA_DIR', '/data')
if not os.path.exists(DATA_DIR):
    DATA_DIR = '.'
```

- [ ] **Step 2: 提交**

```bash
git add backend/app.py
git commit -m "refactor: DATA_DIR 统一从 config.py 导入，消除重复逻辑"
```

---

### Task 7: 锁定前端 package.json 版本

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: 删除 `package.json` 中所有依赖的 `^` 前缀**

`dependencies` 和 `devDependencies` 中的所有 `"^X.Y.Z"` → `"X.Y.Z"`

- [ ] **Step 2: 提交**

```bash
git add frontend/package.json
git commit -m "chore: 锁定前端依赖版本，移除 ^ 前缀"
```

---

### Task 8: 增强 JWT 密钥检查

**Files:**
- Modify: `backend/common/auth.py:155-158`

- [ ] **Step 1: 替换密钥检查逻辑**

将 `auth.py:157` 的检查替换为：
```python
    elif len(JWT_SECRET_KEY.strip()) < 32 or 'your-secret-key' in JWT_SECRET_KEY.lower():
        invalid_vars.append('JWT_SECRET_KEY (must be at least 32 characters and not use default value)')
```

- [ ] **Step 2: 提交**

```bash
git add backend/common/auth.py
git commit -m "fix: 加强 JWT 密钥长度和默认值检查"
```

---

### Task 9: 添加 Agent 注册可选验证

**Files:**
- Modify: `backend/routes/agents.py:119-179`

- [ ] **Step 1: 在 `register_agent` 函数入口添加注册密钥检查**

在 `backend/routes/agents.py` 的 `register_agent()` 函数中，在获取 `agent_data` 之后添加：
```python
    # 可选的注册密钥验证
    registration_key = os.environ.get('AGENT_REGISTRATION_KEY', '')
    if registration_key:
        provided_key = agent_data.get('registration_key', '')
        if provided_key != registration_key:
            logger.warning(f"Agent 注册被拒绝：注册密钥不匹配（来源 IP: {request.remote_addr}）")
            return jsonify({'success': False, 'message': 'Invalid registration key'}), 403
```

在 `register_agent` 函数返回结果之前，从 agent_data 中移除 `registration_key`（避免存入配置）：
```python
    agent_data.pop('registration_key', None)
```

此代码放在获取 `agent_data` 之后、使用 `agent_data` 之前。

- [ ] **Step 2: 提交**

```bash
git add backend/routes/agents.py
git commit -m "feat: Agent 注册添加可选密钥验证机制 AGENT_REGISTRATION_KEY"
```

---

## 执行顺序说明

1. **Task 1**（共享模块）必须先于其他代码改动
2. **Tasks 2-7** 相互独立，可并行
3. **Tasks 8-9** 独立，可在最后或与 Tasks 2-7 并行
