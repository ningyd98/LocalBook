# FloatingToolbar 快速集成指南

## 使用方式

### 1. 通过 SelectionService（自动集成）

```swift
// 已自动集成，无需额外代码
let service = SelectionService.shared

// Accessibility 捕获（自动显示工具栏）
service.captureSelection()

// Safari Extension 消息（自动显示工具栏）
service.handleExtensionMessage(message)
```

### 2. 全局快捷键（已配置）

用户按下 **Command + Shift + C**：
1. AppDelegate 捕获按键事件
2. 调用 SelectionService.captureSelection()
3. FloatingToolbar 自动显示

### 3. 菜单栏（已配置）

用户点击菜单栏图标 → 选择「捕获选中内容」：
1. 调用 AppState.captureSelection()
2. 底层调用 SelectionService.captureSelection()
3. FloatingToolbar 自动显示

## 测试步骤

### 基础测试

1. **启动应用**
   ```bash
   cd /path/to/LocalBook/ReadFlow
   swift run ReadFlow
   ```
   或先执行 `./scripts/build_app.sh`，再打开 `dist/ReadFlow.app`。

2. **授予 Accessibility 权限**
   - 首次运行会弹出权限请求
   - 系统偏好设置 → 隐私与安全性 → 辅助功能
   - 勾选 ReadFlow

3. **测试 Accessibility 捕获**
   - 打开 Preview.app，加载任意 PDF
   - 选中一段文字
   - 按 **Command + Shift + C**
   - **预期**：FloatingToolbar 显示在鼠标位置上方

4. **测试保存功能**
   - 工具栏显示后，点击「⭐️ 保存」按钮
   - **预期**：
     - UI 显示「✓ 已保存」反馈（1秒）
     - 终端打印：`✅ Highlight saved in Xms`
     - 工具栏 0.5 秒后隐藏

5. **验证数据库写入**
   - 打开 `~/Library/Application Support/ReadFlow/reader.db`
   - 使用 DB Browser for SQLite 查看
   - 检查 `highlights` 表和 `sync_outbox` 表

### 性能测试

```bash
# 查看终端输出，验证保存耗时 < 10ms
✅ Highlight saved in 3ms   # 符合要求
✅ Highlight saved in 8ms   # 符合要求
❌ Highlight saved in 15ms  # 需要优化
```

### 自动隐藏测试

1. **3秒无交互**
   - 显示工具栏后不点击
   - **预期**：3秒后自动隐藏

2. **失去焦点**
   - 显示工具栏后点击其他窗口
   - **预期**：0.3秒后隐藏

## 故障排查

### 工具栏不显示

**原因1**：Accessibility 权限未授予
```bash
# 检查
SelectionService.shared.checkAccessibilityPermission()

# 解决
SelectionService.shared.requestAccessibilityPermission()
```

**原因2**：没有选中文本
```bash
# 确保文本已选中，且应用支持 Accessibility API
# 支持的应用：Preview.app, TextEdit, Pages, VS Code 等
```

**原因3**：快捷键冲突
```bash
# 尝试通过菜单栏触发
# 点击菜单栏图标 → 「捕获选中内容」
```

### 保存失败

**检查数据库路径**
```swift
print(FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0])
```

**检查数据库权限**
```bash
ls -la ~/Library/Application\ Support/ReadFlow/
chmod 755 ~/Library/Application\ Support/ReadFlow/
```

**检查 GRDB 初始化**
```swift
do {
    let db = try ReaderDatabase.shared()
    print("✅ Database initialized")
} catch {
    print("❌ Database error: \(error)")
}
```

### 性能不达标（> 10ms）

**优化建议**：

1. **启用 WAL 模式**（已启用）
   ```swift
   try db.usePassphraseWhenEncrypting()
   try db.checkpoint(.passive)
   ```

2. **批量写入**
   ```swift
   try database.write { db in
       try source.insert(db)
       try highlight.insert(db)
       try outbox.insert(db)
   }
   ```

3. **异步写入**（谨慎使用）
   ```swift
   DispatchQueue.global(qos: .userInitiated).async {
       try? database.createHighlight(highlight)
   }
   ```

## 下一步开发

### Phase 2.5 - 同步引擎

完整实现 SyncEngine.shared.triggerSync()：
- Outbox 队列消费
- HTTP 请求到 LocalBook
- 冲突检测和解决
- 双向同步

### Phase 3 - 高级功能

1. **AI 分析**
   - 打开 AI Popover
   - 集成 LLM API
   - 显示分析结果

2. **搜索功能**
   - 打开搜索面板
   - FTS5 全文搜索
   - 高亮匹配结果

3. **笔记功能**
   - 打开笔记编辑器
   - Markdown 支持
   - 自动保存

## 文件结构

```
ReadFlow/ReadFlow/
├── UI/
│   └── FloatingToolbar.swift          # 核心 UI 组件
├── Capture/
│   ├── SelectionService.swift         # 集成入口（已更新）
│   └── AccessibilityCapture.swift     # Accessibility API
├── App/
│   ├── AppDelegate.swift              # 全局快捷键（新增）
│   └── ReadFlowApp.swift              # 应用入口（已更新）
└── Storage/
    ├── ReaderDatabase.swift           # 数据库
    └── Models/                        # 数据模型
```

## API 参考

### FloatingToolbar

```swift
class FloatingToolbar: NSPanel {
    static let shared: FloatingToolbar

    func show(at point: NSPoint, for selection: CapturedSelection)
    func hide()
}
```

### SelectionService

```swift
class SelectionService: ObservableObject {
    static let shared: SelectionService

    @Published var lastCapturedSelection: CapturedSelection?

    func captureSelection() -> CapturedSelection?
    func checkAccessibilityPermission() -> Bool
    func requestAccessibilityPermission()
    func handleExtensionMessage(_ message: SafariExtensionMessage)
}
```

### AppDelegate

```swift
class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification)
    // 自动注册全局快捷键 Command + Shift + C
}
```
