# AI Popover Quick Start

## 快速开始

本指南帮助你快速集成和使用 ReadFlow 的 AI Popover 功能。

## 5 分钟集成

### Step 1: 导入依赖

确保项目已包含必要的组件：

```swift
import SwiftUI
import GRDB
```

### Step 2: 创建 Popover Controller

在需要显示 AI Popover 的地方创建控制器：

```swift
class MyViewController: NSViewController {
    private let aiPopoverController = AIPopoverController()

    // ...
}
```

### Step 3: 显示 Popover

当用户点击 AI 按钮时：

```swift
@objc func showAIPopover() {
    guard let currentHighlight = getCurrentHighlight() else { return }

    aiPopoverController.showPopover(
        relativeTo: aiButton,
        highlightID: currentHighlight.id,
        sourceID: currentHighlight.sourceID,
        context: currentHighlight.selectedText
    )
}
```

完成！用户现在可以向 AI 提问了。

## 常见使用场景

### 场景 1: FloatingToolbar 集成

```swift
// FloatingToolbar.swift
class FloatingToolbar: NSView {
    private let aiButton: NSButton
    private let aiPopoverController = AIPopoverController()

    @objc private func aiButtonClicked() {
        guard let highlight = AppState.shared.currentHighlight else {
            return
        }

        // 构建上下文（选中文本 + 前后 200 字符）
        let context = buildContext(for: highlight)

        aiPopoverController.showPopover(
            relativeTo: aiButton,
            highlightID: highlight.id,
            sourceID: highlight.sourceID,
            context: context
        )
    }

    private func buildContext(for highlight: Highlight) -> String {
        // 从数据库或 AccessibilityCapture 获取完整上下文
        let selectedText = highlight.selectedText
        let beforeContext = highlight.beforeContext ?? ""
        let afterContext = highlight.afterContext ?? ""

        return "\(beforeContext)\(selectedText)\(afterContext)"
    }
}
```

### 场景 2: 笔记界面集成

```swift
// NoteDetailView.swift
struct NoteDetailView: View {
    let note: Note
    @State private var showingAIPopover = false

    var body: some View {
        VStack {
            Text(note.content)

            Button("Ask AI") {
                showAIPopover(for: note)
            }
        }
    }

    private func showAIPopover(for note: Note) {
        // 使用 note 的 highlightID 显示 popover
        let controller = AIPopoverController()
        controller.showPopover(
            relativeTo: aiButtonView,
            highlightID: note.highlightID,
            sourceID: note.sourceID,
            context: note.content
        )
    }
}
```

### 场景 3: 快捷键触发

```swift
// AppDelegate.swift
func applicationDidFinishLaunching(_ notification: Notification) {
    NSEvent.addLocalMonitorForEvents(matching: .keyDown) { event in
        // Cmd+Shift+A 触发 AI Popover
        if event.modifierFlags.contains([.command, .shift]) &&
           event.charactersIgnoringModifiers == "a" {
            self.showAIPopoverForCurrentSelection()
            return nil
        }
        return event
    }
}

private func showAIPopoverForCurrentSelection() {
    // 获取当前选中的文本
    guard let highlight = captureCurrentSelection() else { return }

    aiPopoverController.showPopover(
        relativeTo: statusBarButton,
        highlightID: highlight.id,
        sourceID: highlight.sourceID,
        context: highlight.selectedText
    )
}
```

## AI Scope 使用指南

### 1. 当前内容 (Current Content)

**适用场景**: 理解当前段落、翻译、解释专业术语

**示例提问**:
- "这段话什么意思？"
- "帮我翻译这段英文"
- "解释一下这个技术术语"

```swift
// 默认 scope 就是 current_content
aiPopoverController.showPopover(
    relativeTo: button,
    highlightID: highlight.id,
    sourceID: source.id,
    context: selectedText
)
```

### 2. 我的知识库 (My Knowledge)

**适用场景**: 关联历史阅读、寻找相似内容、跨文档问答

**示例提问**:
- "我之前读过类似的内容吗？"
- "这个观点和我笔记中的其他内容有什么关系？"
- "总结我关于 X 主题的所有划词"

用户在 Popover 中切换到 "我的知识库" scope 即可。

### 3. 联网 (Web)

**适用场景**: 获取最新信息、事实核查、扩展知识

**示例提问**:
- "这个技术的最新进展是什么？"
- "这个数据准确吗？"
- "给我更多关于 X 的背景信息"

用户在 Popover 中切换到 "联网" scope 即可。

## 自定义 UI

### 修改 Popover 大小

```swift
// AIPopoverController.swift
func showPopover(...) {
    let newPopover = NSPopover()
    newPopover.contentSize = NSSize(width: 500, height: 700)  // 自定义尺寸
    // ...
}
```

### 自定义消息样式

