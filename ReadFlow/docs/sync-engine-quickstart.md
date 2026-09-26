# Sync Engine Quick Start

快速开始使用 ReadFlow 同步引擎。

## 前置条件

1. **LocalBook Server** 已启动并运行在局域网内
2. **Bonjour 服务** 正在广播 `_localbook._tcp` 服务
3. **macOS 应用** 已安装并授予必要权限

---

## Step 1: 启动服务器

```bash
# 进入仓库根目录
cd /path/to/LocalBook

# 启动 FastAPI 服务器
uv run uvicorn server.api.main:app --host 0.0.0.0 --port 3780 --reload

# 启动 Bonjour 广播（另一个终端）
uv run python server/discovery/manual_test_bonjour.py
```

验证服务器是否正常运行：

```bash
# 检查 API
curl http://localhost:8000/api/v1/reader/capabilities

# 检查 Bonjour 广播（macOS）
dns-sd -B _localbook._tcp
```

---

## Step 2: 配置 macOS 应用

### 自动发现（推荐）

macOS 应用启动时会自动发现局域网内的 LocalBook 服务：

```swift
// 在 AppDelegate.swift 中已实现
BonjourDiscovery.shared.startDiscovery()

// 监听发现的服务
BonjourDiscovery.shared.$discoveredServices
    .sink { services in
        if let service = services.first {
            print("发现服务: \(service.displayAddress)")
            // 自动配置客户端...
        }
    }
```

### 手动配置

如果自动发现失败，可以手动配置：

```swift
// 1. 配置 baseURL
let baseURL = URL(string: "http://192.168.1.100:8000")!
LocalBookClient.shared.configure(baseURL: baseURL)

// 2. 获取或生成设备 ID
let deviceID = KeychainService.shared.getOrCreateDeviceID()
print("Device ID: \(deviceID)")
```

---

## Step 3: 设备配对

### 服务端生成 Token

1. 访问 LocalBook 管理界面（如有）
2. 或直接调用 API 生成 6 位数字 token：

```bash
curl -X POST http://localhost:8000/api/v1/reader/pair \
  -H "Content-Type: application/json" \
  -d '{
    "device_id": "your-device-uuid",
    "token": "123456"
  }'
```

### 客户端配对

```swift
Task {
    do {
        let deviceID = KeychainService.shared.getOrCreateDeviceID()
        let token = "123456"  // 用户输入的 token

        // 配对
        let response = try await LocalBookClient.shared.pair(
            deviceID: deviceID,
            token: token
        )

        // access_token 已自动保存到 Keychain
        print("✅ 配对成功: \(response.accessToken)")

        // 配置同步引擎
        SyncEngine.shared.configure(
            baseURL: baseURL,
            accessToken: response.accessToken
        )
    } catch {
        print("❌ 配对失败: \(error)")
    }
}
```

---

## Step 4: 测试同步

### 创建 Highlight

```swift
// 模拟划词捕获
let source = Source(
    id: UUID(),
    type: .webpage,
    title: "测试网页",
    url: "https://example.com",
    createdAt: Date()
)

let highlight = Highlight(
    id: UUID(),
    sourceID: source.id,
    selectedText: "这是一段测试高亮文本",
    contextBefore: "前文...",
    contextAfter: "后文...",
    createdAt: Date()
)

// 保存到本地（自动写入 outbox）
try database.createSource(source)
try database.createHighlight(highlight)

// 触发同步（3秒后执行）
SyncEngine.shared.triggerSync()

print("✅ Highlight 已保存，等待同步...")
```

### 监听同步状态

```swift
// 监听状态变化
SyncEngine.shared.$syncState
    .sink { state in
        switch state {
        case .offline:
            print("🔴 离线")
        case .syncing:
            print("🟡 同步中...")
        case .synced:
            print("🟢 同步完成")
        case .error(let message):
            print("🔴 同步失败: \(message)")
        }
    }
    .store(in: &cancellables)

// 监听待同步数量
SyncEngine.shared.$pendingCount
    .sink { count in
        print("📦 待同步: \(count) 条")
    }
    .store(in: &cancellables)
```

---

## Step 5: 验证同步结果

### 检查本地 Outbox

```swift
// 查看待同步的记录
let pendingItems = try database.getPendingSyncItems(limit: 100)
print("待同步: \(pendingItems.count) 条")

for item in pendingItems {
    print("- \(item.entityType) \(item.entityID) (\(item.operation))")
    if item.retryCount > 0 {
        print("  ⚠️ 重试次数: \(item.retryCount), 错误: \(item.lastError ?? "unknown")")
    }
}
```

### 检查服务端数据

