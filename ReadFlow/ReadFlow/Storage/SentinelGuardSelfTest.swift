//
//  SentinelGuardSelfTest.swift
//  ReadFlow
//
//  `--self-test roundtrip-sentinel-guard` — the negative control for A30.
//
//  Why a separate scenario: `roundtrip-nil` asserts that `nil` survives the
//  persistence path. On its own that is a command which can only ever print
//  PASS and exit 0, so "the assertion held" and "the assertion cannot fail" are
//  indistinguishable. This scenario judges the *judge*: it feeds the very
//  failure mode A30 exists to catch — a placeholder that has been persisted into
//  `title`/`snippet` — and requires the detector to flag it.
//
//  Exit code: 0 when the detector is calm on pristine input AND fires on every
//  poisoned input; non-zero when it misses one. A detector that always passes
//  fails here, which is the point.
//
//  Two directions, each run twice — once on values in memory, and once through
//  the real store (`addMessage` → `citationsJSON` BLOB → reopen on a separate
//  connection → decode). The persisted direction is the one that matters: the
//  harm A30 describes is the sentinel reaching the database, where it is later
//  indistinguishable from real content.
//

import Foundation
import GRDB

enum SentinelGuardSelfTest {

    /// The invariant `roundtrip-nil` relies on: a citation that was written with
    /// no title/snippet must read back with no title/snippet.
    ///
    /// Returns every violation found, so the caller can print them. An empty
    /// array means "nothing suspicious" — never "not checked".
    static func violations(in citations: [Citation]) -> [String] {
        var found: [String] = []
        for (index, citation) in citations.enumerated() {
            if let title = citation.title {
                found.append("citations[\(index)].title = \(quoted(title)) — expected nil")
            }
            if let snippet = citation.snippet {
                found.append("citations[\(index)].snippet = \(quoted(snippet)) — expected nil")
            }
            if let url = citation.url {
                found.append("citations[\(index)].url = \(quoted(url)) — expected nil")
            }
        }
        return found
    }

    static func run() -> (assertions: Int, failures: Int) {
        var assertions = 0
        var failures = 0

        func check(_ description: String, _ body: () throws -> Bool) {
            assertions += 1
            do {
                if try body() {
                    log("self-test: PASS  [roundtrip-sentinel-guard] \(description)")
                } else {
                    failures += 1
                    log("self-test: FAIL  [roundtrip-sentinel-guard] \(description)")
                }
            } catch {
                failures += 1
                log("self-test: FAIL  [roundtrip-sentinel-guard] \(description) — threw: \(error)")
            }
        }

        // The poisons are exactly what a naive `??` would persist: the C27
        // placeholder copy, and the empty / whitespace-only strings that C27
        // measured as "renders a blank line while occupying its height".
        let poisons: [(label: String, citation: Citation)] = [
            ("title = 无标题来源 (the C27 placeholder)",
             Citation(sourceID: UUID(), title: "无标题来源", snippet: nil, url: nil)),
            ("snippet = \"\" (the `?? \"\"` shape)",
             Citation(sourceID: UUID(), title: nil, snippet: "", url: nil)),
            ("snippet = \"   \" (blank but not empty)",
             Citation(sourceID: UUID(), title: nil, snippet: "   ", url: nil)),
            ("title = nil but snippet = \"无片段\"",
             Citation(sourceID: UUID(), title: nil, snippet: "无片段", url: nil)),
        ]

        let pristine = Citation(sourceID: UUID(), title: nil, snippet: nil, url: nil)

        // ---- direction 1: the detector must be silent on pristine input -----
        check("direction 1: the detector stays silent on a genuinely nil citation") {
            let found = violations(in: [pristine])
            log("self-test: info — pristine in memory: violations=\(found.count)")
            return found.isEmpty
        }

        // ---- direction 2: it must fire on every poison ----------------------
        // A detector that never complains passes direction 1; these are what
        // give it teeth. Each poison is a separate assertion so a partial
        // blindness names the input it missed.
        for poison in poisons {
            check("direction 2: the detector fires on \(poison.label)") {
                let found = violations(in: [poison.citation])
                log("self-test: info — \(poison.label) → violations=\(found.count) \(found.first ?? "")")
                return !found.isEmpty
            }
        }

        // ---- direction 3: the same, through the real store -------------------
        let storeURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("readflow-sentinel-guard-\(UUID().uuidString)", isDirectory: true)
            .appendingPathComponent("readflow.sqlite")
        try? FileManager.default.createDirectory(
            at: storeURL.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        defer { try? FileManager.default.removeItem(at: storeURL.deletingLastPathComponent()) }

        func roundTrip(_ citations: [Citation], content: String) throws -> [Citation] {
            let database = try ReaderDatabase(path: storeURL.path)
            let source = Source(id: UUID(), type: .web, title: "哨兵守卫语料", url: nil)
            try database.createSource(source)
            let highlight = Highlight(
                id: UUID(), sourceID: source.id, selectedText: "内容", contextBefore: "", contextAfter: ""
            )
            try database.createHighlight(highlight)
            let storage = ConversationStorage(database: database)
            let conversation = try storage.createConversation(
                highlightID: highlight.id, scope: .currentContent
            )
            let record = try storage.addMessage(
                conversationID: conversation.id,
                role: .assistant,
                content: content,
                citations: citations
            )
            guard let written = record.citations else { return [] }
            // A separate connection: this reads the BLOB, not the object graph.
            let reopened = try ReaderDatabase(path: storeURL.path)
            let readBack = try reopened.read { db in
                try ConversationMessageRecord
                    .filter(ConversationMessageRecord.Columns.conversationID == conversation.id)
                    .filter(ConversationMessageRecord.Columns.role == "assistant")
                    .fetchOne(db)
            }
            return readBack?.citations ?? written
        }

        check("direction 3: pristine input persists and comes back calm") {
            let readBack = try roundTrip([pristine], content: "干净样本")
            let found = violations(in: readBack)
            log("self-test: info — pristine through the store: count=\(readBack.count) violations=\(found.count)")
            return readBack.count == 1 && found.isEmpty
        }

        for poison in poisons {
            check("direction 3: the detector fires after \(poison.label) survives a real round trip") {
                let readBack = try roundTrip([poison.citation], content: "投毒样本")
                let found = violations(in: readBack)
                log("self-test: info — persisted \(poison.label) → count=\(readBack.count) "
                    + "violations=\(found.count) \(found.first ?? "")")
                // Both halves matter: the row must come back (otherwise "no
                // violations" could just mean "nothing was read") *and* be
                // flagged.
                return readBack.count == 1 && !found.isEmpty
            }
        }

        return (assertions, failures)
    }

    private static func quoted(_ value: String) -> String {
        "\"\(value)\" (\(value.count) chars)"
    }

    private static func log(_ message: String) {
        FileHandle.standardError.write(Data("[ReadFlow] \(message)\n".utf8))
    }
}