```swift
// AIPopoverView.swift
struct MessageBubble: View {
    let message: ConversationMessage

    var body: some View {
        Text(message.content)
            .padding(12)  // 增加内边距
            .background(customBackgroundColor)  // 自定义颜色
            .cornerRadius(16)  // 自定义圆角
    }

    var customBackgroundColor: Color {
        switch message.role {
        case .user:
            return Color.blue  // 自定义用户消息颜色
        case .assistant:
            return Color(nsColor: .textBackgroundColor)
        case .system:
            return Color.gray.opacity(0.3)
        }
    }
}
```

### 自定义输入框

```swift
// AIPopoverView.swift
private var inputView: some View {
    HStack(spacing: 12) {  // 增加间距
        TextField("问我任何问题...", text: $inputText)  // 自定义占位符
            .textFieldStyle(.roundedBorder)  // 使用圆角边框样式
            .font(.body)  // 自定义字体
            // ...
    }
    .padding(16)  // 增加外边距
}
```

## 调试技巧

### 启用日志

```swift
// AIPopoverViewModel.swift
func sendQuery(_ query: String) async {
    print("🤖 Sending query: \(query)")
    print("📍 Scope: \(selectedScope.rawValue)")
    print("🆔 Conversation ID: \(conversationID?.uuidString ?? "nil")")

    do {
        let response = try await client.ask(...)
        print("✅ Received response: \(response.answer)")
        print("📚 Citations count: \(response.citations?.count ?? 0)")
    } catch {
        print("❌ Error: \(error)")
    }
}
```

### 检查数据库

```swift
// 在 Terminal 中
cd ~/Library/Application\ Support/ReadFlow/
sqlite3 reader.db

-- 查看所有对话
SELECT * FROM conversations;

-- 查看消息
SELECT * FROM conversation_messages;

-- 按对话查询消息
SELECT * FROM conversation_messages WHERE conversationID = 'your-conversation-id';
```

### 模拟 API 响应

```swift
// 用于测试的 Mock Client
class MockLocalBookClient: LocalBookClient {
    override func ask(...) async throws -> AIAnswer {
        try await Task.sleep(nanoseconds: 1_000_000_000)  // 模拟延迟

        return AIAnswer(
            answer: "这是一个模拟回答",
            citations: [
                AICitation(
                    sourceID: UUID().uuidString,
                    title: "测试来源",
                    snippet: "这是引用片段",
                    url: "https://example.com"
                )
            ],
            conversationID: UUID()
        )
    }
}
```

## 常见问题

### Q: Popover 显示位置不对？

**A**: 确保 `positioningView` 已正确布局：

```swift
aiPopoverController.showPopover(
    relativeTo: aiButton,  // 确保 aiButton 已添加到视图层级
    highlightID: highlight.id,
    sourceID: source.id,
    context: context
)

// 如果需要更精确的定位
newPopover.show(
    relativeTo: aiButton.bounds,
    of: aiButton,
    preferredEdge: .maxY  // 尝试不同的边缘: .minY, .maxX, .minX
)
```

### Q: 消息没有保存？

**A**: 检查数据库迁移：

```swift
// 确保 v3_conversations 迁移已运行
let migrator = ReaderDatabase.shared.migrator
print("Applied migrations: \(try! migrator.appliedIdentifiers(...))")
```

### Q: AI 请求总是失败？

**A**: 检查配置：

```swift
// 1. 检查 LocalBookClient 配置
print("Base URL: \(LocalBookClient.shared.baseURL)")
print("Access Token: \(LocalBookClient.shared.accessToken != nil)")

// 2. 检查网络连接
if let status = try? await LocalBookClient.shared.getStatus() {
    print("Device status: \(status)")
} else {
    print("❌ Cannot connect to server")
}
```

### Q: 如何清空对话历史？

**A**: 使用 ConversationStorage：

```swift
let storage = ConversationStorage()

// 删除特定对话
try storage.deleteConversation(id: conversationID)

// 删除 highlight 的所有对话
let conversations = try storage.getConversationsForHighlight(highlightID: highlight.id)
for conversation in conversations {
    try storage.deleteConversation(id: conversation.id)
}
```

## 性能优化

### 1. 消息分页加载

```swift
// TODO: 未来实现
func loadMessages(conversationID: UUID, limit: Int = 50, offset: Int = 0) throws -> [ConversationMessage] {
    try database.read { db in
        try ConversationMessageRecord
            .filter(Column("conversationID") == conversationID)
            .order(Column("createdAt").desc)
            .limit(limit, offset: offset)
            .fetchAll(db)
    }
}
```

### 2. 缓存对话列表

```swift
// AIPopoverViewModel.swift
private var conversationCache: [UUID: [ConversationMessage]] = [:]

func loadConversation() {
    if let cached = conversationCache[conversationID] {
        messages = cached
        return
    }

    // Load from database
    // ...
    conversationCache[conversationID] = messages
}
```

### 3. 异步消息发送

```swift
// 使用 Task 避免阻塞 UI
Button("Send") {
    Task {
        await viewModel.sendQuery(query)
    }
}
```

## 下一步

- 查看 [完整实现文档](./ai-popover-implementation.md)
- 了解 [LocalBookClient API](./sync-engine-implementation.md)
- 探索 [数据库架构](./storage-implementation.md)
