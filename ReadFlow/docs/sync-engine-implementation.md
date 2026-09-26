# Sync Engine Implementation

## 概述

ReadFlow 同步引擎实现了完整的离线优先（Offline-First）同步机制，确保数据在本地和 LocalBook 服务端之间可靠同步。

## 架构

```
┌─────────────────┐
│  FloatingToolbar │
│  SelectionService│
└────────┬────────┘
         │ triggerSync()
         ▼
┌─────────────────┐      ┌──────────────┐
│   SyncEngine    │◄────►│ReaderDatabase│
└────────┬────────┘      └──────────────┘
         │
         │ pushSync / pullSync
         ▼
┌─────────────────┐      ┌──────────────┐
│ LocalBookClient │◄────►│BonjourDiscovery│
└────────┬────────┘      └──────────────┘
         │
         │ HTTP/JSON
         ▼
┌─────────────────┐
│ LocalBook Server│
└─────────────────┘
```

## 核心组件

### 1. BonjourDiscovery

**功能**：使用 mDNS/Bonjour 协议在局域网内自动发现 LocalBook 服务。

**实现要点**：

```swift
// 使用 Network.framework 的 NWBrowser
let browser = NWBrowser(for: .bonjour(type: "_localbook._tcp", domain: nil), using: parameters)

// 服务解析：NWEndpoint.service → IP:Port
let connection = NWConnection(to: result.endpoint, using: .tcp)
connection.stateUpdateHandler = { state in
    if case .ready = state,
       let endpoint = connection.currentPath?.remoteEndpoint,
       case .hostPort(let host, let port) = endpoint {
        // 构建 URL: http://host:port
    }
}
```

**API**：

- `startDiscovery()` - 开始浏览 `_localbook._tcp` 服务
- `stopDiscovery()` - 停止浏览并清理资源
- `discoveredServices: [LocalBookService]` - 已发现的服务列表（@Published）

**数据模型**：

```swift
struct LocalBookService {
    let id: UUID
    let name: String              // 服务名称
    let host: String              // IP 地址或主机名
    let port: Int                 // 端口号
    var resolvedURL: URL?         // http://host:port
}
```

---

### 2. LocalBookClient

**功能**：HTTP 客户端，封装所有与 LocalBook Server 的 REST API 交互。

**技术栈**：

- Alamofire - HTTP 网络库
- KeychainService - 安全存储 access_token
- UserDefaults - 存储 baseURL

**配置**：

```swift
// 初始化配置
LocalBookClient.shared.configure(
    baseURL: URL(string: "http://192.168.1.100:8000")!,
    accessToken: "reader_token_xxx"
)
```

**API 端点**：

#### 设备配对

```swift
// POST /api/v1/reader/pair
func pair(deviceID: UUID, token: String) async throws -> PairResponse
// 返回: { access_token, device_id }

// POST /api/v1/reader/register
func register(deviceID: UUID, deviceName: String, pairingToken: String) async throws -> RegisterResponse
// 返回: { device_id, device_name }
```

#### 同步

```swift
// POST /api/v1/reader/sync/push
func pushSync(items: [SyncOutboxItem]) async throws -> PushSyncResponse
// 请求体: { operations: [{ entity_type, entity_id, operation, payload, timestamp }] }
// 返回: { synced_ids: [String], conflicts: [{ entity_id, reason }] }

// GET /api/v1/reader/sync/pull?cursor=xxx
func pullSync(cursor: String?) async throws -> PullSyncResponse
// 返回: { operations: [...], next_cursor: String? }
```

#### 设备状态

```swift
// GET /api/v1/reader/status
func getStatus() async throws -> DeviceStatusResponse
// 返回: { device_id, device_name, last_sync_at, pending_ops }
```

#### AI 功能（Phase 2.6）

```swift
// POST /api/v1/reader/ask
func ask(...) async throws -> AIAnswer

// POST /api/v1/reader/search
func search(query: String, limit: Int) async throws -> SearchResponse
```

**错误处理**：

```swift
enum NetworkError: Error {
    case invalidURL          // 未配置 baseURL
    case unauthorized        // 401 未授权
    case notFound            // 404 资源未找到
    case serverError(String) // 5xx 服务器错误
    case networkUnavailable  // 网络连接失败
}
```

**认证机制**：

