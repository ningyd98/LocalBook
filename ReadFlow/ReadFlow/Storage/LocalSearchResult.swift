//
//  LocalSearchResult.swift
//  ReadFlow
//
//  Contracts D2 / C25: the local full-text search gets its **own** result type.
//

import Foundation

/// One hit from the *local* full-text index.
///
/// Deliberately **not** `SearchResult`. That type mirrors the server's `/search`
/// response, whose field names and types (`entity_type`, `entity_id`, `title?`,
/// `snippet`, `score`, `url?`, outer `total`) match nothing we can produce
/// locally; the endpoint is also still an empty stub (`return SearchResponse(results: [], total: 0)`).
/// Reusing it would mean reworking it the moment the server side is implemented.
/// `SearchResult` itself is intentionally left untouched (contract D2).
///
/// Nullability rule (C9/D2): `sourceTitle` and `createdAt` are `String?` because
/// they can genuinely be unavailable (the owning `Source` may be gone; a date may
/// fail to format). Missing is reported as `nil` — never as `""` or a placeholder.
struct LocalSearchResult: Identifiable, Equatable {
    /// Primary key of the underlying record — `highlights.id`, `notes.id` or
    /// `reader_items.id`, depending on `type`.
    let id: UUID

    /// Which local table produced the hit.
    let type: LocalSearchResultKind

    /// The matched text itself: `highlights.selectedText`, `notes.content` or
    /// `reader_items.content`.
    let content: String

    /// Title of the owning `Source`, when that row is still reachable.
    let sourceTitle: String?

    /// `Source.id`, when known — lets the UI navigate to the owning source.
    let sourceID: UUID?

    /// Display-ready creation time (ISO-8601, stable across locales), or `nil`
    /// when it could not be produced. The UI may reformat it for display.
    let createdAt: String?

    /// Optional surrounding text, used for a richer excerpt. Highlights carry
    /// `contextBefore`/`contextAfter`; notes and reader items do not, so it is
    /// `nil` for them.
    let context: String?
}

/// Which local table a `LocalSearchResult` came from.
///
/// A real enum (rather than `String`) so the UI switches exhaustively and a new
/// local source cannot be silently ignored. `rawValue` matches the wording used
/// by the storage layer and by `ReaderItem.ItemType`.
enum LocalSearchResultKind: String, CaseIterable {
    case highlight
    case note
    case readerItem = "reader_item"

    /// Label for the UI.
    var displayName: String {
        switch self {
        case .highlight: return "高亮"
        case .note: return "笔记"
        case .readerItem: return "阅读条目"
        }
    }
}

// MARK: - Mapping from storage records

extension LocalSearchResult {
    /// ISO-8601 is used because it is locale-independent and round-trips; the UI
    /// is free to render it more humanly.
    private static let timestampFormatter = ISO8601DateFormatter()

    init(highlight: Highlight, sourceTitle: String? = nil, sourceID: UUID? = nil) {
        let context = [highlight.contextBefore, highlight.contextAfter]
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
            .joined(separator: " … ")

        self.init(
            id: highlight.id,
            type: .highlight,
            content: highlight.selectedText,
            sourceTitle: sourceTitle,
            sourceID: sourceID ?? highlight.sourceID,
            createdAt: Self.timestampFormatter.string(from: highlight.createdAt),
            context: context.isEmpty ? nil : context
        )
    }

    init(note: Note, sourceTitle: String? = nil, sourceID: UUID? = nil) {
        self.init(
            id: note.id,
            type: .note,
            content: note.content,
            sourceTitle: sourceTitle,
            sourceID: sourceID ?? note.sourceID,
            createdAt: Self.timestampFormatter.string(from: note.createdAt),
            context: nil
        )
    }

    init(readerItem: ReaderItem, sourceTitle: String? = nil, sourceID: UUID? = nil) {
        self.init(
            id: readerItem.id,
            type: .readerItem,
            content: readerItem.content,
            sourceTitle: sourceTitle,
            sourceID: sourceID ?? readerItem.sourceID,
            createdAt: Self.timestampFormatter.string(from: readerItem.createdAt),
            context: nil
        )
    }
}

// MARK: - Adapter: local search over the existing FTS queries

extension ReaderDatabase {
    /// Local-only full-text search across highlights, notes and reader items.
    ///
    /// This is the merged entry point the UI and the AI provider can call: it
    /// needs no server, no network and no configuration (contract C8 — the
    /// server's `/search` is an empty stub, so local search is the only path that
    /// can return real hits).
    ///
    /// Ordering: results are grouped by kind in the order highlights → notes →
    /// reader items, and each group keeps the relevance order GRDB's `rank`
    /// produces. Merging across tables by a single global score is not possible
    /// without a shared relevance model, so it is not pretended here.
    ///
    /// Relationship to the per-kind API: this **composes** `searchHighlights`,
    /// `searchNotes` and `searchReaderItems` — it does not reimplement them, so
    /// there is exactly one FTS code path. Callers that want a single kind should
    /// call those directly.
    ///
    /// - Note: published to the UI layer as the search entry point, and currently
    ///   has no caller (the UI may yet adopt either form). Kept deliberately
    ///   rather than deleted, so the published contract does not silently vanish;
    ///   if the team prefers a single shape, drop this and keep the per-kind
    ///   methods — nothing else references it.
    func searchLocal(query: String, limit: Int = 50) throws -> [LocalSearchResult] {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return [] }

        // `try` (not `try?`) on the source lookups: a failing lookup is a real
        // error and must surface. Degrading it to a nil title would be the same
        // kind of silent swallow that contract K5 forbids for the keychain. A
        // source row that is simply *absent* is not an error — the lookup returns
        // nil for that, and the title stays nil.
        var results: [LocalSearchResult] = []
        results += try searchHighlightRecords(query: trimmed, limit: limit).map { highlight in
            LocalSearchResult(highlight: highlight, sourceTitle: try sourceTitle(for: highlight.sourceID))
        }
        results += try searchNoteRecords(query: trimmed, limit: limit).map { note in
            LocalSearchResult(note: note, sourceTitle: try sourceTitle(for: note.sourceID))
        }
        results += try searchReaderItemRecords(query: trimmed, limit: limit).map { item in
            let title = try item.sourceID.map { try sourceTitle(for: $0) } ?? nil
            return LocalSearchResult(readerItem: item, sourceTitle: title)
        }

        return results
    }

    /// `Source.title` for a source id, or `nil` when the row is gone.
    ///
    /// Returning `nil` (rather than `""`) is the point: "unknown source" and
    /// "a source literally titled empty" are different states.
    private func sourceTitle(for id: UUID) throws -> String? {
        try getSource(id: id)?.title
    }
}
