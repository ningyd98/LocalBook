//
//  LocalSearchRowSelfTest.swift
//  ReadFlow
//
//  `--self-test render-search-row` — prints the strings a **local** search-results
//  row renders, for the states a server model cannot represent.
//
//  Why it lives in its own file instead of inside `AISelfTest`: that file has an
//  active writer (t9/ui-developer) and this session's rule is that a file gets one
//  writer at a time. The body is self-contained — its own counters and its own
//  `self-test:`-prefixed output — so it cannot conflict with a concurrent edit;
//  `AISelfTest.run` only has to call it and fold the two counters in.
//
//  Why the outlet is needed at all: the type it exercises is `LocalSearchResult`,
//  not `SearchResult`. `SearchResult` (LocalBookClient.swift:534, the *server*
//  model) has non-optional `title`/`snippet`, so no input to it can express
//  "the source title is absent". A local FTS5 hit can: `sourceTitle`,
//  `sourceID`, `createdAt` and `context` are all optional, and the join that
//  fills `sourceTitle` legitimately finds nothing. The render contract for that
//  state is defined by `LocalSearchResultDisplay` and printed here.
//
//  Exit code discipline is the caller's: this returns counts, and
//  `AISelfTest.run` returns 1 when any assertion failed, 2 for an unknown
//  scenario, 0 only when every assertion held.
//
//  `??` note (C26 / A34): the `??` below substitute "<omitted>" in log text and
//  provide assertion defaults. None is a `Citation(...)` / `AICitation(...)`
//  construction argument and none is on a path that writes to the store.
//

import Foundation

enum LocalSearchRowSelfTest {

    /// One constructed render input, plus what the row must show for it.
    private struct Row {
        let label: String
        let sourceTitle: String?
        let createdAt: String?
        let context: String?
        let content: String
        let kind: LocalSearchResultKind
    }

