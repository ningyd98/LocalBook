# ReadFlow 开发计划

## 产品定位

**ReadFlow** 是 LocalBook 的 macOS 原生阅读采集客户端，负责捕获用户在网页、PDF 和文档中的阅读行为（划词、高亮、笔记、AI 对话），并通过离线优先的增量同步机制将阅读记忆沉淀到 LocalBook 知识库。

ReadFlow ≠ LocalBook
- **ReadFlow**：Capture + Interaction + Cache（感知器）
- **LocalBook**：Storage + Intelligence + Knowledge（知识核心）

---

## 架构原则

1. **离线优先（Offline First）**：任何阅读操作不依赖 LocalBook 实时在线
2. **增量同步**：基于 UUIDv7 的对象级同步，使用 Outbox Pattern
3. **轻量客户端**：不运行 AI 模型、Vector DB，依赖 LocalBook Service
4. **数据保真**：阅读时间、Session、上下文完整保留

---

## 开发阶段规划

### Phase 1: LocalBook 服务端基础设施（2-3周）

**目标**：为 ReadFlow 提供完整的服务端支持

#### P1.1 数据模型设计（3天）

**LocalBook 端新增表结构**：

```python
# server/reader/models.py

# 1. 设备管理
devices (
    id UUID PRIMARY KEY,
    device_name TEXT,
    device_type TEXT,  # macos/ios/android
    created_at TIMESTAMP,
    last_seen TIMESTAMP,
    pairing_token_hash TEXT,
    public_key TEXT
)

# 2. 阅读来源
reader_sources (
    id UUID PRIMARY KEY,
    type TEXT,  # web/pdf/document/markdown
    title TEXT,
    url TEXT,
    canonical_url TEXT,
    file_path TEXT,
    file_hash TEXT,  # SHA-256
    created_at TIMESTAMP,
    last_read_at TIMESTAMP,
    metadata JSONB
)

# 3. 阅读 Session
reader_sessions (
    id UUID PRIMARY KEY,
    device_id UUID,
    source_id UUID,
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    duration INTEGER,
    highlight_count INTEGER,
    query_count INTEGER,
    note_count INTEGER
)

# 4. Highlight
reader_highlights (
    id UUID PRIMARY KEY,
    source_id UUID,
    session_id UUID,
    selected_text TEXT,
    context_before TEXT,
    context_after TEXT,
    page INTEGER,
    location TEXT,
    created_at TIMESTAMP,
    sync_state TEXT  # local_only/pending/synced
)

# 5. Note
reader_notes (
    id UUID PRIMARY KEY,
    source_id UUID,
    highlight_id UUID,
    session_id UUID,
    content TEXT,
    created_at TIMESTAMP,
    updated_at TIMESTAMP,
    revision INTEGER,
    sync_state TEXT
)

# 6. Conversation
reader_conversations (
    id UUID PRIMARY KEY,
    source_id UUID,
    highlight_id UUID,
    session_id UUID,
    created_at TIMESTAMP
)

# 7. Message
reader_messages (
    id UUID PRIMARY KEY,
    conversation_id UUID,
    role TEXT,  # user/assistant/system
    content TEXT,
    model TEXT,
    citations JSONB,
    created_at TIMESTAMP
)

# 8. Sync Outbox (LocalBook 端)
reader_sync_outbox (
    id INTEGER PRIMARY KEY,
    entity_type TEXT,
    entity_id UUID,
    operation TEXT,  # create/update/delete
    payload JSONB,
    created_at TIMESTAMP,
    processed_at TIMESTAMP,
    retry_count INTEGER
)
```

**验收标准**：
- SQLite schema migration 脚本完成
- 所有表支持 UUIDv7 主键
- 索引覆盖常用查询路径（device_id, source_id, session_id, sync_state）

---

#### P1.2 Reader API 实现（5天）

**新增路由模块** `server/reader/`：

