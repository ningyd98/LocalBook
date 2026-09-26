# Floating Toolbar 实现文档

## 概述

FloatingToolbar 是 ReadFlow 的核心 UI 组件，在用户划词后自动显示在选中文字附近，提供快速操作按钮。

## 架构设计

### 组件层次

```
FloatingToolbar (NSPanel)
├── NSView (contentView)
│   ├── NSStackView (按钮容器)
│   │   ├── 保存按钮 (star.fill)
│   │   ├── AI 按钮 (brain.head.profile)
│   │   ├── 搜索按钮 (magnifyingglass)
│   │   └── 笔记按钮 (note.text)
│   └── 阴影和圆角
└── FeedbackWindow (临时反馈窗口)
```

### 关键特性

1. **NSPanel 样式**
   - `styleMask`: [.borderless, .nonactivatingPanel]
   - `level`: .floating（浮动在所有窗口之上）
   - `backgroundColor`: .clear（透明背景）
   - `hasShadow`: true（投影效果）

2. **非激活面板**
   - 点击工具栏不会切换应用焦点
   - 保持用户在原应用的工作流

3. **自动定位**
   - 默认显示在鼠标位置上方 30px
   - 自动调整位置避免超出屏幕边界
   - 智能计算 X/Y 坐标偏移

4. **自动隐藏**
   - 3 秒无交互自动隐藏
   - 失去焦点 0.3 秒后隐藏
   - 操作完成后延迟隐藏

## 核心 API

### 单例模式

```swift
let toolbar = FloatingToolbar.shared
```

### 显示工具栏

```swift
toolbar.show(at: mouseLocation, for: selection)
```

**参数**：
- `at`: 屏幕坐标位置（通常为鼠标位置）
- `for`: CapturedSelection 对象

### 隐藏工具栏

```swift
toolbar.hide()
```

## 按钮操作

### 1. 保存按钮（⭐️）

**功能**：保存 Highlight 到本地数据库

**流程**：
1. 解析或创建 Source（根据 URL 或 filePath）
2. 创建 Highlight 对象
3. 调用 `database.createHighlight()` 保存（包含 Outbox）
4. 显示「✓ 已保存」反馈
5. 后台触发同步
6. 0.5 秒后隐藏工具栏

**性能**：
- 目标：< 10ms 写入本地
- 实际：测量并打印实际耗时
- 优化：使用 GRDB 事务批量写入

```swift
@objc private func saveHighlight() {
    let startTime = Date()

    // 1. 解析 Source
    let source = try resolveSource(from: selection.source)

    // 2. 创建 Highlight
    let highlight = Highlight(...)

    // 3. 保存
    try database.createHighlight(highlight)

    let duration = Date().timeIntervalSince(startTime)
    print("✅ Highlight saved in \(Int(duration * 1000))ms")

    // 4. UI 反馈
    showCheckmark()

    // 5. 触发同步
    SyncEngine.shared.triggerSync()
}
```

### 2. AI 按钮（🧠）

**功能**：使用 AI 分析选中文本（Phase 3 实现）

**占位实现**：
- 打印调试信息
- 显示「AI 功能待实现」反馈
- 隐藏工具栏

### 3. 搜索按钮（🔍）

**功能**：搜索相关内容（Phase 3 实现）

**占位实现**：
- 打印调试信息
- 显示「搜索功能待实现」反馈
- 隐藏工具栏

### 4. 笔记按钮（📝）

**功能**：为选中文本添加笔记（Phase 3 实现）

**占位实现**：
- 打印调试信息
- 显示「笔记功能待实现」反馈
- 隐藏工具栏

## UI 反馈机制

### FeedbackWindow

临时浮动窗口，显示操作反馈消息。

**特点**：
- 显示在工具栏上方 10px
- 1 秒后自动关闭
- 半透明背景（alpha 0.95）
- 圆角 8px

**使用**：

```swift
showCheckmark()        // ✓ 已保存
showError("保存失败")   // ✗ 保存失败
showFeedback("消息")    // 自定义消息
```

## 集成方式

### SelectionService 集成

FloatingToolbar 已集成到 SelectionService：

```swift
// Accessibility 捕获
func captureSelection() -> CapturedSelection? {
    guard let selection = accessibilityCapture.captureSelection() else {
        return nil
    }

    // 自动显示 FloatingToolbar
    DispatchQueue.main.async {
        let mouseLocation = NSEvent.mouseLocation
        FloatingToolbar.shared.show(at: mouseLocation, for: selection)
    }

    return selection
}

// Safari Extension 消息
func handleExtensionMessage(_ message: SafariExtensionMessage) {
    let selection = CapturedSelection(...)

    DispatchQueue.main.async {
        self.lastCapturedSelection = selection

        // 自动显示 FloatingToolbar
        let mouseLocation = NSEvent.mouseLocation
        FloatingToolbar.shared.show(at: mouseLocation, for: selection)
    }
}
```

### 全局快捷键

通过 AppDelegate 注册 Command + Shift + C：

```swift
NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { event in
    if event.modifierFlags.contains([.command, .shift]) && event.keyCode == 8 {
        self.captureSelection()
    }
}
```

### 菜单栏集成

ReadFlowApp.swift 中的 MenuBarView：

