//
//  AIPopoverView.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import SwiftUI

/// AI Scope for query context
enum AIScope: String, CaseIterable {
    case currentContent = "current_content"
    case myKnowledge = "my_knowledge"
    case web = "web"

    var displayName: String {
        switch self {
        case .currentContent: return "当前内容"
        case .myKnowledge: return "我的知识库"
        case .web: return "联网"
        }
    }
}

/// Citation model
///
/// `title` / `snippet` are optional because their source (`AICitation`) is
/// optional on the server. A missing title is a real state ("无标题来源"), so it
/// is carried as `nil` and handled at render time — deliberately **not** faked
/// with a sentinel string, which would be indistinguishable from a real title.
struct Citation: Codable, Identifiable {
    let id: UUID
    let sourceID: UUID
    let title: String?
    let snippet: String?
    let url: String?

    init(id: UUID = UUID(), sourceID: UUID, title: String?, snippet: String?, url: String? = nil) {
        self.id = id
        self.sourceID = sourceID
        self.title = title
        self.snippet = snippet
        self.url = url
    }
}

/// Conversation message model
struct ConversationMessage: Identifiable {
    let id: UUID
    let role: MessageRole
    let content: String
    let timestamp: Date
    let citations: [Citation]?
    /// Which backend produced this message (`localbook` / `cloud` / `offline`).
    ///
    /// Optional, and defaulted, because the two kinds of message differ:
    ///   · a message loaded from history carries what was persisted in
    ///     `conversation_messages.provider` (nullable — rows written before
    ///     migration `v5_conversation_attribution` genuinely have no value);
    ///   · a live user message was written by the user, so there is no producer
    ///     to name, and a not-yet-persisted assistant reply has not been
    ///     attributed yet.
    ///
    /// Either way `nil` means "unknown" and must stay `nil` — substituting a
    /// default would assert a producer the data never claimed.
    let provider: String?
    /// Whether the answer was degraded, with the same optionality reasoning.
    let isDegraded: Bool?

    enum MessageRole {
        case user
        case assistant
        case system
    }

    init(
        id: UUID = UUID(),
        role: MessageRole,
        content: String,
        timestamp: Date = Date(),
        citations: [Citation]? = nil,
        provider: String? = nil,
        isDegraded: Bool? = nil
    ) {
        self.id = id
        self.role = role
        self.content = content
        self.timestamp = timestamp
        self.citations = citations
        self.provider = provider
        self.isDegraded = isDegraded
    }
}

/// AI Popover View
struct AIPopoverView: View {
    @StateObject private var viewModel: AIPopoverViewModel
    @State private var inputText: String = ""
    @FocusState private var isInputFocused: Bool

    /// `storage` is injectable so a caller that is not the running app — the UI
    /// snapshot outlet — can point the conversation at a private store. Without
    /// it, merely *rendering* this view runs `onAppear`, which creates a
    /// conversation: a read-only operation should not write to the user's library.
    init(highlightID: UUID,
         sourceID: UUID,
         context: String,
         storage: ConversationStorage = ConversationStorage()) {
        _viewModel = StateObject(wrappedValue: AIPopoverViewModel(
            highlightID: highlightID,
            sourceID: sourceID,
            context: context,
            storage: storage
        ))
    }

    var body: some View {
        VStack(spacing: 0) {
            // Header with scope selector
            headerView

            Divider()

            // Conversation view
            conversationView

            Divider()

            // Input area
            inputView
        }
        .frame(width: 420, height: 560)
        .onAppear {
            isInputFocused = true
        }
    }

    // MARK: - Header View

