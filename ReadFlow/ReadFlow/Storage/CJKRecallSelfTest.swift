//
//  CJKRecallSelfTest.swift
//  ReadFlow
//
//  `--self-test cjk-recall` — prints the whole C7 proof in one run, and exits
//  non-zero if any part of it fails.
//
//  Why it exists: C7's implementation deliberately leaves the FTS5 tokenizer at
//  `.porter(wrapping: .unicode61())` (see the block comment above
//  `v2_fulltext_search` in ReaderDatabase.swift). So the tokenizer string is not
//  evidence either way for C7, and reading only that line has twice produced the
//  conclusion "C7 is not implemented" — a false negative, because the fix lives
//  in the index/query transform and in the v4 rebuild. This outlet replaces that
//  inference with one command that measures recall and precision directly.
//
//  It covers both store histories, because only testing a fresh database gives a
//  false green:
//    · fresh store      — writing must be indexed through the transform;
//    · pre-v4 store     — rows that already exist must be **reindexed** by the
//                         migration. The case reproduces that state (raw FTS
//                         content, no transform triggers, v4 marker removed) and
//                         shows recall 0 → 1 across the upgrade.
//
//  Exit code: 0 when every expectation holds, 1 otherwise, 2 for usage.
//

import Foundation
import GRDB

enum CJKRecallSelfTest {

    /// A contrived but realistic corpus. Each entry is (text, must-recall, must-miss).
    private struct Fixture {
        let label: String
        let text: String
        let recall: [String]   // ≥2-character contiguous CJK substrings
        let miss: [String]     // non-contiguous, or absent
    }

