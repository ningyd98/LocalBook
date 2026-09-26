# ReadFlow 本地存储层实现文档

## 概述
本文档描述 ReadFlow macOS 客户端的 SQLite 本地存储层实现，基于 GRDB.swift 框架。

## 架构设计

### 数据模型

#### 1. Source（来源）
```swift
struct Source: Identifiable, Codable, FetchableRecord, PersistableRecord {
    var id: UUID              // UUIDv7 主键
    let type: SourceType      // web/book/pdf/video
    let url: String           // 原始 URL
    var title: String?        // 标题
    var author: String?       // 作者
    var metadata: [String: String]?  // 自定义元数据
    let createdAt: Date       // 创建时间
    var updatedAt: Date       // 更新时间
    var syncState: String     // pending/syncing/synced/conflict
}
```

#### 2. Highlight（划词高亮）
```swift
struct Highlight: Identifiable, Codable, FetchableRecord, PersistableRecord {
    var id: UUID              // UUIDv7 主键
    let sourceID: UUID        // 外键 -> Source
    let selectedText: String  // 划选文本（支持 FTS5）
    var context: String?      // 上下文
    var color: String?        // 高亮颜色
    var startOffset: Int?     // 起始偏移
    var endOffset: Int?       // 结束偏移
    let createdAt: Date
    var updatedAt: Date
    var syncState: String
}
```

#### 3. Note（笔记）
```swift
struct Note: Identifiable, Codable, FetchableRecord, PersistableRecord {
    var id: UUID              // UUIDv7 主键
    let sourceID: UUID        // 外键 -> Source
    var content: String       // 笔记内容（支持 FTS5）
    var tags: [String]?       // 标签
    var linkedHighlightID: UUID?  // 关联的 Highlight
    let createdAt: Date
    var updatedAt: Date
    var syncState: String
}
```

#### 4. ReaderItem（统一阅读条目）
```swift
struct ReaderItem: Identifiable, Codable, FetchableRecord, PersistableRecord {
    var id: UUID              // UUIDv7 主键
    let type: ItemType        // source/highlight/note/question/answer
    let sourceID: UUID        // 外键 -> Source
    var content: String       // 内容（支持 FTS5）
    var metadata: [String: String]?
    let createdAt: Date
    var updatedAt: Date
    var syncState: String
}
```

#### 5. SyncOutbox（同步出站队列）
```swift
struct SyncOutbox: Identifiable, Codable, FetchableRecord, PersistableRecord {
    var id: Int64?            // 自增主键
    let entityType: String    // source/highlight/note/reader_item
    let entityID: UUID        // 实体 ID
    let operation: String     // create/update/delete
    let payload: Data         // JSON 负载
    let createdAt: Date
    var retryCount: Int       // 重试次数
    var lastAttemptAt: Date?  // 最后尝试时间
    var lastError: String?    // 最后错误
}
```

### 数据库模式

#### 表结构
```sql
-- 来源表
CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    author TEXT,
    metadata TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    sync_state TEXT DEFAULT 'pending'
);

-- 划词表
CREATE TABLE highlights (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    selected_text TEXT NOT NULL,
    context TEXT,
    color TEXT,
    start_offset INTEGER,
    end_offset INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    sync_state TEXT DEFAULT 'pending'
);

-- 笔记表
CREATE TABLE notes (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    tags TEXT,
    linked_highlight_id TEXT REFERENCES highlights(id) ON DELETE SET NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    sync_state TEXT DEFAULT 'pending'
);

-- 阅读条目表
CREATE TABLE reader_items (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    metadata TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    sync_state TEXT DEFAULT 'pending'
);

-- 同步出站队列
CREATE TABLE sync_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    payload BLOB NOT NULL,
    created_at REAL NOT NULL,
    retry_count INTEGER DEFAULT 0,
    last_attempt_at REAL,
    last_error TEXT
);

-- FTS5 全文索引
CREATE VIRTUAL TABLE highlights_fts USING fts5(
    id UNINDEXED,
    selected_text,
    content='highlights',
    content_rowid='rowid'
);

CREATE VIRTUAL TABLE notes_fts USING fts5(
    id UNINDEXED,
    content,
    content='notes',
    content_rowid='rowid'
);

CREATE VIRTUAL TABLE reader_items_fts USING fts5(
    id UNINDEXED,
    content,
    content='reader_items',
    content_rowid='rowid'
);
```

