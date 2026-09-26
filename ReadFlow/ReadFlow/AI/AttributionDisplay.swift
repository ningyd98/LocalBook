//
//  AttributionDisplay.swift
//  ReadFlow
//
//  How a message's AI attribution should be presented (contract C27/C29/C33).
//
//  This is a pure function on purpose. Attribution presentation was the one UI
//  decision that could not be observed in this environment (no screenshots, no
//  accessibility tree), so it is expressed as a value transformation that a
//  command-line self-test can assert exactly — and the view is reduced to a
//  consumer of the result. If the decision lived inside a `View` body, the only
//  available evidence would be "it compiles".
//
//  The ordering here is load-bearing and is asserted by `--self-test
//  attribution-nil`:
//
//      provider == nil OR isDegraded == nil  →  .unknown
//
//  Both columns are checked, not just `provider`. `ADD COLUMN` makes them
//  independently nullable, so `provider = "cloud", isDegraded = NULL` is
//  reachable — and a predicate that only looked at `provider` would render a
//  live, confident badge for a row whose degradation state is unknown.
//

import Foundation
// `SwiftUI` for `Color`: the badge colour is frozen by §6.3 alongside its copy,
// and keeping the two together stops them drifting apart.
import SwiftUI

/// What to show for one message's attribution.
enum AttributionDisplay: Equatable {
    /// No usable attribution. Rendered as 「产出方未知」 with **no** badge, and
    /// `isDegraded` is not consulted at all in this case.
    case unknown
    /// S1 — nothing configured. Not a degradation: the feature was never
    /// enabled, so the user needs "go configure", not "retry".
    case unconfigured
    /// S2 — configured but unreachable; we fell back. Carries the reason.
    case degradedFallback(reason: String)
    /// A backend answered normally.
    case provided(kind: AIProviderKind, model: String?)
}

extension AttributionDisplay {
    /// Badge text, or `nil` when no badge should be drawn.
    ///
    /// Frozen by contract §6.3: 「未配置」(灰) / 「不可达」(黄) / provider name
    /// (绿). The unknown case deliberately has no badge — an unknown producer is
    /// not the same claim as "offline", and drawing the offline badge would
    /// assert something the data does not say.
    var badgeText: String? {
        switch self {
        case .unknown:
            return nil
        case .unconfigured:
            return "未配置"
        case .degradedFallback:
            return "不可达"
        case .provided(let kind, _):
            return kind.rawValue
        }
    }

    /// Badge colour per §6.3. Kept beside `badgeText` rather than in the view so
    /// the colour cannot drift from the copy it is paired with.
    var badgeColor: Color {
        switch self {
        case .unknown:
            // Never drawn (there is no badge text), but the case stays explicit
            // so adding a state cannot silently inherit the wrong colour.
            return .gray
        case .unconfigured:
            return .gray
        case .degradedFallback:
            return .yellow
        case .provided:
            return .green
        }
    }

    /// The sentence shown alongside the badge.
    ///
    /// C18 requires every state to be explained, including the unknown one:
    /// 「产出方未知」 is a statement, whereas a bare row would make "we have no
    /// attribution" indistinguishable from "the badge failed to render".
    var explanation: String {
        switch self {
        case .unknown:
            return "产出方未知"
        case .unconfigured:
            return "未配置 AI 服务"
        case .degradedFallback:
            return "已回落离线"
        case .provided(let kind, _):
            return "由 \(kind.rawValue) 生成"
        }
    }

    /// Whether this state is a degradation. Only S2 is.
    var isDegraded: Bool {
        if case .degradedFallback = self { return true }
        return false
    }
}

/// A message's attribution.
///
/// Two "no producer" shapes are kept apart on purpose:
///   · `nil` for a message no backend ever produced — one the user wrote, or an
///     assistant reply that has not been persisted yet. The row draws nothing,
///     because the message never asserted a producer.
///   · `.unknown` for an assistant reply that *should* carry attribution but
///     does not: a row written before migration `v5_conversation_attribution`,
///     or a half-written row (`provider` set, `isDegraded` NULL). Here the row
///     must say 「产出方未知」 — staying silent would let "we have no
///     attribution" pass for "no attribution was expected", and the absence is
///     itself the information the user needs.
///
/// Where both columns *are* present, evaluation goes through the same
/// conjunctive predicate `--self-test attribution-nil` asserts; nothing here
/// re-implements it.
extension ConversationMessage {
    var attributionDisplay: AttributionDisplay? {
        guard role == .assistant else { return nil }
        return ReadFlow.attributionDisplay(
            provider: provider.flatMap(AIProviderKind.init(rawValue:)),
            isDegraded: isDegraded
        )
    }
}

/// The decision itself.
///
/// - Parameters:
///   - provider: `nil` for rows written before `v5_conversation_attribution`.
///   - isDegraded: `nil` for the same rows. Must be `Bool?` in the model — a
///     non-optional `Bool` forces a `?? false` fallback somewhere, and that
///     fallback is invisible to every SQL assertion because the column stays
///     NULL in the database.
func attributionDisplay(
    provider: AIProviderKind?,
    isDegraded: Bool?,
    reason: String = ""
) -> AttributionDisplay {
    // Both halves are required. Checking only `provider` would treat a
    // half-attributed row as known and render a badge that carries no
    // degradation information.
    guard let kind = provider, let degraded = isDegraded else {
        return .unknown
    }

    switch kind {
    case .offline:
        // S1 vs S2 is exactly the `isDegraded` distinction, and C18 requires the
        // two to stay distinguishable: one points at settings, the other at retry.
        return degraded ? .degradedFallback(reason: reason) : .unconfigured
    case .localbook, .cloud:
        return .provided(kind: kind, model: nil)
    }
}