    static func run() -> (assertions: Int, failures: Int) {
        var assertions = 0
        var failures = 0

        func check(_ description: String, _ body: () throws -> Bool) {
            assertions += 1
            do {
                if try body() {
                    log("self-test: PASS  [cjk-recall] \(description)")
                } else {
                    failures += 1
                    log("self-test: FAIL  [cjk-recall] \(description)")
                }
            } catch {
                failures += 1
                log("self-test: FAIL  [cjk-recall] \(description) — threw: \(error)")
            }
        }

        let workDir = FileManager.default.temporaryDirectory
            .appendingPathComponent("readflow-cjk-recall-\(UUID().uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: workDir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: workDir) }
        let storeURL = workDir.appendingPathComponent("readflow.sqlite")

        let fixtures: [Fixture] = [
            Fixture(label: "highlight",
                    text: "深度学习需要长期刻意练习才能形成能力",
                    recall: ["深度", "学习", "练习", "深度学习"],
                    miss: ["这 段", "中检", "深度习"]),
            Fixture(label: "note",
                    text: "长期刻意练习能显著提升记忆效果",
                    recall: ["练习", "记忆", "长期"],
                    // Both genuinely non-adjacent in the fixture text:
                    // 「长期刻意练习能显著提升…」 — 练习/显 are separated by 能,
                    // 长期/提 by 「刻意练习能显著」. The first draft used
                    // 「显著提」, which IS contiguous and was correctly recalled;
                    // the negative control caught the fixture, not the code.
                    miss: ["练习显", "长期提"]),
        ]

        // ---------------------------------------------------------------- fresh
        log("self-test: info — cjk-recall mode=fresh store=\(storeURL.path)")
        var highlightID: UUID?
        check("fresh store: the fixture can be written through the real API") {
            let database = try ReaderDatabase(path: storeURL.path)
            let source = Source(id: UUID(), type: .web, title: "C7 语料", url: nil)
            try database.createSource(source)
            let highlight = Highlight(
                id: UUID(), sourceID: source.id,
                selectedText: fixtures[0].text, contextBefore: "", contextAfter: ""
            )
            try database.createHighlight(highlight)
            highlightID = highlight.id
            try database.createNote(Note(id: UUID(), sourceID: source.id, content: fixtures[1].text))
            return true
        }

        for fixture in fixtures {
            let kind = fixture.label
            check("fresh store: \(kind) — every ≥2-character substring recalls") {
                let database = try ReaderDatabase(path: storeURL.path)
                let counts = try fixture.recall.map { query -> (String, Int) in
                    (query, try Self.count(kind: kind, query: query, database: database))
                }
                log("self-test: info — cjk-recall fresh \(kind) recall "
                    + counts.map { "\($0.0)=\($0.1)" }.joined(separator: " "))
                return counts.allSatisfy { $0.1 >= 1 }
            }
            check("fresh store: \(kind) — negative controls miss (no over-recall)") {
                let database = try ReaderDatabase(path: storeURL.path)
                let counts = try fixture.miss.map { query -> (String, Int) in
                    (query, try Self.count(kind: kind, query: query, database: database))
                }
                log("self-test: info — cjk-recall fresh \(kind) negatives "
                    + counts.map { "\($0.0)=\($0.1)" }.joined(separator: " "))
                return counts.allSatisfy { $0.1 == 0 }
            }
        }

        check("fresh store: the index really holds single-character tokens (the transform is what does it)") {
            let database = try ReaderDatabase(path: storeURL.path)
            // `write`, not `read`: creating the vocab table is a schema write
            // (measured: under `read` it fails with
            // "attempt to write a readonly database").
            let terms = try database.write { db -> [String] in
                // `fts5vocab` is a virtual-table module, not a table-valued
                // function (measured: calling it as one fails with
                // `no such table: fts5vocab`).
                try db.execute(sql: """
                    CREATE VIRTUAL TABLE IF NOT EXISTS __cjk_vocab_probe
                    USING fts5vocab(highlights_fts, 'row')
                    """)
                let terms = try String.fetchAll(
                    db, sql: "SELECT term FROM __cjk_vocab_probe LIMIT 400"
                )
                try db.execute(sql: "DROP TABLE IF EXISTS __cjk_vocab_probe")
                return terms
            }
            let cjkSingleCharacters = terms.filter { $0.count == 1 }
            log("self-test: info — cjk-recall index sample: \(terms.prefix(12).joined(separator: " ")) … "
                + "single-character terms=\(cjkSingleCharacters.count)")
            // "深度" as a whole token would mean the transform did not run.
            return cjkSingleCharacters.count > 5 && !terms.contains("深度学习需要长期刻意练习才能形成能力")
        }

        check("fresh store: English stemming still works (the CJK change did not break it)") {
            let database = try ReaderDatabase(path: storeURL.path)
            let source = Source(id: UUID(), type: .web, title: "C7 英文对照", url: nil)
            try database.createSource(source)
            let highlight = Highlight(
                id: UUID(), sourceID: source.id,
                selectedText: "running improves reading skill", contextBefore: "", contextAfter: ""
            )
            try database.createHighlight(highlight)
            let hits = try database.searchHighlightRecords(query: "run", limit: 10)
            return hits.contains { $0.id == highlight.id }
        }

        // ------------------------------------------------------------- upgraded
        // Reproduce a database written BEFORE v4: raw FTS content (whole CJK runs
        // as one token), no transform triggers, v4 unapplied. This is the state a
        // real user's library is in when they upgrade.
        log("self-test: info — cjk-recall mode=upgraded (rewinding the store to the pre-v4 shape)")
        check("upgraded: recall is broken BEFORE the reindex (so the case is not vacuous)") {
            let queue = try DatabaseQueue(path: storeURL.path)
            try queue.write { db in
                try db.execute(sql: "DELETE FROM grdb_migrations WHERE identifier = 'v4_cjk_fts'")
                for table in ["highlights", "notes", "reader_items"] {
                    for suffix in ["ai", "ad", "au"] {
                        try db.execute(sql: "DROP TRIGGER IF EXISTS \(table)_\(suffix)")
                    }
                }
                for table in ["highlights_fts", "notes_fts", "reader_items_fts"] {
                    try db.execute(sql: "DROP TABLE IF EXISTS \(table)")
                }
                try db.execute(sql: """
                    CREATE VIRTUAL TABLE highlights_fts USING fts5(
                        selectedText, contextBefore, contextAfter, tokenize='porter unicode61');
                    INSERT INTO highlights_fts(rowid, selectedText, contextBefore, contextAfter)
                    SELECT rowid, selectedText, contextBefore, contextAfter FROM highlights;
                    CREATE VIRTUAL TABLE notes_fts USING fts5(content, tokenize='porter unicode61');
                    INSERT INTO notes_fts(rowid, content) SELECT rowid, content FROM notes;
                    CREATE VIRTUAL TABLE reader_items_fts USING fts5(content, tokenize='porter unicode61');
                    INSERT INTO reader_items_fts(rowid, content) SELECT rowid, content FROM reader_items;
                    """)
            }
            // Raw MATCH, i.e. what a bare sqlite3 session sees. The app transforms
            // the query, so the honest pre-state is "the index cannot answer it".
            let raw = try queue.read { db in
                try Int.fetchOne(db, sql: """
                    SELECT COUNT(*) FROM highlights_fts WHERE highlights_fts MATCH '"深 度"'
                    """) ?? -1
            }
            log("self-test: info — cjk-recall pre-v4 raw MATCH '\"深 度\"' = \(raw) (0 is the defect being repaired)")
            return raw == 0
        }

        check("upgraded: reopening the store reindexes it and 2-character recall returns") {
            _ = try ReaderDatabase(path: storeURL.path)   // runs v4 → rebuildFTSIndexes
            let database = try ReaderDatabase(path: storeURL.path)
            let hits = try database.searchHighlightRecords(query: "深度", limit: 10)
            let negative = try database.searchHighlightRecords(query: "中检", limit: 10)
            // The id was captured BEFORE the store was rewound, so this is the
            // PRE-EXISTING row — not one written after the upgrade. Requirement:
            // "new store + new writes" must not be the evidence for the reindex.
            guard let highlightID else { return false }
            let recalled = hits.contains { $0.id == highlightID }
            log("self-test: info — cjk-recall after upgrade: PRE-EXISTING highlight id=\(highlightID.uuidString) "
                + "recalled=\(recalled ? "yes" : "NO") 深度=\(hits.count) 负例中检=\(negative.count)")
            return recalled && !negative.contains { $0.id == highlightID }
        }

        // "Rebuilt", not "emptied" and not "duplicated": the rebuild drops the FTS
        // tables and re-feeds every row through the transform, so the index must
        // come back 1:1 with its source table. A `VALUES('rebuild')`-style command,
        // by contrast, would read from a content table that does not exist here
        // (the `*_fts` tables are application-managed, with no `content=` source)
        // and could empty the index instead of rebuilding it.
        check("upgraded: the rebuilt index is 1:1 with its source table (not emptied, no duplicates)") {
            let database = try ReaderDatabase(path: storeURL.path)
            let counts = try database.read { db -> (fts: Int, source: Int, hits: Int) in
                let fts = try Int.fetchOne(db, sql: "SELECT COUNT(*) FROM highlights_fts") ?? -1
                let source = try Int.fetchOne(db, sql: "SELECT COUNT(*) FROM highlights") ?? -1
                let hits = try Int.fetchOne(db, sql: """
                    SELECT COUNT(*) FROM highlights_fts WHERE highlights_fts MATCH '"深 度"'
                    """) ?? -1
                return (fts, source, hits)
            }
            log("self-test: info — cjk-recall index integrity: fts rows=\(counts.fts) "
                + "source rows=\(counts.source) matching rows for '\"深 度\"'=\(counts.hits)")
            return counts.fts == counts.source && counts.fts > 0 && counts.hits == 1
        }

        // Idempotency, measured rather than asserted by inspection: rewind the store
        // to the pre-v4 shape a SECOND time and reopen — recall must return and the
        // 1:1 integrity must hold again. `INSERT OR REPLACE` semantics are not
        // needed because the rebuild drops the tables first; this check is what
        // proves the whole path can be re-run without leaving duplicates.
        check("upgraded: a second rewind→upgrade cycle is idempotent") {
            let queue = try DatabaseQueue(path: storeURL.path)
            try queue.write { db in
                try db.execute(sql: "DELETE FROM grdb_migrations WHERE identifier = 'v4_cjk_fts'")
                for table in ["highlights", "notes", "reader_items"] {
                    for suffix in ["ai", "ad", "au"] {
                        try db.execute(sql: "DROP TRIGGER IF EXISTS \(table)_\(suffix)")
                    }
                }
                // Second time the index is left INTACT (no table drop): that is the
                // case a duplicate-prone reindex would double up.
            }
            _ = try ReaderDatabase(path: storeURL.path)
            let database = try ReaderDatabase(path: storeURL.path)
            let counts = try database.read { db -> (fts: Int, source: Int) in
                let fts = try Int.fetchOne(db, sql: "SELECT COUNT(*) FROM highlights_fts") ?? -1
                let source = try Int.fetchOne(db, sql: "SELECT COUNT(*) FROM highlights") ?? -1
                return (fts, source)
            }
            let hits = try database.searchHighlightRecords(query: "深度", limit: 10)
            log("self-test: info — cjk-recall second cycle: fts rows=\(counts.fts) source rows=\(counts.source) "
                + "recall 深度=\(hits.count)")
            guard let highlightID else { return false }
            return counts.fts == counts.source && hits.contains { $0.id == highlightID }
        }

        check("upgraded: the triggers came back, so future writes stay indexed") {
            let queue = try DatabaseQueue(path: storeURL.path)
            let triggers = try queue.read { db in
                try Int.fetchOne(db, sql: """
                    SELECT COUNT(*) FROM sqlite_master
                    WHERE type='trigger' AND sql LIKE '%readflow_fts_transform%'
                    """) ?? -1
            }
            log("self-test: info — cjk-recall transform triggers after upgrade=\(triggers)")
            return triggers == 6
        }

        return (assertions, failures)
    }

    /// Counts hits for one query against one entity kind.
    private static func count(kind: String, query: String, database: ReaderDatabase) throws -> Int {
        switch kind {
        case "highlight": return try database.searchHighlightRecords(query: query, limit: 50).count
        case "note": return try database.searchNoteRecords(query: query, limit: 50).count
        default: return try database.searchReaderItemRecords(query: query, limit: 50).count
        }
    }

    private static func log(_ message: String) {
        FileHandle.standardError.write(Data("[ReadFlow] \(message)\n".utf8))
    }
}