```python
# server/reader/router.py

# 设备配对
POST   /api/v1/reader/pair
POST   /api/v1/reader/register
GET    /api/v1/reader/status

# 同步
POST   /api/v1/reader/sync/push      # 批量推送 1-100 个操作
GET    /api/v1/reader/sync/pull      # 基于 cursor 拉取变更
GET    /api/v1/reader/capabilities   # 返回服务端能力

# AI 查询
POST   /api/v1/reader/ask            # 带上下文的 AI 查询
POST   /api/v1/reader/search         # 跨 Reader+LocalBook 的搜索

# 高级（V2）
GET    /api/v1/reader/related        # 关联笔记推荐
POST   /api/v1/reader/reindex        # 重新索引 Reader 数据
```

**核心实现**：

1. **Pairing 流程**：
   - 生成一次性 pairing token（6位数字码）
   - 客户端提交 device_id + token
   - 返回 access_token（JWT），存入 macOS Keychain
   - Token 有效期 5 分钟

2. **Push Sync**：
   - 接收批量操作（create/update/delete）
   - 验证 device_id 和 access_token
   - 原子性写入 + Outbox 入队
   - 返回同步结果与 conflict 列表

3. **Pull Sync**：
   - 基于 last_sync_cursor 返回增量
   - 游标使用时间戳 + offset
   - 单次最多返回 100 条
   - 返回 next_cursor

4. **AI Ask**：
   - 接收：conversation_id, source_id, highlight_id, query, context
   - 调用 LocalBook 现有 RAG + AI Provider
   - 保存对话历史到 reader_messages
   - 返回答案 + citations

**验收标准**：
- 所有端点实现完成并通过 pytest
- 错误体沿用现有契约 `{"error":{"code","message"}}`
- API 文档更新到 `docs/api-reference.md`

---

#### P1.3 Bonjour 服务发现（2天）

**实现**：

```python
# server/discovery/bonjour.py

from zeroconf import ServiceInfo, Zeroconf
import socket

class BonjourService:
    """
    广播 LocalBook Service
    类型：_localbook._tcp.local.
    """

    def start(self, port: int):
        info = ServiceInfo(
            "_localbook._tcp.local.",
            f"LocalBook on {socket.gethostname()}._localbook._tcp.local.",
            addresses=[socket.inet_aton("0.0.0.0")],
            port=port,
            properties={
                "version": "1.3.0",
                "reader_api": "v1"
            }
        )
        self.zeroconf = Zeroconf()
        self.zeroconf.register_service(info)

    def stop(self):
        self.zeroconf.unregister_all_services()
        self.zeroconf.close()
```

**验收标准**：
- 局域网内可被 macOS Bonjour 发现
- 手动测试：`dns-sd -B _localbook._tcp`

---

#### P1.4 Reader Connector 集成（3天）

**LocalBook 知识库集成**：

```python
# server/reader/importer.py

class ReaderImporter:
    """
    将 Reader 数据转化为 LocalBook Markdown
    """

    async def import_session(self, session_id: UUID):
        """
        按 Session 生成 Markdown 文档：

        LocalBook/Reading/2026/09/Cerebras-WSE.md

        内容：
        # Cerebras WSE

        source: https://...
        created: 2026-09-12
        session_id: {session_id}

        ## Highlights

        ### 19:04
        > Wafer Scale Engine...

        ## AI Conversations

        Q: WSE 是 ASIC 吗？
        A: ...

        ## Notes

        我的思考...
        """

        # 1. 查询 Session + Highlights + Notes + Conversations
        # 2. 生成 Markdown
        # 3. 调用 VaultService 写入
        # 4. 触发索引更新
```

**Connector 类型**：
- 在 LocalBook 中新增 `connector_type = reader`
- Metadata 包含：device, application, url, session, reading_time

**验收标准**：
- Session 成功转化为 Markdown 文档
- 文档自动触发 FTS5 和向量索引
- Reading 目录按年/月组织

---

### Phase 2: ReadFlow macOS 客户端基础（3-4周）

**目标**：实现 MVP 核心功能

#### P2.1 项目初始化（2天）

**工程结构**：

