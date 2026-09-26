//
//  LocalSearchResultDisplay.swift
//  ReadFlow
//
//  What a *local* search-results row renders (contract C25 / C29).
//
//  Why this file exists: `SearchResult` (the *server* search model in
//  LocalBookClient.swift) has non-optional `title`/`snippet`, so nothing in it
//  can express "the server never sent a source title". A local FTS5 hit can:
//  `ReaderDatabase.searchHighlights/searchNotes/searchReaderItems` return
//  `LocalSearchResult` whose `sourceTitle`, `sourceID`, `createdAt` and
//  `context` are all optional, and the join that fills `sourceTitle` legitimately
//  finds nothing when the FK row is gone or was never set.
//
//  So the row decides here, once, exactly like C27 does for citations: nil,
//  empty and whitespace-only all mean "nothing to show", and the decision is a
//  separate boolean (`displaysDate`) so evidence can assert the *decision*, not
//  merely the resulting string.
//
//  The `--self-test render-search-row` outlet prints these members — unchanged,
//  the same values a row view passes to `Text(...)` — for a result with
//  `sourceTitle == nil && createdAt == nil`, which is the state no server model
//  could represent and therefore the one the implementor must be told about.
//
//  Relationship to `Citation`: same content gate, deliberately duplicated rather
//  than shared while `CitationDisplayAccessors.swift` is owned by another
//  writer. `LocalSearchRowSelfTest` asserts the two gates agree across a corpus,
//  so they cannot drift silently; extracting one shared gate is the follow-up
//  noted in docs/standalone-build.md §5.7.
//

import Foundation

extension LocalSearchResult {

    /// Placeholder for a hit whose source could not be resolved.
    ///
    /// Derived at render time and never written back into the model: a
    /// placeholder stored in `sourceTitle` would be indistinguishable from a real
    /// title. Deliberately *not* the citation copy「无标题来源」— a citation is a
    /// quote of a source, a search hit is a row of content, and reusing one
    /// string for both would make the two states indistinguishable in evidence.
    /// Recorded decision, not drift: the two files agree that the *blankness*
    /// rule is shared (`TextContent.hasVisible`) while the *copy* is per-object.
    static let unknownSourcePlaceholder = "未知来源"

    /// Placeholder for a hit that carries no visible content.
    ///
    /// Should not occur for an FTS5 hit (the index only stores text), but the
    /// accessor cannot assume that: `content` is a plain `String`, and a blank
    /// one would draw a line of nothing inside `VStack(spacing:)`.
    static let emptyContentPlaceholder = "（无正文）"

    /// Whether a string carries anything a reader can actually see.
    ///
    /// Delegates to `TextContent.hasVisible`, the single shared definition of
    /// this rule. It lives there rather than here because `Citation`'s display
    /// path needs the same answer; two copies of the algorithm would drift, and
    /// each copy's tests would only cover itself. The filters and the known
    /// boundary (a lone combining mark counts as content) are documented at that
    /// definition rather than restated here.
    ///
    /// Kept as a named member so this file's call sites and its equivalence
    /// assertions still read naturally.
    static func hasVisibleContent(_ value: String) -> Bool {
        TextContent.hasVisible(value)
    }

    /// Title to render. Never empty, for any input.
    ///
    /// `guard let` rather than `sourceTitle ?? placeholder` so that
    /// `{"sourceTitle": ""}` renders the placeholder instead of an empty line;
    /// it also keeps every force-unwrap out of this path (A30 mode 1).
    var displayTitle: String {
        guard let sourceTitle, Self.hasVisibleContent(sourceTitle) else {
            return Self.unknownSourcePlaceholder
        }
        return sourceTitle
    }

    /// Whether the date line should produce a `Text` at all.
    ///
    /// `createdAt` is the value the FTS5 query returned, not a parsed date, so a
    /// blank or format-scalar string is reachable and must be treated as absent —
    /// `if let createdAt` would accept it and render a visually empty date.
    var displaysDate: Bool {
        guard let createdAt else { return false }
        return Self.hasVisibleContent(createdAt)
    }

    /// Date to render, or `nil` when the whole element should be omitted.
    var displayDate: String? {
        guard displaysDate, let createdAt else { return nil }
        // The store holds ISO-8601; a person should not read `2026-09-13T05:22:56Z`.
        // `humanised` returns the raw value unchanged when it cannot parse, so a
        // formatting failure shows the real string rather than a plausible wrong one.
        return RFDate.humanised(createdAt)
    }

    /// Content to render. Never empty, for any input.
    var displayContent: String {
        Self.hasVisibleContent(content) ? content : Self.emptyContentPlaceholder
    }

    /// Context line to render, or `nil` when it should be omitted entirely.
    ///
    /// Same standard as the date: a blank context would otherwise draw an
    /// invisible line that still occupies `lineLimit(2)`'s height.
    var displayContext: String? {
        guard let context, Self.hasVisibleContent(context) else { return nil }
        return context
    }

    /// Kind badge text. `LocalSearchResultKind.displayName` is frozen copy and
    /// never empty, so this is a pass-through kept for render-site symmetry.
    var displayKind: String {
        type.displayName
    }
}
