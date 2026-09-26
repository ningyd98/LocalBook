# ReadFlow 端到端测试快速指南

## 快速运行

```bash
cd /path/to/LocalBook
python3 tests/integration/simple_e2e_test.py
```

**预期输出**: 3 个场景全部通过，显示 `🎉 所有端到端测试通过！`

---

## 测试场景

### 场景 1: 离线阅读 → 同步
**验证**: 离线保存 → 同步状态流转 (local_only → pending → synced)

**步骤**:
1. 创建设备、Source、Session
2. 保存 3 条 Highlight + 2 条 Note (离线)
3. 模拟联网后同步
4. 验证数据完整性

### 场景 2: AI 查询
**验证**: 对话创建 → 多轮消息 → 历史记录保存

**步骤**:
1. 创建 Highlight
2. 创建 Conversation
3. 添加 6 条消息 (3 轮对话)
4. 验证消息数量和角色

### 场景 3: 多设备同步
**验证**: 设备 A 创建数据 → 设备 B 获取数据

**步骤**:
1. 注册设备 A 和设备 B
2. 设备 A 创建 Highlight (synced)
3. 设备 B 查询同步数据
4. 验证数据一致性

---

## 测试结果解读

### ✅ 成功输出
```
======================================================================
🎉 所有端到端测试通过！
======================================================================

验收标准:
✅ 离线数据保存 (3 Highlights + 2 Notes)
✅ 同步状态流转 (local_only → pending → synced)
✅ AI 对话历史保存 (6 条消息，3 轮对话)
✅ 多设备数据同步 (设备 A → 服务器 → 设备 B)
✅ 无数据丢失
✅ 数据一致性验证通过
```

### ❌ 失败处理
如果测试失败，检查：
1. SQLite schema 文件是否存在: `server/reader/migrations/001_init_reader_schema.sql`
2. 数据库表结构是否匹配
3. 外键约束是否启用 (`PRAGMA foreign_keys = ON`)

---

## 修改测试

### 添加更多测试数据

编辑 `tests/integration/simple_e2e_test.py`：

```python
# 增加 Highlight 数量
highlights = [
    ("uuid1", "Text 1"),
    ("uuid2", "Text 2"),
    ("uuid3", "Text 3"),
    ("uuid4", "Text 4"),  # 新增
]
```

### 添加新测试场景

```python
def test_scenario_4_custom():
    """自定义测试场景"""
    print("\n场景 4: 自定义测试")

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        conn = init_database(str(db_path))
        cursor = conn.cursor()

        # 你的测试逻辑
        # ...

        conn.close()
        return True

# 在 main() 中添加
results.append(("场景 4: 自定义", test_scenario_4_custom()))
```

---

## 数据库 Schema 参考

### 核心表

```sql
-- 设备
devices (id, device_name, device_type, created_at, last_seen)

-- 来源
reader_sources (id, type, title, url, file_path, created_at)

-- 会话
reader_sessions (id, device_id, source_id, started_at)

-- 高亮
reader_highlights (id, source_id, session_id, selected_text, sync_state)

-- 笔记
reader_notes (id, source_id, highlight_id, content, sync_state)

-- 对话
reader_conversations (id, source_id, highlight_id, session_id)

-- 消息
reader_messages (id, conversation_id, role, content)

-- 同步队列
reader_sync_outbox (id, operation_type, entity_type, entity_id, payload)
```

### Sync State 枚举
- `local_only`: 仅本地，未同步
- `pending`: 等待同步
- `synced`: 已同步

---

## 常见问题

### Q: 为什么不使用 pytest？
A: 沙箱环境限制无法运行 pytest，使用原生 Python + SQLite 直接测试。

### Q: 测试会影响真实数据吗？
A: 不会。测试使用临时数据库 (`tempfile.TemporaryDirectory()`)，测试结束后自动删除。

### Q: 如何测试真实的 macOS 客户端？
A: 本测试只验证数据层。客户端功能需要在真实设备上手动测试：
1. 构建 ReadFlow.app
2. 安装 Safari Extension
3. 测试划词、FloatingToolbar、全局快捷键

### Q: 如何测试实际的网络同步？
A: 需要启动 LocalBook 服务器，并编写集成测试调用 API。本测试聚焦于数据库层。

---

## 后续测试建议

### Phase 3.2 - 手动测试清单

#### macOS 客户端
- [ ] 安装 ReadFlow.app
- [ ] 测试 Accessibility 权限请求
- [ ] Safari Extension 划词捕获
- [ ] FloatingToolbar 显示和交互
- [ ] 全局快捷键 (Cmd+Shift+C)
- [ ] MenuBar 图标和菜单

#### 服务端
- [ ] 启动 LocalBook 服务器
- [ ] Bonjour 服务发现 (`dns-sd -B _localbook._tcp`)
- [ ] 设备配对流程 (POST /pair, POST /register)
- [ ] 同步 API (POST /sync/push, GET /sync/pull)
- [ ] AI 查询 (POST /ask)

#### 端到端集成
- [ ] 离线划词 → 联网后自动同步
- [ ] AI 查询 → 显示答案和 Citations
- [ ] 两台设备同步 (Mac + iPad)

---

## 文档链接

- **完整测试报告**: `docs/e2e-test-report.md`
- **开发计划**: `docs/readflow-development-plan.md`
- **数据库 Schema**: `server/reader/migrations/001_init_reader_schema.sql`
- **测试脚本**: `tests/integration/simple_e2e_test.py`

---

## 贡献

如果添加新的测试场景，请：
1. 在 `simple_e2e_test.py` 中添加 `test_scenario_X()` 函数
2. 更新本文档的「测试场景」部分
3. 运行完整测试套件确保通过
4. 更新 `docs/e2e-test-report.md`

---

**最后更新**: 2025-01-XX
**维护者**: integration-tester
