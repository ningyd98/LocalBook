//
//  OfflineAIProvider.swift
//  ReadFlow
//
//  The `.offline` backend (contract §3.3, §6.2).
//
//  This provider makes **no network request at all** and still returns a
//  structurally complete `AIResponse`. It is why a standalone ReadFlow can
//  answer with no server and no network: the result is an honest degradation,
//  not an error and not a fabricated model output.
//
//  Two states share this backend and must stay distinguishable (contract C18 —
//  the user needs to know whether to "go configure" or "try later"):
//
//    · S1 未配置  — `isDegraded = false`. Nothing was degraded; the feature was
//                   never enabled. Badge:「未配置」(灰).
//    · S2 不可达  — `isDegraded = true`. A backend was tried and we fell back.
//                   Badge:「不可达」(黄).
//
//  The frozen copy for each lives here so the strings exist in exactly one
//  place (C17 freezes them verbatim for A15/A24–A26).
//

import Foundation

/// Frozen offline copy (contract C17). Not user-editable: verification asserts
/// these strings verbatim.
enum OfflineCopy {
    /// S1 — nothing configured. Must point at the settings panel.
    static let notConfigured =
        "未配置 AI 服务：请在「设置 → AI 供应商」中填写 API 端点，或连接 LocalBook 服务端。"

    /// S2 — configured but unreachable. `<原因>` is filled from the underlying
    /// `AIProviderError`; the wrapping sentence is frozen.
    static func unreachable(reason: String) -> String {
        "AI 服务当前不可达（\(reason)）。已切换为本地检索结果。"
    }
}

/// Why an answer is being served offline. Drives `isDegraded` and the copy.
enum OfflineReason: Equatable {
    /// S1: endpoint/key and LocalBook are both unconfigured.
    case notConfigured
    /// S2: a backend was configured and should have been reachable, but the
    /// call failed with a timeout/connection error and we fell back.
    case unreachable(reason: String)
}

/// Serves answers without touching the network.
final class OfflineAIProvider: AIProvider {
    let kind: AIProviderKind = .offline

    private let reason: OfflineReason

    /// Optional local-search hook. When supplied, hits are attached as
    /// citations so a degraded answer still points at real library content
    /// (contract §3.3 puts local hits in `citations`). It must be a *local*
    /// search — no network — or this provider's central guarantee is void.
    ///
    /// Exposed rather than private because the coordinator re-instantiates this
    /// provider to serve a *post-fallback* answer (S2) and needs to carry the
    /// hook across; without it, a degraded answer reached after a failed cloud
    /// call would lose its local citations while the S1 path kept them.
    let localSearchHook: ((String) -> [AICitation])?

    init(reason: OfflineReason = .notConfigured, localSearch: ((String) -> [AICitation])? = nil) {
        self.reason = reason
        self.localSearchHook = localSearch
    }

    /// Always available: it needs nothing.
    func isAvailable() async -> Bool { true }

    func ask(_ request: AIRequest) async throws -> AIResponse {
        // Local hits, when a hook exists, so a degradation is useful rather
        // than merely honest.
        let citations = localSearchHook?(request.query) ?? []

        let text: String
        let degraded: Bool
        switch reason {
        case .notConfigured:
            // S1: not a degradation — the feature was never enabled.
            text = OfflineCopy.notConfigured
            degraded = false
        case .unreachable(let why):
            text = OfflineCopy.unreachable(reason: why)
            degraded = true
        }

        return AIResponse(
            requestID: request.requestID,
            text: text,
            citations: citations,
            provider: .offline,
            isDegraded: degraded,
            model: nil
        )
    }
}
