# AI Popover Implementation Guide

## Overview

AI Popover 是 ReadFlow 的核心交互界面，提供基于上下文的 AI 问答功能。用户可以针对划词内容向 AI 提问，并在三种不同的知识范围（scope）之间切换。

## Architecture

### Components

```
AIPopover/
├── AIPopoverView.swift           # SwiftUI 视图层
├── AIPopoverViewModel.swift      # 业务逻辑层
└── AIPopoverController.swift     # NSPopover 控制器
```

### Data Flow

```
User Input → ViewModel → LocalBookClient → Server API
                ↓
         ConversationStorage (persistence)
                ↓
         Messages List (UI update)
```

## Core Features

### 1. AI Scope Switching

三种知识范围：

- **当前内容** (`current_content`): 仅基于当前划词的上下文回答
- **我的知识库** (`my_knowledge`): 基于用户的所有阅读数据回答
- **联网** (`web`): 联网搜索实时信息

切换 scope 时会创建新的对话分支。

### 2. Conversation History

- 每个 highlight 可以有多个对话（按 scope 区分）
- 消息持久化存储在 SQLite 数据库
- 支持连续追问（保持上下文）

### 3. Citations

AI 回答可能包含引用（citations）：

```swift
struct Citation {
    let id: UUID
    let sourceID: UUID
    let title: String
    let snippet: String
    let url: String?
}
```

引用会在消息下方显示，帮助用户溯源。

## Usage

### Basic Usage

```swift
// 1. Create controller
let popoverController = AIPopoverController()

// 2. Show popover
popoverController.showPopover(
    relativeTo: aiButton,
    highlightID: highlight.id,
    sourceID: source.id,
    context: "这是选中的文本及其上下文"
)

// 3. Close popover
popoverController.closePopover()
```

### Integration with FloatingToolbar

```swift
// In FloatingToolbar
private let aiPopoverController = AIPopoverController()

@objc private func aiButtonClicked() {
    guard let highlight = currentHighlight else { return }

    aiPopoverController.showPopover(
        relativeTo: aiButton,
        highlightID: highlight.id,
        sourceID: highlight.sourceID,
        context: highlight.selectedText
    )
}
```

## Database Schema

### conversations table

```sql
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    highlightID TEXT NOT NULL REFERENCES highlights(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    createdAt DATETIME NOT NULL,
    updatedAt DATETIME NOT NULL
);
```

### conversation_messages table

```sql
CREATE TABLE conversation_messages (
    id TEXT PRIMARY KEY,
    conversationID TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    citationsJSON BLOB,
    createdAt DATETIME NOT NULL
);
```

## API Integration

### POST /api/v1/reader/ask

Request:
```json
{
  "query": "这段话什么意思？",
  "source_id": "uuid",
  "highlight_id": "uuid",
  "conversation_id": "uuid",
  "context": "选中的文本及上下文",
  "scope": "current_content"
}
```

Response:
```json
{
  "answer": "AI 的回答",
  "citations": [
    {
      "source_id": "uuid",
      "title": "来源标题",
      "snippet": "引用片段",
      "url": "https://example.com"
    }
  ],
  "conversation_id": "uuid"
}
```

## UI Customization

### Color Scheme

- User messages: Accent color background
- Assistant messages: Control background color
- System messages: Gray background

### Layout

- Width: 420pt
- Height: 560pt
- Popover behavior: Transient (auto-close on focus loss)

### Typography

- Message content: Default body font
- Citations: Caption font
- Timestamps: Caption2 font

## Error Handling

### Network Errors

```swift
do {
    let response = try await client.ask(...)
} catch NetworkError.unauthorized {
    // Show pairing prompt
} catch NetworkError.networkUnavailable {
    // Show offline message
} catch {
    // Show generic error
}
```

### UI Feedback

- Loading state: ProgressView with "思考中..." text
- Error messages: System message bubble with error description
- Empty state: (TODO) Show welcome message on first load

## Performance Considerations

### Message Loading

- Messages are loaded lazily from database
- Only load messages for current conversation
- Use `LazyVStack` for efficient scrolling

### Auto-scroll

```swift
ScrollViewReader { proxy in
    // ...
    .onChange(of: viewModel.messages.count) { _ in
        if let lastMessage = viewModel.messages.last {
            withAnimation {
                proxy.scrollTo(lastMessage.id, anchor: .bottom)
            }
        }
    }
}
```

## Testing

### Unit Tests

```swift
func testCreateConversation() {
    let storage = ConversationStorage()
    let conversation = try! storage.createConversation(
        highlightID: UUID(),
        scope: .currentContent
    )
    XCTAssertNotNil(conversation.id)
}

func testAddMessage() {
    let storage = ConversationStorage()
    // ...
    try! storage.addMessage(
        conversationID: conversation.id,
        role: .user,
        content: "Test message"
    )

    let messages = try! storage.getMessages(conversationID: conversation.id)
    XCTAssertEqual(messages.count, 1)
}
```

### Integration Tests

- Test end-to-end flow with mock server
- Test scope switching behavior
- Test citation display

## Future Enhancements

### Phase 3 (Future)

- [ ] Voice input support
- [ ] Message editing
- [ ] Conversation branching UI
- [ ] Export conversation as Markdown
- [ ] Conversation search
- [ ] Suggested follow-up questions
- [ ] Code syntax highlighting in messages
- [ ] Image attachments in messages

### Performance Optimizations

- [ ] Message pagination (load on scroll)
- [ ] Conversation list caching
- [ ] Debounce typing indicators
- [ ] Background conversation preloading

## Troubleshooting

### Popover doesn't show

1. Check if `positioningView` is valid
2. Verify `highlightID` and `sourceID` exist
3. Check console for errors

### Messages not persisting

1. Verify database migration ran successfully
2. Check `conversationID` is not nil
3. Verify database file permissions

### API calls failing

1. Check LocalBookClient configuration
2. Verify access token is valid
3. Check network connectivity
4. Review server logs

## References

- [LocalBookClient API](./sync-engine-implementation.md)
- [ReaderDatabase Schema](./storage-implementation.md)
- [Reader API Specification](./api-reference.md)