```
ReadFlow/
├── ReadFlow.xcodeproj
├── ReadFlow/
│   ├── App/
│   │   ├── ReadFlowApp.swift
│   │   └── AppState.swift
│   ├── Capture/
│   │   ├── SelectionService.swift
│   │   └── AccessibilityCapture.swift
│   ├── Browser/
│   │   ├── SafariBridge.swift
│   │   └── BrowserMessage.swift
│   ├── Storage/
│   │   ├── Database.swift (GRDB.swift)
│   │   ├── Models/
│   │   │   ├── ReaderItem.swift
│   │   │   ├── Source.swift
│   │   │   ├── Highlight.swift
│   │   │   └── Note.swift
│   │   └── Repositories/
│   ├── Sync/
│   │   ├── SyncEngine.swift
│   │   ├── Outbox.swift
│   │   └── ConflictResolver.swift
│   ├── Network/
│   │   ├── LocalBookClient.swift
│   │   ├── BonjourDiscovery.swift
│   │   └── KeychainService.swift
│   ├── UI/
│   │   ├── FloatingToolbar/
│   │   ├── AIPopover/
│   │   ├── SearchPanel/
│   │   └── MenuBar/
│   └── Resources/
└── ReadFlowExtension/ (Safari Extension)
```

**依赖**：
- GRDB.swift（SQLite）
- Alamofire（HTTP）
- SwiftUI + AppKit

**验收标准**：
- Xcode 项目创建完成
- 基础 SwiftUI App 可运行
- 依赖管理配置完成（SPM）

---

#### P2.2 本地存储实现（4天）

**SQLite 数据库**：

```swift
// Storage/Database.swift

class ReaderDatabase {
    private let dbQueue: DatabaseQueue

    // 表结构镜像服务端设计
    struct ReaderItem: Codable, FetchableRecord, PersistableRecord {
        var id: UUID
        var type: ItemType  // source/highlight/note/question/answer
        var sourceID: UUID?
        var sessionID: UUID?
        var parentID: UUID?
        var content: String
        var metadata: Data  // JSON
        var createdAt: Date
        var updatedAt: Date
        var syncState: SyncState  // local_only/pending/syncing/synced/conflict

        enum ItemType: String, Codable {
            case source, highlight, note, question, answer
        }

        enum SyncState: String, Codable {
            case local_only, pending, syncing, synced, conflict, failed
        }
    }

    // Outbox 表
    struct SyncOutbox: Codable, FetchableRecord, PersistableRecord {
        var id: Int64?
        var entityType: String
        var entityID: UUID
        var operation: String  // create/update/delete
        var payload: Data  // JSON
        var createdAt: Date
        var retryCount: Int
    }

    func saveHighlight(_ highlight: Highlight) throws {
        try dbQueue.write { db in
            // 同一个事务写入 Highlight + Outbox
            try highlight.save(db)
            try SyncOutbox(
                entityType: "highlight",
                entityID: highlight.id,
                operation: "create",
                payload: try JSONEncoder().encode(highlight),
                createdAt: Date(),
                retryCount: 0
            ).insert(db)
        }
    }
}
```

**本地搜索**：
- 使用 SQLite FTS5
- 索引：highlight.selected_text, note.content, query, answer

**验收标准**：
- GRDB 集成完成
- 增删改查单元测试通过
- Outbox Pattern 事务性验证

---

#### P2.3 划词捕获（5天）

**Accessibility API 捕获**：

```swift
// Capture/AccessibilityCapture.swift

class AccessibilityCapture: ObservableObject {
    func captureSelection() -> CapturedSelection? {
        // 1. 获取焦点应用
        let app = NSWorkspace.shared.frontmostApplication

        // 2. AXUIElement 获取选中文本
        let element = AXUIElementCreateApplication(app.processIdentifier)
        var selectedText: AnyObject?
        AXUIElementCopyAttributeValue(element, kAXSelectedTextAttribute, &selectedText)

        guard let text = selectedText as? String else { return nil }

        // 3. 获取上下文（前后各 200 字符）
        let context = captureContext(element: element)

        // 4. 获取来源信息
        let source = resolveSource(app: app)

        return CapturedSelection(
            text: text,
            contextBefore: context.before,
            contextAfter: context.after,
            source: source
        )
    }

    private func resolveSource(app: NSRunningApplication) -> Source {
        // Safari/Chrome: 通过 Extension 获取 URL
        // PDF: 文件路径 + 页码
        // 其他: app bundle ID + window title
    }
}
```

