//
//  CitationDisplayAccessors.swift
//  ReadFlow
//
//  The single source of truth for what a citation renders (contract C27).
//
//  Why this file exists: both render sites used to decide independently, and
//  both used `if let snippet = citation.snippet`. Once `snippet` became
//  `String?` (C22/C23) that check became an **existence** gate, so a server
//  sending `{"snippet": ""}` — or `" "` — still constructed a real `Text`,
//  which reserves line height inside `VStack(spacing:)`. That is structurally
//  the same defect as `Text("")`, just reached through a different input.
//
//  The fix is a **content** gate, derived once and consumed everywhere, so the
//  view and the `--self-test render-row` outlet cannot disagree about what a
//  row displays: the self-test prints the very values the views pass to `Text`.
//
//  Deliberately not a free function anywhere in the call graph: these are
//  instance computed members, and the construction site stays a bare
//  `Citation(title: citationData.title, …)` pass-through with no `??` and no
//  helper — so "did the placeholder leak into the model?" is unambiguous.
//

import Foundation

/// The shared "is there anything to look at?" predicate.
///
/// This lives here, outside any type extension, because **two** display paths
/// need the same answer: `Citation` (AI answers) and `LocalSearchResult` (local
/// search hits). Before this existed each carried its own copy of the same
/// algorithm, which is the failure mode this codebase has been eliminating all
/// along: two implementations of one rule drift, and each one's tests only cover
/// itself, so the divergence is invisible.
///
/// It is deliberately not `private` to either call site — one rule, one
/// definition, two callers.
enum TextContent {

    /// Whether a string carries anything a reader can actually see.
    ///
    /// Two filters, both needed:
    ///   · `trimmingCharacters(in: .whitespacesAndNewlines)` removes ordinary
    ///     whitespace, NBSP (U+00A0) and zero-width space (U+200B);
    ///   · the general-category check removes format/control scalars that
    ///     survive trimming — word-joiner (U+2060), ZWNJ/ZWJ (U+200C/U+200D),
    ///     BOM (U+FEFF), Mongolian vowel separator (U+180E). A differential
    ///     probe showed trimming alone reports all of those as "has content",
    ///     which would render a visually blank `Text`.
    ///
    /// Known boundary (deliberate, not an oversight): a string consisting only
    /// of combining marks (e.g. a lone U+0301) counts as content. `.nonspacingMark`
    /// means "modifies the preceding character" — it is invisible only because
    /// there is no base character, which is not the same property as the
    /// format/control class. Closing it would mean excluding three more
    /// categories for an input that does not occur outside constructed cases;
    /// when a mark is attached to a real letter both behaviours agree.
    static func hasVisible(_ value: String) -> Bool {
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return false }
        return trimmed.unicodeScalars.contains { scalar in
            switch scalar.properties.generalCategory {
            case .format, .control:
                return false
            default:
                return true
            }
        }
    }
}

extension Citation {

    /// Placeholder shown when a citation has no usable title.
    ///
    /// A missing title is a legitimate state, so it is *derived here at render
    /// time* and never written back into the model. Storing a placeholder in
    /// `Citation.title` would make it indistinguishable from a real title and,
    /// because `Citation` is `Codable`, it would round-trip into the database as
    /// genuine content.
    ///
    /// Recorded decision, not drift: `LocalSearchResult` uses a different
    /// placeholder (「未知来源」) on purpose. A citation and a search hit are
    /// different objects — "the title is missing" and "the source is unknown"
    /// are different claims, so the two strings are allowed to differ even
    /// though the blankness rule they both rely on is shared.
    static let untitledPlaceholder = "无标题来源"

    /// Whether a string carries anything a reader can actually see.
    ///
    /// Thin wrapper over the shared predicate so `Citation` call sites read
    /// naturally while the *rule* stays defined in exactly one place. See
    /// `TextContent.hasVisible` for the two filters and the known boundary — it
    /// is not restated here, because a second copy of the reasoning is how the
    /// two implementations drifted in the first place.
    static func hasContent(_ value: String) -> Bool {
        TextContent.hasVisible(value)
    }

    /// Title to render. Never empty, for any input.
    ///
    /// Covers both `nil` and an empty/blank string: `title ?? placeholder` would
    /// render `""` for `{"title": ""}`, leaving a blank line where a title should
    /// be. The `guard let` also keeps every `!` out of this path, so the
    /// force-unwrap check (A30 mode 1) stays clean and the invariant is enforced
    /// by the compiler rather than by a runtime assumption about `hasContent`.
    var displayTitle: String {
        guard let title, Self.hasContent(title) else { return Self.untitledPlaceholder }
        return title
    }

    /// Whether the snippet should produce a `Text` at all.
    ///
    /// Exposed separately from `displaySnippet` so evidence can assert the
    /// *decision* (omit vs show) rather than only the string: a boolean is what
    /// makes "the view never constructs that `Text`" checkable, which
    /// `Text(snippet ?? "")` cannot express.
    var showsSnippet: Bool {
        guard let snippet else { return false }
        return Self.hasContent(snippet)
    }

    /// Snippet to render, or `nil` when the whole block should be omitted.
    var displaySnippet: String? {
        showsSnippet ? snippet : nil
    }

    /// URL to render, or `nil` when no link should appear.
    ///
    /// Same standard as the snippet: a blank or whitespace URL would otherwise
    /// draw an invisible link that still occupies `lineLimit(1)`'s full line —
    /// the same defect as an empty snippet, reached through another field.
    var displayURL: String? {
        guard let url, Self.hasContent(url) else { return nil }
        return url
    }
}

extension Citation {
    /// A single shared id used when the server sent a source id that is not a
    /// valid UUID.
    ///
    /// Purpose: avoid inventing data. The previous code produced a *fresh*
    /// random `UUID()` per occurrence, which looks exactly like a real source
    /// id and is persisted (the model is `Codable`). This sentinel can never
    /// match a row in `sources`, so the UI resolves it to「来源未知」, and its
    /// constancy makes it auditable — see `AISelfTest`'s citation cases.
    static let unresolvedSourceIDSentinel = UUID(
        uuidString: "00000000-0000-0000-0000-0000000000FF"
    )!

    /// Maps a server-provided source id string to a `Citation.sourceID`.
    static func unresolvedSourceID(for raw: String) -> UUID {
        UUID(uuidString: raw) ?? unresolvedSourceIDSentinel
    }
}