    private var headerView: some View {
        VStack(spacing: 8) {
            Text("AI 助手")
                .font(.headline)
                .padding(.top, 12)

            // Scope selector
            Picker("", selection: $viewModel.selectedScope) {
                ForEach(AIScope.allCases, id: \.self) { scope in
                    Text(scope.displayName).tag(scope)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal, 12)
            .padding(.bottom, 8)
        }
    }

    // MARK: - Conversation View

    private var conversationView: some View {
        ScrollViewReader { proxy in
            ScrollView {
                // An empty conversation used to be a blank rectangle: the window
                // said nothing about what this is, what it will read, or what
                // happens without a configured endpoint.
                if viewModel.messages.isEmpty && !viewModel.isLoading {
                    RFEmptyState(systemImage: "sparkles",
                                 title: "向 AI 提问",
                                 message: "回答会引用你的高亮、笔记与阅读条目；检索范围可在上方切换。",
                                 footnote: "未配置云端 AI 时使用离线降级，回答来自本地检索。")
                        .frame(minHeight: 360)
                }

                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(viewModel.messages) { message in
                        MessageBubble(message: message)
                            .id(message.id)
                    }

                    if viewModel.isLoading {
                        HStack {
                            ProgressView()
                                .scaleEffect(0.8)
                            Text("思考中...")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        .padding(.leading, 12)
                    }
                }
                .padding(12)
            }
            .onChange(of: viewModel.messages.count) { _ in
                if let lastMessage = viewModel.messages.last {
                    withAnimation {
                        proxy.scrollTo(lastMessage.id, anchor: .bottom)
                    }
                }
            }
        }
    }

    // MARK: - Input View

    private var inputView: some View {
        HStack(spacing: 8) {
            Image(systemName: "text.bubble")
                .font(.system(size: 12))
                .foregroundStyle(.tertiary)

            TextField("输入问题…", text: $inputText)
                .textFieldStyle(.plain)
                .font(.system(size: 13))
                .focused($isInputFocused)
                .onSubmit {
                    sendMessage()
                }

            Button(action: sendMessage) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 18))
                    .foregroundStyle(inputText.isEmpty ? AnyShapeStyle(.tertiary) : AnyShapeStyle(RFColor.accent))
            }
            .buttonStyle(.plain)
            .disabled(inputText.isEmpty || viewModel.isLoading)
            .help("发送（↵）")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(RFColor.fieldBackground, in: RoundedRectangle(cornerRadius: RFMetric.fieldRadius))
        .overlay(
            RoundedRectangle(cornerRadius: RFMetric.fieldRadius)
                .stroke(RFColor.hairline, lineWidth: 1)
        )
        .padding(.horizontal, 10)
        .padding(.vertical, 10)
        .padding(12)
    }

    // MARK: - Actions

    private func sendMessage() {
        guard !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        let query = inputText
        inputText = ""

        Task {
            await viewModel.sendQuery(query)
        }
    }
}

// MARK: - Message Bubble

struct MessageBubble: View {
    let message: ConversationMessage

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            if message.role == .user {
                Spacer(minLength: 40)
            }

            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 4) {
                // Message content
                Text(message.content)
                    .padding(10)
                    .background(backgroundColor)
                    .foregroundColor(textColor)
                    .cornerRadius(12)

                // Citations
                if let citations = message.citations, !citations.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(citations) { citation in
                            CitationView(citation: citation)
                        }
                    }
                    .padding(.top, 4)
                }

                // Attribution. Only an assistant reply carries one: a message the
                // user wrote has no AI producer and draws nothing, whereas an
                // assistant reply that should be attributed but is not says so
                // (「产出方未知」) — silence there would let "we have no
                // attribution" pass for "no attribution was expected".
                //
                // Every string and colour comes from `AttributionDisplay`, so this
                // view owns no attribution rule of its own — which is what keeps
                // `--self-test attribution-nil` meaningful for this row.
                if let attribution = message.attributionDisplay {
                    HStack(spacing: 6) {
                        if let label = attribution.badgeText {
                            Text(label)
                                .font(.caption2)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(attribution.badgeColor.opacity(0.18))
                                .foregroundColor(attribution.badgeColor)
                                .cornerRadius(4)
                        }
                        // C18: a restricted state must explain itself, so the
                        // unknown case is never represented by bare absence.
                        Text(attribution.explanation)
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                    .padding(.top, 2)
                }

                // Timestamp
                Text(message.timestamp, style: .time)
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }

            if message.role == .assistant {
                Spacer(minLength: 40)
            }
        }
    }

    private var backgroundColor: Color {
        switch message.role {
        case .user:
            return Color.accentColor
        case .assistant:
            return Color(nsColor: .controlBackgroundColor)
        case .system:
            return Color.gray.opacity(0.2)
        }
    }

    private var textColor: Color {
        message.role == .user ? .white : .primary
    }
}

// MARK: - Citation View

struct CitationView: View {
    let citation: Citation

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            // Every operand below comes from `CitationDisplayAccessors`, so this
            // body contains no display decision of its own — the self-test
            // outlet and this view therefore cannot drift apart.
            Text(citation.displayTitle)
                .font(.caption)
                .fontWeight(.medium)

            // `showsSnippet` is a *content* gate, not an existence check: an
            // empty or blank snippet must not construct a `Text` here, because
            // the view would then reserve line height for nothing.
            if let snippet = citation.displaySnippet {
                Text(snippet)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }

            if let rawURL = citation.displayURL,
               let url = URL(string: rawURL),
               ["http", "https"].contains(url.scheme?.lowercased() ?? "") {
                Link(rawURL, destination: url)
                    .font(.caption2)
                    .foregroundColor(.blue)
                    .lineLimit(1)
            }
        }
        .padding(8)
        .background(Color(nsColor: .textBackgroundColor))
        .cornerRadius(8)
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.gray.opacity(0.2), lineWidth: 1)
        )
    }
}

// MARK: - Preview

// `#Preview` needs the `PreviewsMacros` plugin, which ships with Xcode only.
// A Command Line Tools build has to use the macro-free `PreviewProvider` form.
struct AIPopoverView_Previews: PreviewProvider {
    static var previews: some View {
        AIPopoverView(
            highlightID: UUID(),
            sourceID: UUID(),
            context: "这是一段示例上下文"
        )
    }
}