#### 索引
```sql
CREATE INDEX idx_highlights_source ON highlights(source_id);
CREATE INDEX idx_notes_source ON notes(source_id);
CREATE INDEX idx_reader_items_source ON reader_items(source_id);
CREATE INDEX idx_sync_outbox_created ON sync_outbox(created_at);
```

## 核心功能实现

### 1. ReaderDatabase 类

主数据库管理类，提供所有 CRUD 操作：

```swift
class ReaderDatabase {
    private let dbQueue: DatabaseQueue

    init(path: String) throws {
        dbQueue = try DatabaseQueue(path: path)
        try migrator.migrate(dbQueue)
    }

    // CRUD 操作
    func createSource(_ source: Source) throws
    func updateSource(_ source: Source) throws
    func deleteSource(id: UUID) throws
    func getSource(id: UUID) throws -> Source?
    func listSources(limit: Int) throws -> [Source]

    // Highlight, Note, ReaderItem 同样实现

    // 全文搜索
    func searchHighlights(query: String, limit: Int) throws -> [Highlight]
    func searchNotes(query: String, limit: Int) throws -> [Note]
    func searchReaderItems(query: String, type: ItemType?, limit: Int) throws -> [ReaderItem]

    // Outbox 管理
    func getPendingOutboxItems(limit: Int) throws -> [SyncOutbox]
    func markOutboxItemSynced(id: Int64) throws
    func markOutboxItemFailed(id: Int64, error: String) throws
}
```

### 2. Outbox Pattern 实现

每次写操作（创建、更新、删除）自动在事务中写入 `sync_outbox` 表：

```swift
func createSource(_ source: Source) throws {
    try dbQueue.write { db in
        try source.insert(db)

        // 编码为 JSON
        let encoder = JSONEncoder()
        let payload = try encoder.encode(source)

        // 写入 Outbox
        let outboxEntry = SyncOutbox(
            entityType: "source",
            entityID: source.id,
            operation: "create",
            payload: payload
        )
        try outboxEntry.insert(db)
    }
}
```

优势：
- **事务性保证**：业务数据和同步队列原子写入
- **离线优先**：网络断开时继续本地操作
- **有序同步**：按 `created_at` 顺序同步
- **失败重试**：记录 `retry_count` 和 `last_error`

### 3. FTS5 全文搜索

使用 SQLite FTS5 模块实现高性能全文检索：

```swift
func searchHighlights(query: String, limit: Int = 50) throws -> [Highlight] {
    try dbQueue.read { db in
        let pattern = FTS5Pattern(matchingAllTokensIn: query)
        let sql = """
            SELECT h.* FROM highlights h
            INNER JOIN highlights_fts fts ON h.rowid = fts.rowid
            WHERE highlights_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """
        return try Highlight.fetchAll(db, sql: sql, arguments: [pattern, limit])
    }
}
```

特性：
- **分词搜索**：支持中英文分词
- **相关性排序**：按 `rank` 排序
- **前缀匹配**：`swift*` 匹配 `swift`, `swiftui` 等
- **布尔查询**：`AND`, `OR`, `NOT` 操作符

### 4. 数据库迁移

使用 GRDB 的 `DatabaseMigrator` 管理版本：

```swift
private var migrator: DatabaseMigrator {
    var migrator = DatabaseMigrator()

    migrator.registerMigration("v1") { db in
        // 创建表
        try db.create(table: "sources") { t in
            t.column("id", .text).primaryKey()
            t.column("type", .text).notNull()
            // ...
        }

        // 创建索引
        try db.create(index: "idx_highlights_source",
                      on: "highlights", columns: ["source_id"])

        // 创建 FTS5 表
        try db.create(virtualTable: "highlights_fts", using: FTS5()) { t in
            t.column("selected_text")
        }
    }

    return migrator
}
```

## 单元测试

### 测试覆盖