**Safari Extension**：

```swift
// ReadFlowExtension/ContentScript.swift

// 监听 selection 事件
document.addEventListener('mouseup', () => {
    const selection = window.getSelection();
    if (selection.toString().length > 0) {
        browser.runtime.sendMessage({
            type: 'selection',
            text: selection.toString(),
            url: window.location.href,
            title: document.title,
            contextBefore: getContextBefore(selection),
            contextAfter: getContextAfter(selection)
        });
    }
});
```

**验收标准**：
- 可在 Preview.app 中捕获 PDF 选中文本
- Safari Extension 可获取网页选中文本 + URL
- 其他 App（VS Code, Pages）可通过 Accessibility 捕获

---

#### P2.4 Floating Toolbar UI（4天）

**划词工具栏**：

```swift
// UI/FloatingToolbar/FloatingToolbar.swift

class FloatingToolbar: NSPanel {
    private let stackView: NSStackView

    init() {
        super.init(
            contentRect: NSRect(x: 0, y: 0, width: 300, height: 50),
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )

        level = .floating
        isOpaque = false
        backgroundColor = .clear

        stackView = NSStackView()
        stackView.distribution = .fillEqually
        stackView.spacing = 8

        // 按钮：保存 | AI | 搜索 | 笔记
        addButton(icon: "star", action: #selector(saveHighlight))
        addButton(icon: "brain", action: #selector(askAI))
        addButton(icon: "magnifyingglass", action: #selector(search))
        addButton(icon: "note.text", action: #selector(addNote))
    }

    func show(at point: NSPoint, for selection: CapturedSelection) {
        self.selection = selection
        setFrameOrigin(point)
        orderFrontRegardless()
    }

    @objc func saveHighlight() {
        // 本地立即写 SQLite (<10ms)
        try? database.saveHighlight(Highlight(
            id: UUID(),
            sourceID: selection.source.id,
            selectedText: selection.text,
            contextBefore: selection.contextBefore,
            contextAfter: selection.contextAfter,
            createdAt: Date()
        ))

        // UI 反馈
        showCheckmark()

        // 后台同步
        SyncEngine.shared.triggerSync()
    }
}
```

**验收标准**：
- 划词后工具栏正确显示在选中文字附近
- 点击「保存」后 <10ms 写入本地
- UI 显示「✓ 已保存」反馈

---

#### P2.5 同步引擎（5天）

**SyncEngine 实现**：

```swift
// Sync/SyncEngine.swift

class SyncEngine: ObservableObject {
    @Published var syncState: SyncState = .offline

    private let database: ReaderDatabase
    private let client: LocalBookClient
    private var batchWindow: DispatchWorkItem?

    enum SyncState {
        case offline, syncing, synced, error(String)
    }

    func triggerSync() {
        // 聚合 2-5 秒内的操作
        batchWindow?.cancel()
        batchWindow = DispatchWorkItem { [weak self] in
            Task {
                await self?.performSync()
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: batchWindow!)
    }

    private func performSync() async {
        guard let localBookURL = UserDefaults.standard.localBookURL else {
            syncState = .offline
            return
        }

        syncState = .syncing

        do {
            // 1. 读取 Outbox（最多 100 条）
            let items = try database.fetchPendingOutbox(limit: 100)

            // 2. 批量推送
            let result = try await client.pushSync(items: items)

            // 3. 更新同步状态
            try database.markSynced(ids: result.syncedIDs)

            // 4. 拉取服务端变更
            let cursor = UserDefaults.standard.lastSyncCursor
            let changes = try await client.pullSync(cursor: cursor)

            // 5. 应用变更
            try database.applyChanges(changes)
            UserDefaults.standard.lastSyncCursor = changes.nextCursor

            syncState = .synced
        } catch {
            syncState = .error(error.localizedDescription)
        }
    }
}
```

**Bonjour 发现**：