所有需要认证的请求都会自动添加 Authorization header：

```
Authorization: Bearer reader_token_{device_id}_{random}
```

---

### 3. SyncEngine

**功能**：同步引擎核心，协调本地数据库和远程服务器的数据同步。

**同步策略**：

1. **离线优先（Offline-First）**：
   - 所有操作先写入本地数据库
   - 后台异步同步到服务器
   - 离线时数据保留在 Outbox

2. **批量聚合（Batch Aggregation）**：
   - 触发同步后等待 3 秒
   - 聚合期间的所有操作合并为一批
   - 减少网络请求次数

3. **冲突解决（Conflict Resolution）**：
   - 服务器优先（Server Wins）
   - 冲突的本地操作标记为 failed
   - 用户可在 UI 中查看并手动处理

**状态机**：

```
┌────────┐
│ offline│────┐
└────────┘    │ configure()
              ▼
          ┌────────┐
          │ synced │◄───┐
          └────┬───┘    │
               │        │ success
    triggerSync()       │
               │        │
               ▼        │
          ┌────────┐    │
          │syncing │────┘
          └────┬───┘
               │
               │ error
               ▼
          ┌────────┐
          │ error  │
          └────────┘
```

**API**：

```swift
// 配置客户端（设置 baseURL 和 access_token）
func configure(baseURL: URL, accessToken: String)

// 触发延迟同步（3秒批量窗口）
func triggerSync()

// 立即执行同步
func immediateSync() async

// 状态（@Published）
var syncState: SyncState { get }
var lastSyncTime: Date? { get }
var pendingCount: Int { get }
```

**同步流程**：

#### Push 阶段

```swift
1. 从 sync_outbox 读取最多 100 条待同步记录
2. 转换为 API 格式：
   {
     "operations": [
       {
         "entity_type": "highlight",
         "entity_id": "uuid",
         "operation": "create",
         "payload": "base64(json)",
         "timestamp": "2024-01-01T12:00:00Z"
       }
     ]
   }
3. POST /api/v1/reader/sync/push
4. 服务器返回: { synced_ids: [...], conflicts: [...] }
5. 标记成功的记录：从 outbox 删除
6. 标记冲突的记录：retryCount++, lastError
```

#### Pull 阶段

```swift
1. 从 UserDefaults 读取 last_sync_cursor
2. GET /api/v1/reader/sync/pull?cursor={cursor}
3. 服务器返回: { operations: [...], next_cursor: "xxx" }
4. 遍历 operations 并应用：
   - 解码 base64 payload → JSON → Model
   - 根据 operation (create/update/delete) 更新本地数据库
   - 使用 upsert* 方法（不触发 Outbox）
5. 更新 last_sync_cursor 到 UserDefaults
6. 如果有 next_cursor，重复步骤 2-5（最多 1000 条）
```

**应用服务器操作**：

```swift
private func applyServerOperations(_ operations: [SyncOperation]) throws {
    for operation in operations {
        // 1. Base64 解码 payload
        guard let data = Data(base64Encoded: operation.payload) else { continue }

        // 2. 根据 entity_type 分发
        switch operation.entityType {
        case "source":
            let source = try JSONDecoder().decode(Source.self, from: data)
            try database.upsertSource(source)  // 不触发 Outbox

        case "highlight":
            let highlight = try JSONDecoder().decode(Highlight.self, from: data)
            try database.upsertHighlight(highlight)

        case "note":
            let note = try JSONDecoder().decode(Note.self, from: data)
            try database.upsertNote(note)

        case "reader_item":
            let item = try JSONDecoder().decode(ReaderItem.self, from: data)
            try database.upsertReaderItem(item)
        }
    }
}
```

**性能优化**：

1. **批量窗口**：3秒内的多次 triggerSync() 只执行一次
2. **限流**：每次 push 最多 100 条，pull 最多 1000 条
3. **游标分页**：pull 使用 cursor 避免重复拉取
4. **异步非阻塞**：所有网络操作使用 async/await

---

## 数据流

### 保存 Highlight 的完整流程

