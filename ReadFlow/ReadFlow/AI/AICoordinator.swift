//
//  AICoordinator.swift
//  ReadFlow
//
//  Chooses between the three backends and records how it chose (contract C5).
//
//  The selection order is frozen and, importantly, **observable**: the caller
//  learns which backend answered and whether the answer was a degradation, and
//  every fallback actually taken is recorded. That record is the difference
//  between "the cloud failed and we degraded" and "the cloud was never
//  consulted", which are indistinguishable from the response alone.
//
//  What this type deliberately does **not** do: retry. C5 freezes the fallback
//  order, and an automatic retry would both defeat "no silent fallback" and risk
//  billing the user twice for one question. There is exactly one `ask` call per
//  candidate provider per request; a retry is a user action.
//

import Foundation

/// User-supplied cloud configuration (contract §6.4.1).
///
/// Plain values rather than a store: the coordinator must stay testable and must
/// never read the keychain itself. Whoever builds this is responsible for having
/// sourced the key from the right place.
struct CloudConfiguration: Equatable {
    let baseURL: String
    let model: String
    let apiKey: String
    let enabled: Bool
    /// Optional request timeout (contract C6 allows 10...60s).
    ///
    /// `nil` means "use `CloudAIProvider.defaultTimeout`", which preserves the
    /// behaviour of every caller that predates this field. The range is enforced
    /// by `CloudAIProvider.init` via `precondition`, not clamped here: silently
    /// correcting an out-of-range value would hide a configuration bug.
    let timeout: TimeInterval?

    init(
        baseURL: String,
        model: String,
        apiKey: String,
        enabled: Bool = true,
        timeout: TimeInterval? = nil
    ) {
        self.baseURL = baseURL
        self.model = model
        self.apiKey = apiKey
        self.enabled = enabled
        self.timeout = timeout
    }

    /// Cloud is usable only when the user enabled it *and* supplied both an
    /// address and a key. `enabled == false` is treated as S1 ("not
    /// configured"), per contract §6.4.1 — a disabled endpoint must never be
    /// dialled, and the user must not be told their setup is missing either.
    var isUsable: Bool {
        enabled
            && !baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }
}

/// A fallback that actually happened.
struct FallbackRecord: Equatable {
    let from: AIProviderKind
    let to: AIProviderKind
    let reason: String
}

/// Result of one `ask`, including how the backend was chosen.
struct AIOutcome {
    let response: AIResponse
    /// Empty unless a fallback was taken. Non-empty is what makes "we degraded"
    /// distinguishable from "we never tried" — and it is the observable C5.1
    /// requires ("必须记录一次回落").
    let fallbacks: [FallbackRecord]
}

final class AICoordinator {
    private let localbook: AIProvider?
    private let cloud: AIProvider?
    private let offline: OfflineAIProvider
    private let localSearchHook: ((String) -> [AICitation])?

    /// - Parameters:
    ///   - localbook: `nil` when no server address is configured.
    ///   - cloud: `nil` when the cloud configuration is unusable.
    ///   - offline: the terminal backend; always present.
    init(localbook: AIProvider?, cloud: AIProvider?, offline: OfflineAIProvider) {
        self.localbook = localbook
        self.cloud = cloud
        self.offline = offline
        self.localSearchHook = offline.localSearchHook
    }

    /// Builds a coordinator from user configuration.
    ///
    /// A backend is only constructed when it is actually usable, so an
    /// unconfigured or disabled endpoint cannot be reached by accident — that is
    /// what makes S1 a real state rather than an error case.
    static func make(
        cloudConfiguration: CloudConfiguration?,
        localbookProvider: AIProvider?,
        localSearch: ((String) -> [AICitation])? = nil
    ) -> AICoordinator {
        var cloudProvider: AIProvider?
        if let config = cloudConfiguration, config.isUsable,
           let normalized = try? EndpointNormalizer.normalize(config.baseURL) {
            // An absent timeout keeps `CloudAIProvider.defaultTimeout`, so
            // existing callers behave exactly as before.
            cloudProvider = config.timeout.map { timeout in
                CloudAIProvider(baseURL: normalized, apiKey: config.apiKey, model: config.model, timeout: timeout)
            } ?? CloudAIProvider(baseURL: normalized, apiKey: config.apiKey, model: config.model)
        }
        return AICoordinator(
            localbook: localbookProvider,
            cloud: cloudProvider,
            offline: OfflineAIProvider(reason: .notConfigured, localSearch: localSearch)
        )
    }

    /// Runs the frozen selection order and reports the answer plus any fallbacks.
    func ask(_ request: AIRequest) async throws -> AIOutcome {
        // Web evidence is provided by LocalBook's configured search service.
        // Cloud and offline providers cannot claim to have searched the web.
        if request.scope == .web {
            guard let localbook, await localbook.isAvailable() else {
                throw AIProviderError.serverError(501, "请先连接 LocalBook 并配置联网搜索")
            }
            return AIOutcome(response: try await localbook.ask(request), fallbacks: [])
        }
        var fallbacks: [FallbackRecord] = []
        // Whether we reached offline *after trying* something. This is the S1/S2
        // distinction the UI must be able to show (C18), and it is derived from
        // what actually happened rather than from configuration state.
        var triedAndFailed: String?

        // 1. LocalBook, when it exists and reports itself available.
        if let localbook, await localbook.isAvailable() {
            do {
                let response = try await localbook.ask(request)
                return AIOutcome(response: response, fallbacks: fallbacks)
            } catch let error as AIProviderError {
                // Authorization and protocol problems must not be swallowed:
                // they are actionable, and falling back would hide them.
                if Self.mustSurface(error) { throw error }
                triedAndFailed = error.localizedDescription
                fallbacks.append(
                    FallbackRecord(
                        from: .localbook,
                        to: cloud == nil ? .offline : .cloud,
                        reason: error.localizedDescription
                    )
                )
            }
        }

        // 2. Cloud, when configured.
        if let cloud {
            do {
                let response = try await cloud.ask(request)
                return AIOutcome(response: response, fallbacks: fallbacks)
            } catch let error as AIProviderError {
                if Self.mustSurface(error) { throw error }
                triedAndFailed = error.localizedDescription
                fallbacks.append(
                    FallbackRecord(from: .cloud, to: .offline, reason: error.localizedDescription)
                )
            }
        }

        // 3. Terminal: a structured offline answer. Never a throw, never nil.
        // A fresh provider is built because the reason only becomes known here,
        // while the local-search hook is carried across so a post-fallback (S2)
        // answer keeps the same local citations an S1 answer would have had.
        let terminal = OfflineAIProvider(
            reason: triedAndFailed.map { .unreachable(reason: $0) } ?? .notConfigured,
            localSearch: localSearchHook
        )
        let response = try await terminal.ask(request)
        return AIOutcome(response: response, fallbacks: fallbacks)
    }

    // MARK: - Helpers

    /// Errors that must reach the caller rather than trigger a fallback
    /// (contract C5.2 / C17 exception list).
    ///
    /// `.serverError` belongs here too — it includes 429 rate limiting, which is
    /// a server-side condition the user can act on ("try later"), not a reason to
    /// silently serve an offline answer that would look like a real one.
    static func mustSurface(_ error: AIProviderError) -> Bool {
        switch error {
        case .unauthorized, .badResponse, .invalidEndpoint, .serverError:
            return true
        case .notConfigured, .timeout, .unreachable:
            return false
        }
    }
}
