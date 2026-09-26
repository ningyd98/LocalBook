//
//  LocalBookAIProvider.swift
//  ReadFlow
//
//  The `.localbook` backend: the existing `LocalBookClient.ask` path, wrapped
//  behind `AIProvider` so the fallback chain can treat it as one option among
//  three rather than the only option (contract §3.3).
//
//  This provider is a first-class backend, not a legacy branch: when the server
//  is configured and reachable it answers normally. What changed is that its
//  absence is no longer fatal — the coordinator falls through to cloud/offline
//  instead of surfacing a network error as "AI is broken".
//

import Foundation

final class LocalBookAIProvider: AIProvider {
    let kind: AIProviderKind = .localbook

    private let client: LocalBookClient

    init(client: LocalBookClient = .shared) {
        self.client = client
    }

    /// Cheap probe: configuration presence only. Issuing a real `/ask` to test
    /// availability would be a paid request, which §3.1 forbids for `isAvailable`.
    func isAvailable() async -> Bool {
        client.isConfigured()
    }

    func ask(_ request: AIRequest) async throws -> AIResponse {
        do {
            let answer = try await client.ask(
                conversationID: request.conversationID,
                sourceID: request.sourceID,
                highlightID: request.highlightID,
                query: request.query,
                context: request.context,
                scope: request.scope.rawValue
            )

            return AIResponse(
                requestID: request.requestID,
                text: answer.answer,
                citations: answer.citations,
                provider: .localbook,
                isDegraded: false,
                model: answer.model
            )
        } catch let error as NetworkError {
            throw Self.map(error)
        }
    }

    /// Maps the transport-level error taxonomy onto the frozen provider
    /// taxonomy (§3.5). The two are deliberately kept separate: `NetworkError`
    /// describes the wire, `AIProviderError` describes what the *user* can do
    /// about it, and the fallback policy (C5) keys off the latter.
    static func map(_ error: NetworkError) -> AIProviderError {
        switch error {
        case .unauthorized, .pairingApprovalRequired:
            return .unauthorized
        case .invalidURL:
            return .invalidEndpoint("服务端地址无效")
        case .invalidPayload:
            return .badResponse
        case .networkUnavailable:
            return .unreachable
        case .serverError(let detail):
            return .serverError(500, detail)
        case .notImplemented:
            return .badResponse
        case .notFound:
            return .badResponse
        }
    }
}