```swift
// Network/BonjourDiscovery.swift

class BonjourDiscovery: NSObject, NetServiceBrowserDelegate {
    private let browser = NetServiceBrowser()
    @Published var discoveredServices: [LocalBookService] = []

    func startDiscovery() {
        browser.delegate = self
        browser.searchForServices(ofType: "_localbook._tcp.", inDomain: "local.")
    }

    func netServiceBrowser(_ browser: NetServiceBrowser, didFind service: NetService, moreComing: Bool) {
        service.resolve(withTimeout: 5.0)
        // 解析出 IP + Port
        let localBookService = LocalBookService(
            name: service.name,
            host: service.hostName ?? "unknown",
            port: service.port
        )
        discoveredServices.append(localBookService)
    }
}
```

**验收标准**：
- 可自动发现局域网内的 LocalBook Service
- 批量同步 100 条操作成功
- 离线时数据保留在本地，上线后自动同步

---

#### P2.6 AI Popover（3天）

**AI 弹窗**：

```swift
// UI/AIPopover/AIPopover.swift

class AIPopover: NSPopover {
    private let conversationView: ConversationView
    private let scopeSelector: SegmentedControl

    enum AIScope: String, CaseIterable {
        case current = "当前内容"
        case localbook = "我的知识库"
        case web = "联网"
    }

    func show(for highlight: Highlight, at point: NSPoint) {
        Task {
            let answer = try await askAI(
                highlight: highlight,
                scope: scopeSelector.selected
            )

            conversationView.addMessage(role: .assistant, content: answer.text)
            conversationView.setCitations(answer.citations)
        }
    }

    private func askAI(highlight: Highlight, scope: AIScope) async throws -> AIAnswer {
        return try await LocalBookClient.shared.ask(
            conversationID: conversation.id,
            sourceID: highlight.sourceID,
            highlightID: highlight.id,
            query: "解释这部分内容",
            context: highlight.selectedText,
            scope: scope.rawValue
        )
    }
}
```

**验收标准**：
- 点击 AI 按钮后显示 Popover
- 三种 scope 可切换
- 支持连续追问

---

### Phase 3: MVP 集成与测试（1周）

#### P3.1 端到端测试

**测试场景**：

1. **离线阅读 → 同步**：
   - 断开网络
   - Safari 划词保存 3 条 Highlight
   - 添加 2 条 Note
   - 连接网络
   - 验证自动同步成功
   - 验证 LocalBook 生成 Markdown 文档

2. **AI 查询**：
   - 划词高亮
   - 点击 AI 解释
   - 验证返回答案
   - 追问 2 次
   - 验证对话历史保存

3. **多设备同步**：
   - MacBook 和 iMac 都安装 ReadFlow
   - MacBook 保存 Highlight
   - 验证 iMac 可拉取到

**验收标准**：
- 所有场景通过
- 无数据丢失
- 冲突正确处理

---

#### P3.2 性能验证

**指标**：

- 划词保存响应时间：< 10ms
- 本地搜索响应时间：< 100ms
- 空闲 CPU 占用：< 0.2%
- 内存占用：60-100 MB
- 批量同步 100 条：< 2s

**验收标准**：
- 所有指标达标

---

## 里程碑总结

| 阶段 | 时间 | 交付物 | 依赖 |
|------|------|--------|------|
| P1.1 数据模型 | 3天 | SQLite schema + migration | LocalBook V1.3.0 |
| P1.2 Reader API | 5天 | 8个端点实现 + 测试 | P1.1 |
| P1.3 Bonjour | 2天 | 服务发现 | P1.2 |
| P1.4 Connector | 3天 | Session → Markdown | P1.2 |
| P2.1 项目初始化 | 2天 | Xcode 项目 | - |
| P2.2 本地存储 | 4天 | GRDB + Outbox | P2.1 |
| P2.3 划词捕获 | 5天 | Accessibility + Safari Extension | P2.2 |
| P2.4 Toolbar UI | 4天 | Floating Toolbar | P2.3 |
| P2.5 同步引擎 | 5天 | SyncEngine + Bonjour | P2.2, P1.3 |
| P2.6 AI Popover | 3天 | AI 对话界面 | P2.5, P1.2 |
| P3.1 集成测试 | 4天 | 端到端场景验证 | 所有 P2 |
| P3.2 性能验证 | 2天 | 性能指标达标 | P3.1 |