```bash
# 查询服务端的 highlights
curl http://localhost:8000/api/v1/reader/highlights \
  -H "Authorization: Bearer reader_token_xxx"

# 查询同步状态
curl http://localhost:8000/api/v1/reader/status \
  -H "Authorization: Bearer reader_token_xxx"
```

---

## 常见问题

### Q1: Bonjour 发现不到服务

**原因**：
- 服务器未启动 Bonjour 广播
- 防火墙阻止了 mDNS 流量（UDP 5353）
- 不在同一局域网内

**解决方案**：
```bash
# 1. 检查 Bonjour 是否广播
dns-sd -B _localbook._tcp

# 2. 手动输入 IP:Port
LocalBookClient.shared.configure(
    baseURL: URL(string: "http://192.168.1.100:8000")!
)
```

### Q2: 配对失败（401 Unauthorized）

**原因**：
- Token 无效或已过期
- Device ID 不匹配

**解决方案**：
```swift
// 1. 重新生成 device ID
let deviceID = UUID()
KeychainService.shared.save(key: "device_id", value: deviceID.uuidString)

// 2. 服务端生成新 token
// 3. 重新配对
```

### Q3: 同步一直处于 syncing 状态

**原因**：
- 网络连接超时
- 服务端响应缓慢或宕机

**解决方案**：
```swift
// 1. 检查网络连接
Task {
    do {
        let status = try await LocalBookClient.shared.getStatus()
        print("✅ 服务器正常: \(status)")
    } catch {
        print("❌ 服务器异常: \(error)")
    }
}

// 2. 取消当前同步并重试
// （当前版本不支持，未来版本将添加）
```

### Q4: 同步冲突

**原因**：
- 多设备同时修改同一条记录
- 本地数据比服务端旧

**解决方案**：
```swift
// 当前策略：服务器优先
// 冲突的本地操作会被标记为 failed

// 查看冲突记录
let failedItems = try database.getPendingSyncItems(limit: 100)
    .filter { $0.retryCount > 0 }

// 手动处理：
// 1. 查看 lastError 了解冲突原因
// 2. 从服务端拉取最新数据
// 3. 删除本地失败的 outbox 记录
try database.markSyncItemSuccess(id: failedItem.id!)
```

---

## 性能测试

### 测试批量同步

```swift
// 创建 100 条 Highlights
for i in 0..<100 {
    let highlight = Highlight(
        id: UUID(),
        sourceID: sourceID,
        selectedText: "测试文本 \(i)",
        contextBefore: nil,
        contextAfter: nil,
        createdAt: Date()
    )
    try database.createHighlight(highlight)
}

// 触发同步
let startTime = Date()
await SyncEngine.shared.immediateSync()
let duration = Date().timeIntervalSince(startTime)

print("✅ 同步 100 条记录耗时: \(duration) 秒")
// 预期: < 1 秒（局域网）
```

### 测试批量窗口

```swift
// 连续触发 10 次同步
for i in 0..<10 {
    try database.createHighlight(/* ... */)
    SyncEngine.shared.triggerSync()
    try await Task.sleep(nanoseconds: 100_000_000) // 0.1 秒
}

// 等待 4 秒
try await Task.sleep(nanoseconds: 4_000_000_000)

// 验证只执行了一次网络请求
// （需要通过日志或 mock 来验证）
```

---

## 调试技巧

### 启用详细日志

```swift
// 在 SyncEngine 中添加日志
print("[SyncEngine] Pushing \(items.count) items")
print("[SyncEngine] Server accepted \(response.syncedIDs.count) items")
print("[SyncEngine] Pulled \(response.operations.count) operations")
```

### 使用 Charles Proxy

```bash
# 1. 启动 Charles Proxy
# 2. 配置 macOS 系统代理: 127.0.0.1:8888
# 3. 安装 Charles 根证书
# 4. 查看所有 HTTP/HTTPS 请求
```

### 检查数据库状态

```bash
# 使用 sqlite3 命令行工具
sqlite3 ~/Library/Application\ Support/ReadFlow/reader.db

# 查看 outbox 表
SELECT * FROM sync_outbox;

# 查看 highlights 表
SELECT id, selectedText, syncState FROM highlights;
```

---

## 下一步

完成基本同步测试后，可以继续：

1. **Phase 2.6 - AI Integration**：
   - 实现 AI Popover UI
   - 集成 `/ask` API
   - 实现语义搜索

2. **Phase 2.7 - Settings UI**：
   - 设备配对界面
   - 同步状态监控
   - 手动同步按钮

3. **Phase 3 - Integration Testing**：
   - 多设备同步测试
   - 离线场景测试
   - 性能压测

---

**快速开始版本**：1.0
**最后更新**：2024-01-15