```
1. 用户划词 → FloatingToolbar 点击「保存」

2. FloatingToolbar.saveHighlight()
   ├─► database.createHighlight(...)
   │   └─► 事务写入:
   │       ├─ INSERT INTO highlights
   │       └─ INSERT INTO sync_outbox (entity_type='highlight', operation='create')
   │
   └─► SyncEngine.shared.triggerSync()

3. SyncEngine (3秒后)
   ├─► performSync()
   │   ├─► pushChanges()
   │   │   ├─ 读取 sync_outbox (limit 100)
   │   │   ├─ POST /api/v1/reader/sync/push
   │   │   ├─ 服务器返回 synced_ids
   │   │   └─ DELETE FROM sync_outbox WHERE id IN (...)
   │   │
   │   └─► pullChanges()
   │       ├─ GET /api/v1/reader/sync/pull?cursor=xxx
   │       ├─ 服务器返回 operations + next_cursor
   │       ├─ 应用 operations (upsert 不触发 outbox)
   │       └─ 更新 last_sync_cursor
   │
   └─► 更新状态: .synced, lastSyncTime = Date()
```

---

## 使用示例

### 初始化和配置

```swift
// 1. 在 AppDelegate 或 App 启动时配置
class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        // 启动 Bonjour 发现
        BonjourDiscovery.shared.startDiscovery()

        // 监听发现的服务
        BonjourDiscovery.shared.$discoveredServices
            .sink { services in
                if let firstService = services.first,
                   let url = firstService.resolvedURL {
                    // 配置客户端
                    if let token = KeychainService.shared.getAccessToken() {
                        LocalBookClient.shared.configure(
                            baseURL: url,
                            accessToken: token
                        )

                        // 配置同步引擎
                        SyncEngine.shared.configure(
                            baseURL: url,
                            accessToken: token
                        )
                    }
                }
            }
            .store(in: &cancellables)
    }
}
```

### 设备配对流程

```swift
// 在 SettingsView 中
func pairDevice() async {
    do {
        // 1. 生成设备 ID
        let deviceID = KeychainService.shared.getOrCreateDeviceID()

        // 2. 用户输入服务器提供的 6 位数字 token
        let token = pairingTokenTextField.text

        // 3. 配置 baseURL（从 Bonjour 发现或手动输入）
        let baseURL = selectedService.resolvedURL
        LocalBookClient.shared.configure(baseURL: baseURL)

        // 4. 配对
        let response = try await LocalBookClient.shared.pair(
            deviceID: deviceID,
            token: token
        )

        // 5. 保存 access_token（已自动保存到 Keychain）
        // 6. 配置同步引擎
        SyncEngine.shared.configure(
            baseURL: baseURL,
            accessToken: response.accessToken
        )

        print("✅ 设备配对成功")
    } catch {
        print("❌ 配对失败: \(error)")
    }
}
```

### 监听同步状态

```swift
// 在 UI 中显示同步状态
struct SyncStatusView: View {
    @ObservedObject var syncEngine = SyncEngine.shared

    var body: some View {
        HStack {
            // 状态图标
            switch syncEngine.syncState {
            case .offline:
                Image(systemName: "wifi.slash")
                Text("离线")
            case .syncing:
                ProgressView()
                Text("同步中...")
            case .synced:
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
                Text("已同步")
            case .error(let message):
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundColor(.red)
                Text(message)
            }

            // 待同步数量
            if syncEngine.pendingCount > 0 {
                Text("(\(syncEngine.pendingCount) 待同步)")
                    .foregroundColor(.secondary)
            }

            // 最后同步时间
            if let lastSync = syncEngine.lastSyncTime {
                Text(lastSync, style: .relative)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }
}
```

### 手动触发同步

```swift
// 在 MenuBar 或 Settings 中
Button("立即同步") {
    Task {
        await SyncEngine.shared.immediateSync()
    }
}
```

---

## 错误处理

### 网络错误

```swift
do {
    await SyncEngine.shared.immediateSync()
} catch NetworkError.unauthorized {
    // Token 过期，需要重新配对
    showPairingAlert()
} catch NetworkError.networkUnavailable {
    // 网络不可用，数据已保存到本地
    showOfflineNotice()
} catch {
    // 其他错误
    showErrorAlert(error.localizedDescription)
}
```

### 同步冲突

```swift
// 服务器返回冲突时，SyncEngine 会：
// 1. 标记本地操作为 failed
// 2. 增加 retryCount
// 3. 记录 lastError

// 查看失败的同步操作
let failedItems = try database.getPendingSyncItems(limit: 100)
    .filter { $0.retryCount > 0 }

for item in failedItems {
    print("❌ \(item.entityType) \(item.entityID): \(item.lastError ?? "unknown")")
}
```