**总计**：8-9 周（约 2 个月）

---

## 技术风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Accessibility API 权限被拒 | 无法捕获选中文本 | 提供详细的权限申请引导；Safari Extension 降级 |
| UUIDv7 冲突 | 数据同步失败 | 使用标准库实现；添加冲突检测 |
| SQLite 并发写入 | 数据损坏 | GRDB 自动处理；使用 WAL 模式 |
| 网络不稳定 | 同步失败 | Outbox 重试机制；指数退避 |
| macOS 沙盒限制 | 无法访问其他 App | 申请必要的 entitlements |

---

## 不在 MVP 范围内

以下功能推迟到 V2：

- Chrome Extension
- PDF 内嵌注释
- Reading Timeline 可视化
- Knowledge Graph 集成
- Cross Device Reading State
- Reading Analytics
- Agent 自动整理

---

## 成功标准

MVP 成功标准：

1. ✅ 可在 Safari 和 Preview 中划词保存
2. ✅ 离线时正常工作，上线后自动同步
3. ✅ AI 解释返回时间 < 3s
4. ✅ 本地搜索 < 100ms
5. ✅ 数据零丢失（所有操作写入 Outbox）
6. ✅ 空闲资源占用 < 0.2% CPU, < 100MB RAM
7. ✅ 局域网自动发现 LocalBook Service
8. ✅ 阅读数据自动生成 Markdown 文档

---

## 后续路线图（V2, V3）

### V2（3-4 个月后）

- Chrome Extension
- PDF 阅读器集成（页码、批注）
- Reading Session UI
- AI Web Search
- Related Notes 推荐
- Full Reading History
- Conflict Resolution UI

### V3（6-8 个月后）

- Research Timeline
- Knowledge Graph Visualization
- Auto Wikilink Suggestion
- Cross Device Reading State
- Reading Analytics Dashboard
- Agent 自动整理
- iOS/iPadOS 客户端

---

## 开发顺序建议

按依赖关系，推荐顺序：

1. **Week 1-2**: P1.1 → P1.2（服务端基础）
2. **Week 2**: P1.3 → P1.4（服务发现与集成）
3. **Week 3**: P2.1 → P2.2（客户端存储）
4. **Week 4-5**: P2.3 → P2.4（捕获与 UI）
5. **Week 5-6**: P2.5 → P2.6（同步与 AI）
6. **Week 7-8**: P3.1 → P3.2（测试与优化）

---

## 关键决策记录

1. **为什么使用 UUIDv7？**
   - 保证离线创建时的全局唯一性
   - 时间有序，便于按时间查询
   - 避免服务端自增 ID 冲突

2. **为什么 Embedding 不在 Reader 端？**
   - 避免多设备重复计算
   - Reader 保持轻量
   - 统一由 LocalBook 处理

3. **为什么使用 Outbox Pattern？**
   - 保证事务性（写入数据 + 写入同步队列原子性）
   - 崩溃恢复时不丢失同步任务
   - 支持批量聚合

4. **为什么按 Session 生成 Markdown？**
   - 避免 10 万个小文件
   - 保留阅读时间轴
   - 便于回顾阅读过程

---

## 附录：目录结构对比

### LocalBook (现有)
```
LocalBook/
├── server/          # Python FastAPI
├── apps/web/        # React 前端
└── packages/        # 共享包
```

### ReadFlow (新增)
```
ReadFlow/
├── ReadFlow.xcodeproj
├── ReadFlow/               # macOS App
│   ├── App/
│   ├── Capture/
│   ├── Storage/
│   ├── Sync/
│   ├── Network/
│   └── UI/
└── ReadFlowExtension/      # Safari Extension
```

### LocalBook Server (扩展)
```
server/
├── reader/          # 新增 Reader API
│   ├── router.py
│   ├── service.py
│   ├── models.py
│   ├── sync.py
│   └── importer.py
├── discovery/       # 新增 Bonjour
│   └── bonjour.py
└── ...             # 现有模块不变
```