`ReaderDatabaseTests.swift` 提供完整的单元测试：

1. **CRUD 测试**（15 个测试）
   - Source: create, update, delete, get
   - Highlight: create, update, delete, get
   - Note: create, update, delete, get
   - ReaderItem: create, list, listByType

2. **Outbox Pattern 测试**（5 个测试）
   - 创建时生成 Outbox 条目
   - 更新时生成 Outbox 条目
   - 删除时生成 Outbox 条目
   - 标记同步成功
   - 标记同步失败（重试计数）

3. **全文搜索测试**（4 个测试）
   - 搜索 Highlights
   - 搜索 Notes
   - 搜索 ReaderItems
   - 按类型搜索 ReaderItems

### 运行测试

```bash
swift test
# 或
xcodebuild test -scheme ReadFlow
```

## 性能优化

### 1. 索引策略
- 外键字段索引：`source_id`
- 时间字段索引：`created_at`, `updated_at`
- FTS5 全文索引：高频搜索字段

### 2. 批量操作
```swift
func batchCreateHighlights(_ highlights: [Highlight]) throws {
    try dbQueue.write { db in
        for highlight in highlights {
            try highlight.insert(db)
        }
    }
}
```

### 3. 读写分离
- `dbQueue.read { }`: 并发读
- `dbQueue.write { }`: 串行写

### 4. 连接池
GRDB 自动管理连接池，默认配置：
- WAL 模式：支持并发读写
- PRAGMA synchronous = NORMAL
- PRAGMA journal_mode = WAL

## 安全性

### 1. SQL 注入防护
使用参数化查询：
```swift
try Source.fetchOne(db, key: sourceID)  // ✅ 安全
// 不直接拼接 SQL
```

### 2. 事务隔离
```swift
try dbQueue.write { db in
    // 原子操作
    try source.insert(db)
    try outbox.insert(db)
}
```

### 3. 数据校验
模型层实现 `Codable` 自动校验类型。

## 下一步集成

### 与 SyncEngine 集成

```swift
class SyncEngine {
    private let database: ReaderDatabase
    private let client: LocalBookClient

    func syncPendingItems() async throws {
        let items = try database.getPendingOutboxItems(limit: 100)

        for item in items {
            do {
                // 上传到服务端
                try await client.syncItem(item)

                // 标记成功
                try database.markOutboxItemSynced(id: item.id!)
            } catch {
                // 标记失败
                try database.markOutboxItemFailed(
                    id: item.id!,
                    error: error.localizedDescription
                )
            }
        }
    }
}
```

### 与 UI 集成

```swift
class ContentViewModel: ObservableObject {
    private let database: ReaderDatabase

    @Published var highlights: [Highlight] = []

    func loadHighlights() async {
        do {
            highlights = try database.listHighlights(limit: 50)
        } catch {
            print("Load error: \(error)")
        }
    }

    func search(query: String) async {
        do {
            highlights = try database.searchHighlights(query: query)
        } catch {
            print("Search error: \(error)")
        }
    }
}
```

## 故障排查

### 常见问题

1. **数据库锁定**
   - 检查是否有长时间持有的事务
   - 使用 WAL 模式提高并发性

2. **FTS5 不工作**
   - 确认 SQLite 编译时启用 FTS5
   - 检查 triggers 是否正确同步内容

3. **迁移失败**
   - 查看迁移日志
   - 手动检查表结构

### 调试工具

```swift
// 启用 SQL 日志
var config = Configuration()
config.prepareDatabase { db in
    db.trace { print("SQL: \($0)") }
}
```

## 总结

本实现完成了 ReadFlow 本地存储层的核心功能：

✅ GRDB.swift 集成
✅ 5 个数据模型（Source, Highlight, Note, ReaderItem, SyncOutbox）
✅ 完整 CRUD 操作
✅ Outbox Pattern 事务性保证
✅ SQLite FTS5 全文搜索
✅ 24 个单元测试覆盖
✅ 数据库迁移机制
✅ 性能优化（索引、批量操作、读写分离）

下一阶段（P2.3）将实现划词捕获服务，基于本存储层持久化采集数据。