    /// Runs the scenario and returns `(assertions, failures)`.
    ///
    /// Counts rather than a verdict so the caller keeps a single exit-code path:
    /// `AISelfTest` already turns "any failure" into exit 1, and duplicating that
    /// decision here would create a second, divergent definition of pass.
    static func run() -> (assertions: Int, failures: Int) {
        var assertions = 0
        var failures = 0

        func check(_ description: String, _ body: () -> Bool) {
            assertions += 1
            if body() {
                log("self-test: PASS  [render-search-row] \(description)")
            } else {
                failures += 1
                log("self-test: FAIL  [render-search-row] \(description)")
            }
        }

        // The first row is the contract case: a hit whose source title is absent
        // *and* whose date is absent — the combination that made C25 necessary.
        // The blank and format-scalar rows exist because `nil` alone is not the
        // interesting input once the fields are optional: `""`, `" "` and a lone
        // word-joiner all pass an `if let` and would each draw a line of nothing.
        let rows: [Row] = [
            Row(label: "sourceTitle nil + createdAt nil",
                sourceTitle: nil, createdAt: nil, context: nil,
                content: "本地检索命中的正文", kind: .highlight),
            Row(label: "sourceTitle \"\" + createdAt \" \"",
                sourceTitle: "", createdAt: " ", context: "",
                content: "空白元数据", kind: .note),
            Row(label: "format scalars",
                sourceTitle: "\u{2060}", createdAt: "\u{FEFF}", context: "\u{200C}\u{200D}",
                content: "格式类标量", kind: .readerItem),
            Row(label: "full metadata",
                sourceTitle: "书 A", createdAt: "2026-09-13T10:00:00Z", context: "上下文",
                content: "完整元数据", kind: .highlight),
            Row(label: "blank content",
                sourceTitle: "书 B", createdAt: nil, context: nil,
                content: "   ", kind: .note),
        ]

        for row in rows {
            let result = LocalSearchResult(
                id: UUID(),
                type: row.kind,
                content: row.content,
                sourceTitle: row.sourceTitle,
                sourceID: nil,
                createdAt: row.createdAt,
                context: row.context
            )

            // The exact members a row view consumes — nothing here is recomputed
            // for the log, so evidence and UI cannot disagree.
            let title = result.displayTitle
            let showsDate = result.displaysDate
            let date = result.displayDate
            let content = result.displayContent
            let context = result.displayContext

            log("self-test: info — [\(row.label)] "
                + "kind=[\(result.displayKind)] "
                + "title=[\(title)] "
                + "displaysDate=\(showsDate) "
                + "date=[\(date ?? "<omitted>")] "
                + "content=[\(content)] "
                + "context=[\(context ?? "<omitted>")]")

            check("\(row.label): renders a non-empty visible title") {
                !title.isEmpty && LocalSearchResult.hasVisibleContent(title)
            }

            // Decision and value must agree, and a shown date must be the date —
            // an accessor that displayed a substitute would pass a bare
            // non-empty check while showing the wrong thing.
            //
            // The expected value is the *mapping*, not the raw string: the row
            // renders a human date, so pinning the raw ISO-8601 literal here made
            // this assertion fail the moment the display layer began formatting.
            // `RFDate.humanised` is that mapping's single source, so comparing
            // against it also catches a second, divergent formatter.
            check("\(row.label): displaysDate agrees with displayDate") {
                guard showsDate == (date != nil) else { return false }
                guard let date, let raw = row.createdAt else { return true }
                return date == RFDate.humanised(raw)
            }

            check("\(row.label): neither title nor content is ever blank") {
                !content.isEmpty && LocalSearchResult.hasVisibleContent(content)
            }

            check("\(row.label): optional context is omitted exactly when blank") {
                (context != nil) == (row.context.map(LocalSearchResult.hasVisibleContent) ?? false)
            }

            // The failure this guards is a botched interpolation reaching the
            // screen as "Optional(\"x\")" or "nil" — unreadable, and impossible
            // to catch visually in this environment.
            check("\(row.label): no rendered value looks like an unformatted optional") {
                let rendered = [title, content, date ?? "", context ?? ""]
                return !rendered.contains { value in
                    value.contains("Optional(") || value == "nil"
                        || value.contains("<null>") || value.contains("(null)")
                }
            }
        }

        // The contract case, asserted by value rather than by shape, so a change
        // to the placeholder copy fails here instead of silently shipping.
        check("nil source title renders the frozen placeholder") {
            let result = LocalSearchResult(
                id: UUID(), type: .highlight, content: "正文",
                sourceTitle: nil, sourceID: nil, createdAt: nil, context: nil
            )
            log("self-test: info — nil-metadata row title=[\(result.displayTitle)] "
                + "date=[\(result.displayDate ?? "<omitted>")]")
            return result.displayTitle == LocalSearchResult.unknownSourcePlaceholder
                && result.displayDate == nil
                && !result.displaysDate
        }

        // nil / "" / whitespace / format-scalar must be *indistinguishable* in
        // output. Asserting the equality is what makes a regression in any single
        // one of them fail, which four separate outcome checks would not.
        check("nil / \"\" / whitespace / format scalars render identically") {
            func row(_ title: String?, _ date: String?) -> LocalSearchResult {
                LocalSearchResult(
                    id: UUID(), type: .highlight, content: "正文",
                    sourceTitle: title, sourceID: nil, createdAt: date, context: nil
                )
            }
            let states: [(String?, String?)] = [
                (nil, nil), ("", ""), (" \t\n", "   "), ("\u{2060}", "\u{FEFF}"),
            ]
            let rendered = states.map { pair -> [String] in
                let result = row(pair.0, pair.1)
                return [result.displayTitle, result.displayDate ?? "<omitted>",
                        result.displayContext ?? "<omitted>"]
            }
            return rendered.dropFirst().allSatisfy { $0 == rendered[0] }
        }

        // Differential check against C27's gate. The two implementations are
        // intentionally separate files (one writer per file), so this is what
        // keeps them from drifting: if either gate changes its standard, the
        // corpus below disagrees and this assertion fails.
        check("content gate agrees with C27's Citation.hasContent on a corpus") {
            let corpus = [
                "", " ", "\t\n", "\u{00A0}", "\u{200B}", "\u{2060}", "\u{200C}", "\u{FEFF}",
                "\u{180E}", "x", "真", "e\u{0301}", "\u{0301}", " x ", "۰", "　",
            ]
            var disagreements: [String] = []
            for value in corpus {
                let mine = LocalSearchResult.hasVisibleContent(value)
                let c27 = Citation.hasContent(value)
                if mine != c27 {
                    disagreements.append("[\(value.unicodeScalars.map { String(format: "U+%04X", $0.value) }.joined())]")
                }
            }
            // The mapping is judged directly, because a row fixture can only show
            // that *some* string came out. These are the two ways a formatter fails a
            // reader: echoing machine output, or inventing a plausible value for input
            // it could not understand.
            check("date mapping: a parseable timestamp is not echoed as raw ISO-8601") {
                let shown = RFDate.humanised("2026-09-13T10:00:00Z")
                return !shown.isEmpty && !shown.contains("T") && !shown.hasSuffix("Z")
            }
            check("date mapping: an unparseable value is returned unchanged, never invented") {
                RFDate.humanised("not a date") == "not a date" && RFDate.humanised("") == ""
            }
            check("date mapping: a same-day timestamp reads as 今天") {
                let now = Date()
                return RFDate.humanised(ISO8601DateFormatter().string(from: now), now: now).hasPrefix("今天")
            }

            log("self-test: info — gate corpus=\(corpus.count) disagreements=\(disagreements.count)"
                + (disagreements.isEmpty ? "" : " \(disagreements.joined(separator: " "))"))
            return disagreements.isEmpty
        }

        return (assertions, failures)
    }

    private static func log(_ message: String) {
        FileHandle.standardError.write(Data("[ReadFlow] \(message)\n".utf8))
    }
}
