//
//  AIPopoverViewModel.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import Combine

/// View model for AI Popover
///
/// **Answers go through `AICoordinator`, not straight to `LocalBookClient`.**
/// Until this changed, the live path called `LocalBookClient.ask` directly and
/// the coordinator was constructed *only* by self-tests — so a user's configured
/// cloud endpoint was never used for real answering, `OfflineAIProvider` never
/// ran in production, and every fresh reply was persisted without attribution.
/// The `A15`–`A19` scenarios were therefore exercising a component production
/// never built. Fixing that is what this type now does: the coordinator is built
/// from the user's stored settings, LocalBook remains one backend among three,
/// and offline is the terminal fallback.
@MainActor
class AIPopoverViewModel: ObservableObject {
    @Published var messages: [ConversationMessage] = []
    @Published var selectedScope: AIScope = .currentContent
    @Published var isLoading: Bool = false
    @Published var error: String?
    /// Whether the most recent send failed. Set by `sendQuery`'s catch, so a
    /// headless check can tell "the send failed and was surfaced" from "the send
    /// succeeded but wrote nothing" without reading the message list.
    @Published private(set) var lastSendFailed = false

    private let highlightID: UUID
    private let sourceID: UUID
    private let context: String
    private var conversationID: UUID?

    private let storage: ConversationStorage
    private let coordinator: AICoordinator

    private var cancellables = Set<AnyCancellable>()

    /// Provenance of the most recent answer, so the view can label the row.
    /// `nil` until a query has been answered in this session.
    var lastProvenance: (display: AttributionDisplay, fallbacks: [FallbackRecord])? {
        lastOutcome.map {
            (ReadFlow.attributionDisplay(
                provider: $0.response.provider,
                isDegraded: $0.response.isDegraded
             ), $0.fallbacks)
        }
    }

    /// Readable so the scriptable live-path outlet can assert what the live path
    /// decided, without that outlet having to build a second path to observe.
    private(set) var lastOutcome: AIOutcome?

    init(
        highlightID: UUID,
        sourceID: UUID,
        context: String,
        storage: ConversationStorage = ConversationStorage(),
        database: ReaderDatabase = .shared,
        keychain: KeychainService = .shared,
        coordinator: AICoordinator? = nil
    ) {
        self.highlightID = highlightID
        self.sourceID = sourceID
        self.context = context
        self.storage = storage
        self.coordinator = coordinator ?? Self.makeCoordinator(
            database: database,
            keychain: keychain
        )

        loadConversation()
    }

    /// Builds the coordinator from the user's own configuration.
    ///
    /// - cloud: whatever `CloudSettingsStore` holds (three UserDefaults keys plus
    ///   the keychain key). Passed as `nil` when unusable, which is what keeps S1
    ///   a real state rather than a provider that fails later.
    /// - localbook: the existing client, still a first-class backend — not
    ///   bypassed, and not privileged either.
    /// - offline: the terminal fallback, with a local-search hook so a degraded
    ///   answer can still cite what is on disk.
    ///
    /// `database` and `keychain` are parameters rather than reached through
    /// `storage` because `ConversationStorage.database` is private: threading them
    /// in keeps the store encapsulated while still letting the local-search hook
    /// read the index, and lets a headless run substitute an in-memory keychain
    /// (the real one is unavailable without a login session).
    private static func makeCoordinator(
        database: ReaderDatabase,
        keychain: KeychainService
    ) -> AICoordinator {
        let settingsStore = CloudSettingsStore(keychain: keychain)
        return AICoordinator.make(
            cloudConfiguration: settingsStore.cloudConfiguration,
            localbookProvider: LocalBookAIProvider(),
            localSearch: { query in
                // Local hits become citations on a degraded answer (contract
                // §3.3). A failing lookup degrades to "no citations" rather than
                // propagating: a fallback that throws would defeat its own point.
                guard let hits = try? database.searchLocal(query: query, limit: 5) else {
                    return []
                }
                // `LocalSearchResult` has no title/snippet of its own, so the
                // citation carries the resolved source title as its headline —
                // `nil` when unknown, never an invented string.
                return hits.map { hit in
                    AICitation(
                        sourceID: hit.sourceID?.uuidString ?? "",
                        title: hit.sourceTitle,
                        snippet: hit.content,
                        url: nil
                    )
                }
            }
        )
    }

    // MARK: - Conversation Management

    private func loadConversation() {
        do {
            let conversations = try storage.getConversationsForHighlight(highlightID: highlightID)
            if let existing = conversations.first(where: { $0.scope == selectedScope.rawValue }) {
                conversationID = existing.id
                messages = try storage.conversationMessages(conversationID: existing.id)
                return
            }

            let conversation = try storage.createConversation(
                highlightID: highlightID,
                scope: selectedScope
            )
            conversationID = conversation.id
        } catch let storageError {
            self.error = "无法读取对话历史：\(storageError.localizedDescription)"
        }
    }

