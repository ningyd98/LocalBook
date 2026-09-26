//
//  ConversationView.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import SwiftUI

/// Reusable conversation view component for displaying AI chat history
struct ConversationView: View {
    @ObservedObject var viewModel: ConversationViewModel

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    ForEach(viewModel.messages) { message in
                        ConversationMessageView(message: message)
                            .id(message.id)
                    }

                    // Loading indicator
                    if viewModel.isLoading {
                        HStack {
                            Spacer()
                            ProgressView()
                                .scaleEffect(0.8)
                            Text("AI 思考中...")
                                .font(.caption)
                                .foregroundColor(.secondary)
                            Spacer()
                        }
                        .padding()
                    }
                }
                .padding()
            }
            .onChange(of: viewModel.messages.count) { _ in
                // Auto-scroll to latest message
                if let lastMessage = viewModel.messages.last {
                    withAnimation(.easeOut(duration: 0.3)) {
                        proxy.scrollTo(lastMessage.id, anchor: .bottom)
                    }
                }
            }
        }
    }
}

// MARK: - Conversation Message View

struct ConversationMessageView: View {
    let message: ConversationMessage

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if message.role == .user {
                Spacer(minLength: 60)
            }

            VStack(alignment: message.role == .user ? .trailing : .leading, spacing: 6) {
                // Message bubble
                Text(message.content)
                    .font(.body)
                    .foregroundColor(textColor)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(bubbleBackground)
                    .cornerRadius(16)
                    .textSelection(.enabled)

                // Citations (if any)
                if let citations = message.citations, !citations.isEmpty {
                    citationsView(citations)
                }

                // Timestamp
                Text(formattedTime)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .padding(.horizontal, 4)
            }

            if message.role != .user {
                Spacer(minLength: 60)
            }
        }
    }

    private var bubbleBackground: some View {
        Group {
            switch message.role {
            case .user:
                LinearGradient(
                    colors: [Color.accentColor, Color.accentColor.opacity(0.8)],
                    startPoint: .topLeading,
                    endPoint: .bottomTrailing
                )
            case .assistant:
                Color(NSColor.controlBackgroundColor)
            case .system:
                Color.orange.opacity(0.2)
            }
        }
    }

    private var textColor: Color {
        message.role == .user ? .white : .primary
    }

    private var formattedTime: String {
        let formatter = DateFormatter()
        formatter.timeStyle = .short
        return formatter.string(from: message.timestamp)
    }

    @ViewBuilder
    private func citationsView(_ citations: [Citation]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("📚 引用来源")
                .font(.caption)
                .fontWeight(.medium)
                .foregroundColor(.secondary)

            ForEach(citations, id: \.id) { citation in
                citationCard(citation)
            }
        }
        .padding(.top, 4)
    }

    @ViewBuilder
    private func citationCard(_ citation: Citation) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            // Same accessors as `CitationView` — one derivation, two consumers.
            Text(citation.displayTitle)
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundColor(.accentColor)

            // Content gate, not an existence check (see CitationView).
            if let snippet = citation.displaySnippet {
                Text(snippet)
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(3)
            }
            if let rawURL = citation.displayURL,
               let url = URL(string: rawURL),
               ["http", "https"].contains(url.scheme?.lowercased() ?? "") {
                Link(rawURL, destination: url)
                    .font(.caption2)
                    .lineLimit(1)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(NSColor.controlBackgroundColor))
        .cornerRadius(8)
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.accentColor.opacity(0.3), lineWidth: 1)
        )
    }
}

// MARK: - Conversation View Model

@MainActor
class ConversationViewModel: ObservableObject {
    @Published var messages: [ConversationMessage] = []
    @Published var isLoading: Bool = false

    private let storage: ConversationStorage

    init(storage: ConversationStorage = ConversationStorage()) {
        self.storage = storage
    }

    func addMessage(_ message: ConversationMessage) {
        messages.append(message)
    }

    func addMessages(_ messages: [ConversationMessage]) {
        self.messages.append(contentsOf: messages)
    }

    func setLoading(_ loading: Bool) {
        isLoading = loading
    }

    func clearMessages() {
        messages.removeAll()
    }

    func loadConversationHistory(conversationID: UUID) async {
        do {
            messages = try storage.conversationMessages(conversationID: conversationID)
        } catch {
            messages = [ConversationMessage(
                role: .system,
                content: "无法读取对话历史：\(error.localizedDescription)"
            )]
        }
    }
}

// MARK: - Preview

#if DEBUG
struct ConversationView_Previews: PreviewProvider {
    static var previews: some View {
        let viewModel = ConversationViewModel()

        // Add sample messages
        viewModel.addMessage(ConversationMessage(
            id: UUID(),
            role: .user,
            content: "解释这段代码的作用",
            timestamp: Date().addingTimeInterval(-120)
        ))

        viewModel.addMessage(ConversationMessage(
            id: UUID(),
            role: .assistant,
            content: "这段代码实现了一个对话视图组件，用于显示 AI 助手和用户之间的对话历史。它使用 SwiftUI 的 ScrollViewReader 来实现自动滚动到最新消息的功能。",
            timestamp: Date().addingTimeInterval(-60),
            citations: [
                Citation(
                    sourceID: UUID(),
                    title: "SwiftUI 官方文档",
                    snippet: "ScrollViewReader is a view that provides programmatic scrolling..."
                )
            ]
        ))

        viewModel.addMessage(ConversationMessage(
            id: UUID(),
            role: .user,
            content: "它支持引用吗？",
            timestamp: Date().addingTimeInterval(-30)
        ))

        return ConversationView(viewModel: viewModel)
            .frame(width: 400, height: 500)
    }
}
#endif