```swift
Button(action: {
    appState.captureSelection()
}) {
    Label("捕获选中内容", systemImage: "selection.pin.in.out")
}
```

## Source 解析逻辑

### resolveSource()

根据 CapturedSource 创建或查找 Source：

```swift
private func resolveSource(from capturedSource: CapturedSource) throws -> Source {
    // 1. 尝试通过 URL 或 filePath 查找现有 Source
    // （简化实现：直接创建新 Source）

    // 2. 确定 Source 类型
    let type = determineSourceType(from: capturedSource)

    // 3. 创建 Source
    let source = Source(
        id: UUID(),
        type: type,
        title: capturedSource.windowTitle ?? "Untitled",
        url: capturedSource.url,
        filePath: capturedSource.filePath,
        author: nil,
        metadata: [
            "appBundleID": capturedSource.appBundleID,
            "appName": capturedSource.appName
        ],
        createdAt: Date(),
        updatedAt: Date()
    )

    try database.createSource(source)
    return source
}
```

### Source 类型判断

```swift
private func determineSourceType(from capturedSource: CapturedSource) -> String {
    if capturedSource.url != nil {
        return "webpage"
    } else if let filePath = capturedSource.filePath {
        if filePath.hasSuffix(".pdf") {
            return "pdf"
        } else if filePath.hasSuffix(".epub") {
            return "epub"
        } else {
            return "document"
        }
    } else {
        return "unknown"
    }
}
```

## 样式设计

### 配色

- 背景：`NSColor.controlBackgroundColor.withAlphaComponent(0.95)`
- 阴影：黑色 30% 透明度，偏移 (0, -2)，半径 8px
- 按钮：透明背景，悬停时浅灰色

### 尺寸

- 工具栏：240 x 48 px
- 按钮：48 x 32 px
- 圆角：8px（工具栏）、6px（按钮）
- 间距：8px（按钮之间）、12px（左右边距）

### SF Symbols

- 保存：`star.fill`
- AI：`brain.head.profile`
- 搜索：`magnifyingglass`
- 笔记：`note.text`

所有图标：18pt, medium weight

## 性能优化

### 保存性能

**目标**：< 10ms 写入本地

**测量**：
```swift
let startTime = Date()
try database.createHighlight(highlight)
let duration = Date().timeIntervalSince(startTime)
print("✅ Highlight saved in \(Int(duration * 1000))ms")
```

**优化策略**：
1. GRDB 已使用 WAL 模式（并发读写）
2. 单次写入操作（事务自动提交）
3. 异步触发同步（不阻塞 UI）
4. 索引优化（source_id, created_at）

### UI 响应

- 使用 `DispatchQueue.main.async` 更新 UI
- 后台同步使用 `.utility` QoS
- 反馈窗口延迟关闭避免闪烁

## 已知限制

1. **Source 去重未实现**
   - 当前每次捕获都创建新 Source
   - Phase 3 需要实现 URL/filePath 去重逻辑

2. **全局快捷键限制**
   - 使用 Carbon Event Manager（已废弃）
   - 建议使用第三方库（如 HotKey）或系统服务

3. **窗口定位精度**
   - 使用鼠标位置近似选中文字位置
   - 无法获取 Accessibility 元素的屏幕坐标

4. **AI/搜索/笔记功能**
   - 当前为占位实现
   - Phase 3 需要完整实现

## 测试验证

### 手动测试

1. **启动应用**
   ```bash
   cd ReadFlow
   open ReadFlow.xcodeproj
   # 在 Xcode 中运行
   ```

2. **测试 Accessibility 捕获**
   - 在 Preview.app 打开 PDF
   - 选中文本
   - 按 Command + Shift + C
   - 验证工具栏显示

3. **测试保存功能**
   - 点击保存按钮
   - 验证「✓ 已保存」反馈
   - 检查终端打印的保存耗时
   - 验证数据库写入（使用 DB Browser for SQLite）

4. **测试自动隐藏**
   - 显示工具栏后不操作
   - 验证 3 秒后自动隐藏
   - 点击其他窗口
   - 验证 0.3 秒后隐藏

### 性能测试

```swift
// 在 saveHighlight() 中添加计时
let startTime = Date()
try database.createHighlight(highlight)
let duration = Date().timeIntervalSince(startTime)

// 验证 < 10ms
XCTAssertLessThan(duration, 0.01, "保存耗时应 < 10ms")
```

## 下一步

1. **Phase 2.5**：实现完整的 SyncEngine
2. **Phase 3**：实现 AI/搜索/笔记功能
3. **优化**：Source 去重、全局快捷键库、精确窗口定位

## 文件清单

```
ReadFlow/
├── UI/
│   └── FloatingToolbar.swift       (470 行)
├── Capture/
│   └── SelectionService.swift      (更新)
└── App/
    ├── AppDelegate.swift           (110 行)
    └── ReadFlowApp.swift           (更新)
```

## 依赖关系

```
FloatingToolbar
├── ReaderDatabase (保存数据)
├── CapturedSelection (选中数据)
└── SyncEngine (触发同步，占位)

SelectionService
├── AccessibilityCapture
├── FloatingToolbar (显示 UI)
└── CapturedSelection

AppDelegate
└── SelectionService (捕获入口)
```