    // MARK: - Query Handling

    func sendQuery(_ query: String) async {
        lastSendFailed = false
        do {
            try await sendQueryThrowing(query)
        } catch {
            lastSendFailed = true
            // Failures stay failures: the error is surfaced and a system notice is
            // appended. This path never fabricates a model-shaped answer.
            self.error = error.localizedDescription
            messages.append(ConversationMessage(
                role: .system,
                content: "抱歉，发生了错误：\(error.localizedDescription)"
            ))
            isLoading = false
        }
    }

    /// The same send, but rethrows so the failing behaviour is assertable.
    ///
    /// `sendQuery` swallows the error into `error`/a system message, which is
    /// right for the UI but leaves no way for a headless check to tell "the call
    /// failed" from "the call succeeded and wrote nothing". Exposing the throwing
    /// core keeps one implementation and one behaviour.
    func sendQueryThrowing(_ query: String) async throws {
        guard !query.isEmpty else { return }

        // Add user message
        let userMessage = ConversationMessage(
            role: .user,
            content: query
        )
        messages.append(userMessage)

        // Save user message to storage
        if let convID = conversationID {
            do {
                _ = try storage.addMessage(
                    conversationID: convID,
                    role: .user,
                    content: query
                )
            } catch {
                print("Failed to save user message: \(error)")
            }
        }

        isLoading = true
        error = nil

        let request = AIRequest(
            query: query,
            context: context,
            scope: selectedScope,
            sourceID: sourceID,
            highlightID: highlightID,
            conversationID: conversationID
        )

        do {
            // The one exiting call. Which backend answers, and whether the answer
            // is a degradation, is decided inside the coordinator by the frozen
            // C5 order — not here.
            let outcome = try await coordinator.ask(request)
            lastOutcome = outcome

            let response = outcome.response

            // Parse citations. `sourceID` is non-optional on `Citation`, but a
            // malformed id must not be turned into a fresh random one: that would
            // be indistinguishable from a real source and, because `Citation` is
            // `Codable`, would persist as if it were one. It collapses to a single
            // shared sentinel instead, which never matches a real `Source`.
            let citations = response.citations.map { citationData in
                Citation(
                    sourceID: Citation.unresolvedSourceID(for: citationData.sourceID),
                    title: citationData.title,
                    snippet: citationData.snippet,
                    url: citationData.url
                )
            }

            // Attribution travels with the row from the moment it is created, so
            // a fresh answer is labelled exactly like one read back from history.
            let attribution = ReadFlow.attributionDisplay(
                provider: response.provider,
                isDegraded: response.isDegraded
            )

            let assistantMessage = ConversationMessage(
                role: .assistant,
                content: response.text,
                citations: citations,
                provider: response.provider.rawValue,
                isDegraded: response.isDegraded
            )
            messages.append(assistantMessage)

            // Single write point: both attribution columns are written in this one
            // insert, so a live row can never be half-attributed.
            if let convID = conversationID {
                _ = try storage.addMessage(
                    conversationID: convID,
                    role: .assistant,
                    content: response.text,
                    citations: citations,
                    provider: response.provider.rawValue,
                    isDegraded: response.isDegraded
                )
            }

            // A fallback must not be silent (C18): the user is told which backend
            // answered, that the answer is degraded, and why. The badge alone
            // would leave "we fell back" indistinguishable from "this is normal".
            if !outcome.fallbacks.isEmpty || response.isDegraded {
                let reason = outcome.fallbacks.last?.reason
                var notice = "[\(attribution.explanation)]"
                if let reason, !reason.isEmpty {
                    notice += " 原因：\(reason)"
                }
                messages.append(ConversationMessage(role: .system, content: notice))
            }

        }

        isLoading = false
    }

    // MARK: - Scope Change

    func changeScope(_ newScope: AIScope) {
        guard newScope != selectedScope else { return }

        selectedScope = newScope

        // Keep the previous scope as history and select an existing branch when
        // one is available. Otherwise create a deliberate branch for the new
        // scope, so reopening this highlight never mixes prompt contexts.
        do {
            let conversations = try storage.getConversationsForHighlight(highlightID: highlightID)
            if let existing = conversations.first(where: { $0.scope == newScope.rawValue }) {
                conversationID = existing.id
                messages = try storage.conversationMessages(conversationID: existing.id)
            } else {
                let conversation = try storage.createConversation(
                    highlightID: highlightID,
                    scope: newScope
                )
                conversationID = conversation.id
                messages.removeAll()
            }
        } catch let storageError {
            self.error = "无法切换对话范围：\(storageError.localizedDescription)"
        }
    }
}