---

## 测试

### 单元测试

```swift
class SyncEngineTests: XCTestCase {
    var database: ReaderDatabase!
    var syncEngine: SyncEngine!

    override func setUp() {
        // 使用内存数据库
        database = try! ReaderDatabase(path: ":memory:")
        syncEngine = SyncEngine.shared
    }

    func testBatchAggregation() async {
        // 连续触发 3 次同步
        syncEngine.triggerSync()
        syncEngine.triggerSync()
        syncEngine.triggerSync()

        // 等待 4 秒
        try? await Task.sleep(nanoseconds: 4_000_000_000)

        // 验证只执行了一次同步
        // （可以通过 mock LocalBookClient 来验证）
    }

    func testOfflineMode() {
        // 未配置客户端时
        syncEngine.triggerSync()

        // 验证状态为 offline
        XCTAssertEqual(syncEngine.syncState, .offline)
    }
}
```

### 集成测试

需要运行本地 LocalBook Server：

```bash
# 启动服务器
cd server
python -m uvicorn main:app --reload

# 启动 Bonjour 广播
python server/discovery/manual_test_bonjour.py
```

然后在 macOS 应用中：

1. 自动发现服务（通过 Bonjour）
2. 生成配对 token（在服务器 UI）
3. 输入 token 完成配对
4. 创建 Highlight → 验证写入 outbox
5. 等待 3 秒 → 验证同步到服务器
6. 在另一台设备修改 → 验证 pull 到本地

---

## 性能指标

### 保存响应时间

- **本地写入**：< 10ms（GRDB 事务）
- **同步延迟**：3 秒（批量窗口）
- **网络往返**：< 100ms（局域网）

### 批量同步能力

- **Push**：最多 100 条/批次
- **Pull**：最多 1000 条/批次
- **聚合窗口**：3 秒

### 离线能力

- **Outbox 容量**：无限制（SQLite）
- **失败重试**：手动或下次同步时重试
- **冲突解决**：服务器优先

---

## 已知限制

1. **冲突解决策略**：
   - 当前仅支持「服务器优先」
   - 未来可扩展为 CRDTs 或 LWW（Last-Write-Wins）

2. **大文件同步**：
   - Payload 使用 base64 编码，约增加 33% 体积
   - 未来可改用二进制传输或压缩

3. **实时同步**：
   - 当前基于轮询（3秒聚合窗口）
   - 未来可改用 WebSocket 或 Server-Sent Events

4. **多设备协同**：
   - 当前每台设备独立同步
   - 未来可添加设备间直接同步（P2P）

---

## 下一步

Phase 2.6 将实现：

1. **AI Popover UI**：
   - 右键高亮文本 → 显示 AI 浮窗
   - 输入问题 → 调用 `/ask` API
   - 显示 AI 回答和引用

2. **语义搜索**：
   - 全局搜索框
   - 调用 `/search` API
   - 向量检索 + FTS5 混合搜索

3. **设置界面完善**：
   - 设备配对 UI
   - 同步状态监控
   - 手动同步按钮
   - 清除缓存/重置

---

## 文件清单

```
ReadFlow/
├── Network/
│   ├── BonjourDiscovery.swift      (208 行) - Bonjour 服务发现
│   ├── LocalBookClient.swift       (426 行) - HTTP 客户端
│   └── KeychainService.swift       ( 89 行) - Keychain 封装 [已存在]
├── Sync/
│   └── SyncEngine.swift            (358 行) - 同步引擎核心
└── docs/
    └── sync-engine-implementation.md - 本文档
```

**总代码量**：约 1000 行（不含文档）

---

## 参考

- [GRDB.swift](https://github.com/groue/GRDB.swift) - SQLite ORM
- [Alamofire](https://github.com/Alamofire/Alamofire) - HTTP 网络库
- [Network.framework](https://developer.apple.com/documentation/network) - Bonjour/mDNS
- [Outbox Pattern](https://microservices.io/patterns/data/transactional-outbox.html) - 事务性同步模式

---

**文档版本**：1.0
**最后更新**：2024-01-15
**作者**：ReadFlow macOS Developer
