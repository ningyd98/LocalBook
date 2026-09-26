//
//  ReaderDatabase.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import GRDB

/// Where the shared store actually ended up.
enum ReaderDatabaseStorageMode: Equatable {
    /// A real SQLite file on disk.
    case onDisk(URL)
    /// Nothing could be opened on disk, so the app runs against a throwaway
    /// in-memory store. Reads and writes work, but nothing survives a relaunch.
    case inMemory(reason: String)
}

/// What happened while locating the shared store.
///
/// Why this exists: the degradation to an in-memory store used to be announced
/// only by `print`, i.e. on stdout. A launched `.app` has no visible stdout, and
/// the app has no UI surface for it — so a user whose store could never be
/// created saw *no results in search* and nothing else: no error, no file, no
/// readable message. A product-level defect, not merely a verification problem.
///
/// The failure is now carried in three places, weakest last:
///   1. `ReaderDatabase.lastInitDiagnostic` — readable from any code path
///      (the `--self-test db-init` scenario asserts on it);
///   2. stderr, so a shell-launched run or a captured log sees it;
///   3. an appended line in a fixed-path log file (`logFile` records which one
///      was actually writable), so it survives a LaunchServices launch.
struct ReaderDatabaseInitDiagnostic: Equatable {
    /// Every location that was tried, in priority order.
    let attemptedPaths: [String]
    /// One `path: reason` line per location that failed.
    let failures: [String]
    /// Where the store ended up.
    let mode: ReaderDatabaseStorageMode
    /// The log file the diagnostic was appended to, if one could be written.
    let logFile: String?

    /// True when nothing persisted — the state that must never be silent.
    var didDegradeToMemory: Bool {
        if case .inMemory = mode { return true }
        return false
    }

    /// One-line summary suitable for a log, a banner, or an assertion message.
    ///
    /// The failures are included whenever there were any — also when the store
    /// ended up on disk. Otherwise a run whose requested path was rejected would
    /// log only "ready at <somewhere else>", which reads as success and hides the
    /// very thing a user needs to see.
    var summary: String {
        let failureSuffix = failures.isEmpty
            ? ""
            : " — \(failures.count) location(s) failed first: " + failures.joined(separator: " | ")
        switch mode {
        case .onDisk(let url):
            return "local store ready at \(url.path) (schema migrated)" + failureSuffix
        case .inMemory(let reason):
            return "⚠️ NO PERSISTENT STORE — running in memory, nothing will be saved. Tried: "
                + attemptedPaths.joined(separator: ", ")
                + failureSuffix
                + " | reason: \(reason)"
        }
    }
}

class ReaderDatabase {
    private let dbQueue: DatabaseQueue

    /// How the store was opened. Surfaced so the UI (and the smoke tests) can
    /// tell the user that local persistence is degraded instead of silently
    /// pretending everything is fine.
    let storageMode: ReaderDatabaseStorageMode

    // MARK: - Initialization

    init(path: String) throws {
        dbQueue = try DatabaseQueue(path: path, configuration: Self.makeConfiguration())
        storageMode = .onDisk(URL(fileURLWithPath: path))
        try migrator.migrate(dbQueue)
    }

    /// In-memory store. Used as the degradation path when no writable location
    /// is available; also handy for tests and for `READFLOW_DB_PATH=:memory:`.
    init(inMemory reason: String = "explicitly requested") throws {
        dbQueue = try DatabaseQueue(configuration: Self.makeConfiguration())
        storageMode = .inMemory(reason: reason)
        try migrator.migrate(dbQueue)
    }

    // MARK: - FTS text preparation (CJK)

    /// SQL function name shared by the FTS sync triggers and the queries, so
    /// indexing and searching can never drift apart.
    static let ftsTransformFunction = "readflow_fts_transform"

    /// Registers the CJK-aware index transform on a database connection.
    ///
    /// Why this exists: the FTS tables use the stock `unicode61` tokenizer,
    /// which treats a whole run of CJK as a *single* token. Measured against
    /// the shipped schema: for `阅读是一种能力`, `MATCH '阅读'` and
    /// `MATCH '能力'` both return 0 rows, while English stemming works. The
    /// app's content is mostly Chinese, so local search was effectively dead.
    ///
    /// The fix keeps the tokenizer and changes *what gets indexed*: CJK runs
    /// are rewritten as space-separated single characters. Queries run through
    /// the same function and are then matched as a quoted phrase, which keeps
    /// the characters adjacent (`"阅 读"` cannot match inside `阅览室`, while a
    /// plain AND of the same characters can — measured false positive).
    static func registerFTSHelpers(on database: Database) {
        database.add(function: DatabaseFunction(ftsTransformFunction, argumentCount: 1) { values in
            guard let raw = String.fromDatabaseValue(values[0]) else { return nil }
            return cjkIndexTransform(raw)
        })
    }

    /// A configuration that installs the CJK transform on every connection.
    static func makeConfiguration() -> Configuration {
        var configuration = Configuration()
        configuration.prepareDatabase { db in
            registerFTSHelpers(on: db)
        }
        return configuration
    }

    /// Rewrites CJK runs as space-separated single characters and leaves every
    /// other character untouched. Applied to raw column values at index time,
    /// and to query text before building the MATCH pattern.
    ///
    /// Chosen over CJK bigrams because bigrams cannot serve a 1-character query
    /// (measured: `MATCH '中'` returns 0 against a bigram index), while this
    /// form also supports them. Recall is restored by joining the segmented
    /// characters into a quoted FTS5 phrase, which additionally preserves
    /// adjacency — a bigram index would match `阅读` inside `阅览` only if the
    /// bigram happened to exist, whereas the phrase `"阅 读"` cannot.
    static func cjkIndexTransform(_ text: String) -> String {
        guard !text.isEmpty else { return text }
        var out = String()
        out.reserveCapacity(text.count * 2)

        var pendingSpace = false
        for character in text {
            if character.isCJK {
                if pendingSpace, !out.isEmpty { out.append(" ") }
                out.append(character)
                pendingSpace = true
            } else {
                out.append(character)
                pendingSpace = false
            }
        }
        return out
    }

    /// Builds the FTS5 MATCH pattern for a user query.
    ///
    /// Each whitespace-separated run is quoted so FTS5 treats it as a phrase.
    /// Quoting is what makes the segmented characters adjacent again: the index
    /// holds `阅 读` as two tokens, and `"阅 读"` requires them in that order
    /// and position. Non-CJK runs are quoted too, so English queries degrade to
    /// phrase search rather than token search — stemming still applies to the
    /// tokens inside the phrase.
    ///
    /// Verified alternatives: joining the segments as a plain AND (path A in
    /// the contract) loses adjacency and produced a measured false positive;
    /// CJK bigrams lose 1-character queries.
    static func ftsPattern(for query: String) -> FTS5Pattern? {
        let segmented = cjkIndexTransform(query)
        guard !segmented.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        return FTS5Pattern(matchingPhrase: segmented)
    }

    // MARK: - Shared instance

    /// Opened once, on first use, and shared by the app's long-lived
    /// singletons (`ConversationStorage`, `SyncEngine`, `FloatingToolbar`).
    ///
    /// Opening a file store can legitimately fail — read-only home directory,
    /// sandbox without file access, a corrupt database. A launch must not trap
    /// on that, so after every candidate location fails the fallback is an
    /// in-memory store plus a loud log line and `storageMode == .inMemory`.
    static let shared: ReaderDatabase = makeShared()

    /// On-disk locations to try, in priority order:
    /// 1. `READFLOW_DB_PATH` when set (offline smoke tests point this at the workspace);
    /// 2. `~/Library/Application Support/ReadFlow/readflow.sqlite` (normal installs);
    /// 3. `<temp>/ReadFlow/readflow.sqlite`, which stays writable inside
    ///    restricted sandboxes that deny `~/Library`.
    /// True when the user asked for an in-memory store via the environment.
    ///
    /// This must be decided on the *raw* string: `URL(fileURLWithPath: ":memory:")`
    /// yields an absolute path like `/private/tmp/:memory:`, so comparing
    /// `url.path == ":memory:"` later can never hold and the branch is dead code
    /// that silently creates a real file named `:memory:`.
    static var overrideRequestsInMemory: Bool {
        shouldUseInMemorySharedStore(
            arguments: CommandLine.arguments,
            databasePathOverride: ProcessInfo.processInfo.environment["READFLOW_DB_PATH"]
        )
    }

    static func shouldUseInMemorySharedStore(
        arguments: [String], databasePathOverride: String?
    ) -> Bool {
        if let databasePathOverride, !databasePathOverride.isEmpty {
            return databasePathOverride == ":memory:"
        }
        return arguments.contains("--self-test")
    }

    static var candidateStoreURLs: [URL] {
        var urls: [URL] = []

        if let override = ProcessInfo.processInfo.environment["READFLOW_DB_PATH"],
           !override.isEmpty,
           override != ":memory:" {
            urls.append(URL(fileURLWithPath: override))
        }

        if let base = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)
            .first {
            urls.append(
                base.appendingPathComponent("ReadFlow", isDirectory: true)
                    .appendingPathComponent("readflow.sqlite")
            )
        }

        urls.append(
            URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
                .appendingPathComponent("ReadFlow", isDirectory: true)
                .appendingPathComponent("readflow.sqlite")
        )

        return urls
    }

    /// Primary on-disk store location (see `candidateStoreURLs`).
    static var defaultStoreURL: URL {
        candidateStoreURLs[0]
    }

    private static func makeShared() -> ReaderDatabase {
        var failures: [String] = []
        let attempted = candidateStoreURLs

        if overrideRequestsInMemory {
            do {
                let database = try ReaderDatabase(inMemory: "headless self-test or READFLOW_DB_PATH=:memory:")
                publishInitDiagnostic(
                    attempted: attempted, failures: [], mode: database.storageMode
                )
                return database
            } catch {
                fatalError("[ReaderDatabase] in-memory store unavailable: \(error)")
            }
        }

        for url in attempted {
            do {
                try FileManager.default.createDirectory(
                    at: url.deletingLastPathComponent(),
                    withIntermediateDirectories: true
                )
                let database = try ReaderDatabase(path: url.path)
                print("[ReaderDatabase] local store ready at \(url.path) (schema migrated)")
                publishInitDiagnostic(
                    attempted: attempted, failures: failures, mode: database.storageMode
                )
                return database
            } catch {
                failures.append("\(url.path): \(error.localizedDescription)")
                print("[ReaderDatabase] ⚠️ cannot open \(url.path): \(error)")
            }
        }

        let reason = failures.joined(separator: "; ")
        print("[ReaderDatabase] ⚠️ no writable store location; falling back to in-memory (nothing will persist)")
        do {
            let database = try ReaderDatabase(inMemory: reason)
            // The loud path: this is the state a user must not be left guessing
            // about, so it leaves stdout even when the app was double-clicked.
            publishInitDiagnostic(
                attempted: attempted, failures: failures, mode: database.storageMode
            )
            return database
        } catch {
            // An in-memory DatabaseQueue only fails if the SQLite library
            // itself is unusable, which means nothing else can work either.
            fatalError("[ReaderDatabase] no usable SQLite store: \(error)")
        }
    }

    // MARK: - Init diagnostics (contract A11/A12)

    /// The outcome of locating the shared store, readable from any code path.
    ///
    /// Set exactly once, by `makeShared()`. `nil` before the shared instance is
    /// first touched — which is itself the signal that the lazy open has not run
    /// yet (the ambiguity `--self-test db-init` was added to remove).
    private(set) static var lastInitDiagnostic: ReaderDatabaseInitDiagnostic?

    /// Fixed-path log file for the init outcome.
    ///
    /// Deliberately next to the store rather than in `/tmp`: it must survive a
    /// relaunch so a user (or a verifier) can find out *after the fact* why
    /// nothing persisted. Falls back to the temp directory when the Application
    /// Support directory is not writable — and the resolved path is recorded in
    /// the diagnostic, so evidence never has to guess which one was used.
    static var initLogFileURL: URL {
        let base = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)
            .first
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        return base
            .appendingPathComponent("ReadFlow", isDirectory: true)
            .appendingPathComponent("database-init.log")
    }

    /// Second choice when `initLogFileURL` is not writable (measured here: a
    /// sandbox that denies `~/Library` also denies the Application Support log).
    /// A log that lands somewhere retrievable beats a log that lands nowhere,
    /// and the diagnostic names the file that was actually used.
    static var fallbackInitLogFileURL: URL {
        URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("ReadFlow", isDirectory: true)
            .appendingPathComponent("database-init.log")
    }

    /// Appends one line, trying the fixed path first and the temp path second.
    /// Returns the path actually written, or `nil` when neither worked.
    private static func appendInitLog(_ line: String) -> String? {
        for url in [initLogFileURL, fallbackInitLogFileURL] {
            do {
                try FileManager.default.createDirectory(
                    at: url.deletingLastPathComponent(),
                    withIntermediateDirectories: true
                )
                if FileManager.default.fileExists(atPath: url.path) {
                    let handle = try FileHandle(forWritingTo: url)
                    defer { try? handle.close() }
                    try handle.seekToEnd()
                    try handle.write(contentsOf: Data(line.utf8))
                } else {
                    try Data(line.utf8).write(to: url)
                }
                return url.path
            } catch {
                FileHandle.standardError.write(
                    Data("[ReaderDatabase] (could not write \(url.path): \(error.localizedDescription))\n".utf8)
                )
            }
        }
        return nil
    }

    private static func publishInitDiagnostic(
        attempted: [URL], failures: [String], mode: ReaderDatabaseStorageMode
    ) {
        let pending = ReaderDatabaseInitDiagnostic(
            attemptedPaths: attempted.map(\.path),
            failures: failures,
            mode: mode,
            logFile: nil
        )
        let stderrLine = "[ReaderDatabase] \(pending.summary)"

        // 1. stderr — always, whatever else succeeds.
        FileHandle.standardError.write(Data((stderrLine + "\n").utf8))

        // 2. A file, appended so successive launches accumulate. Best effort by
        //    design: the process must still start when the home directory is
        //    unwritable, which is one of the states being reported.
        let stamp = ISO8601DateFormatter().string(from: Date())
        let line = "\(stamp) pid=\(ProcessInfo.processInfo.processIdentifier) \(stderrLine)\n"
        let written = appendInitLog(line)

        lastInitDiagnostic = ReaderDatabaseInitDiagnostic(
            attemptedPaths: attempted.map(\.path),
            failures: failures,
            mode: mode,
            logFile: written
        )
    }

    // MARK: - Direct database access

    /// Read-only access for collaborators that need the raw GRDB `Database`
    /// (conversation persistence, ad-hoc queries).
    func read<T>(_ value: (Database) throws -> T) throws -> T {
        try dbQueue.read(value)
    }

    /// Write access for collaborators that need the raw GRDB `Database`.
    func write<T>(_ value: (Database) throws -> T) throws -> T {
        try dbQueue.write(value)
    }

    // MARK: - Schema Migration

    private var migrator: DatabaseMigrator {
        var migrator = DatabaseMigrator()

        // v1: Initial schema
        migrator.registerMigration("v1_initial_schema") { db in
            // Sources table
            try db.create(table: "sources") { t in
                t.column("id", .text).primaryKey()
                t.column("type", .text).notNull()
                t.column("title", .text).notNull()
                t.column("url", .text)
                t.column("canonicalURL", .text)
                t.column("filePath", .text)
                t.column("fileHash", .text)
                t.column("createdAt", .datetime).notNull()
                t.column("lastReadAt", .datetime)
                t.column("metadata", .blob)
            }
            try db.create(index: "idx_sources_type", on: "sources", columns: ["type"])
            try db.create(index: "idx_sources_url", on: "sources", columns: ["url"])

            // Reader items table
            try db.create(table: "reader_items") { t in
                t.column("id", .text).primaryKey()
                t.column("type", .text).notNull()
                t.column("sourceID", .text).notNull()
                    .indexed()
                    .references("sources", onDelete: .cascade)
                t.column("sessionID", .text)
                t.column("parentID", .text)
                    .references("reader_items", onDelete: .cascade)
                t.column("content", .text).notNull()
                t.column("metadata", .blob)
                t.column("createdAt", .datetime).notNull()
                t.column("updatedAt", .datetime).notNull()
                t.column("syncState", .text).notNull().defaults(to: "local_only")
            }
            try db.create(index: "idx_items_type", on: "reader_items", columns: ["type"])
            try db.create(index: "idx_items_session", on: "reader_items", columns: ["sessionID"])
            try db.create(index: "idx_items_sync", on: "reader_items", columns: ["syncState"])

            // Highlights table
            try db.create(table: "highlights") { t in
                t.column("id", .text).primaryKey()
                t.column("sourceID", .text).notNull()
                    .indexed()
                    .references("sources", onDelete: .cascade)
                t.column("sessionID", .text)
                t.column("selectedText", .text).notNull()
                t.column("contextBefore", .text)
                t.column("contextAfter", .text)
                t.column("page", .integer)
                t.column("location", .blob)
                t.column("createdAt", .datetime).notNull()
                t.column("syncState", .text).notNull().defaults(to: "local_only")
            }
            try db.create(index: "idx_highlights_source", on: "highlights", columns: ["sourceID"])

            // Notes table
            try db.create(table: "notes") { t in
                t.column("id", .text).primaryKey()
                t.column("sourceID", .text).notNull()
                    .indexed()
                    .references("sources", onDelete: .cascade)
                t.column("sessionID", .text)
                t.column("highlightID", .text)
                    .references("highlights", onDelete: .cascade)
                t.column("content", .text).notNull()
                t.column("createdAt", .datetime).notNull()
                t.column("updatedAt", .datetime).notNull()
                t.column("revision", .integer).notNull().defaults(to: 1)
                t.column("syncState", .text).notNull().defaults(to: "local_only")
            }
            try db.create(index: "idx_notes_source", on: "notes", columns: ["sourceID"])
            try db.create(index: "idx_notes_highlight", on: "notes", columns: ["highlightID"])

            // Sync outbox table
            try db.create(table: "sync_outbox") { t in
                t.autoIncrementedPrimaryKey("id")
                t.column("entityType", .text).notNull()
                t.column("entityID", .text).notNull()
                t.column("operation", .text).notNull()
                t.column("payload", .blob).notNull()
                t.column("createdAt", .datetime).notNull()
                t.column("retryCount", .integer).notNull().defaults(to: 0)
                t.column("lastAttemptAt", .datetime)
                t.column("lastError", .text)
            }
            try db.create(index: "idx_outbox_created", on: "sync_outbox", columns: ["createdAt"])
        }

        // v2: Full-text search
        //
        // ─────────────────────────────────────────────────────────────────────
        // C7 (CJK recall) READ THIS BEFORE CHANGING THE TOKENIZER.
        //
        // `.porter(wrapping: .unicode61())` below is **unchanged on purpose**, and
        // changing it is *not* how C7 was fixed. The stock tokenizers treat a run
        // of CJK as ONE token ("深度学习是一种能力"), so `MATCH '深度'` returns 0 —
        // and that stays true for `trigram` too (its tokens are 3-character
        // windows, so every 2-character query misses; measured).
        //
        // The fix changes **what gets indexed and what gets matched**, not how
        // bytes are tokenized:
        //   · index time — the `v4_cjk_fts` migration installs six application
        //     triggers that write `readflow_fts_transform(<column>)`, which
        //     rewrites each CJK run as single characters separated by spaces
        //     ("深 度 学 习 …") so one character is one token;
        //   · query time — `ftsPattern(for:)` runs the query text through the
        //     same function and matches it as a quoted phrase ("深 度"), which
        //     requires adjacency, so `阅读` matches `阅读…` but not `阅览室`;
        //   · existing rows — the same migration calls `rebuildFTSIndexes`,
        //     which drops the three FTS tables and **backfills every row**
        //     through the transform, then re-installs the triggers. Databases
        //     written before v4 are therefore searchable after the upgrade
        //     (regression-locked by `StorageSelfCheck.case11`).
        //
        // One command prints the whole proof, on a fresh store AND on a store
        // downgraded to the pre-v4 shape:
        //     <binary> --self-test cjk-recall
        // Measured: 2-char substrings recall (练习=1 深度=1), 4-char recalls,
        // negatives miss (这段/中检 = 0), English stemming still works.
        // ─────────────────────────────────────────────────────────────────────
        migrator.registerMigration("v2_fulltext_search") { db in
            // FTS5 virtual table for highlights
            // (tokenizer deliberately unchanged — see the C7 note above)
            try db.create(virtualTable: "highlights_fts", using: FTS5()) { t in
                t.column("selectedText")
                t.column("contextBefore")
                t.column("contextAfter")
                t.tokenizer = .porter(wrapping: .unicode61())
            }

            // FTS5 virtual table for notes
            try db.create(virtualTable: "notes_fts", using: FTS5()) { t in
                t.column("content")
                t.tokenizer = .porter(wrapping: .unicode61())
            }

            // FTS5 virtual table for reader items
            try db.create(virtualTable: "reader_items_fts", using: FTS5()) { t in
                t.column("content")
                t.tokenizer = .porter(wrapping: .unicode61())
            }

            // Triggers to keep FTS in sync
            try db.execute(sql: """
                CREATE TRIGGER highlights_ai AFTER INSERT ON highlights BEGIN
                    INSERT INTO highlights_fts(rowid, selectedText, contextBefore, contextAfter)
                    VALUES (new.rowid, new.selectedText, new.contextBefore, new.contextAfter);
                END;
                """)

            try db.execute(sql: """
                CREATE TRIGGER highlights_ad AFTER DELETE ON highlights BEGIN
                    DELETE FROM highlights_fts WHERE rowid = old.rowid;
                END;
                """)

            try db.execute(sql: """
                CREATE TRIGGER highlights_au AFTER UPDATE ON highlights BEGIN
                    UPDATE highlights_fts SET selectedText = new.selectedText,
                        contextBefore = new.contextBefore,
                        contextAfter = new.contextAfter
                    WHERE rowid = new.rowid;
                END;
                """)

            try db.execute(sql: """
                CREATE TRIGGER notes_ai AFTER INSERT ON notes BEGIN
                    INSERT INTO notes_fts(rowid, content) VALUES (new.rowid, new.content);
                END;
                """)

            try db.execute(sql: """
                CREATE TRIGGER notes_ad AFTER DELETE ON notes BEGIN
                    DELETE FROM notes_fts WHERE rowid = old.rowid;
                END;
                """)

            try db.execute(sql: """
                CREATE TRIGGER notes_au AFTER UPDATE ON notes BEGIN
                    UPDATE notes_fts SET content = new.content WHERE rowid = new.rowid;
                END;
                """)

            try db.execute(sql: """
                CREATE TRIGGER reader_items_ai AFTER INSERT ON reader_items BEGIN
                    INSERT INTO reader_items_fts(rowid, content) VALUES (new.rowid, new.content);
                END;
                """)

            try db.execute(sql: """
                CREATE TRIGGER reader_items_ad AFTER DELETE ON reader_items BEGIN
                    DELETE FROM reader_items_fts WHERE rowid = old.rowid;
                END;
                """)

            try db.execute(sql: """
                CREATE TRIGGER reader_items_au AFTER UPDATE ON reader_items BEGIN
                    UPDATE reader_items_fts SET content = new.content WHERE rowid = new.rowid;
                END;
                """)
        }

        // v3: Conversation tables
        migrator.registerMigration("v3_conversations") { db in
            // Conversations table
            try db.create(table: "conversations") { t in
                t.column("id", .text).primaryKey()
                t.column("highlightID", .text).notNull()
                    .indexed()
                    .references("highlights", onDelete: .cascade)
                t.column("scope", .text).notNull()
                t.column("createdAt", .datetime).notNull()
                t.column("updatedAt", .datetime).notNull()
            }

            // Conversation messages table
            try db.create(table: "conversation_messages") { t in
                t.column("id", .text).primaryKey()
                t.column("conversationID", .text).notNull()
                    .indexed()
                    .references("conversations", onDelete: .cascade)
                t.column("role", .text).notNull()
                t.column("content", .text).notNull()
                t.column("citationsJSON", .blob)
                t.column("createdAt", .datetime).notNull()
            }
        }

        // v4: CJK-aware FTS (C7) + one-off reindex (C31).
        //
        // Why a *new* migration instead of editing v2 in place: GRDB never
        // re-runs an already-applied migration, so editing v2 would fix fresh
        // databases and silently leave existing user databases broken (columns
        // and index form unchanged). Measured by two independent verifiers:
        // "edit in place, no new migration" is green on a fresh DB and a no-op
        // on an upgraded one. Conversely, editing v2 *and* adding v4 raises
        // `duplicate column name` on a fresh DB. Touching nothing and adding a
        // new migration is the only shape that works on both starting points.
        //
        // The reindex matters as much as the tokenizer change: the three FTS
        // tables are maintained by application-owned triggers only, with no
        // `content=` source table and no rebuild/backfill path anywhere in the
        // package. Existing rows therefore keep their old single-token CJK
        // index unless they are explicitly rewritten here.
        migrator.registerMigration("v4_cjk_fts") { db in
            try Self.rebuildFTSIndexes(in: db)
        }

        // v5: per-message AI attribution (C29).
        //
        // A *new* migration, not an edit of v3: GRDB never re-runs an applied
        // migration, so folding these columns into v3 would give fresh
        // databases the columns and leave existing ones without them forever
        // (measured by two independent verifiers). Editing v3 *and* adding v5
        // is equally wrong — a fresh database then throws
        // `duplicate column name` because v3 runs before v5.
        //
        // Both columns are nullable with **no default**. A default would
        // fabricate attribution for every pre-existing row: `DEFAULT 'offline'`
        // would claim every historical answer came from the offline provider,
        // which is exactly the "silently invented data" class the contract
        // forbids (C29). SQLite leaves old rows NULL when no default is given,
        // so nothing needs backfilling.
        migrator.registerMigration("v5_conversation_attribution") { db in
            try db.alter(table: "conversation_messages") { t in
                t.add(column: "provider", .text)
                t.add(column: "isDegraded", .boolean)
            }
        }

        return migrator
    }

    /// Drops the FTS tables and their sync triggers, recreates them, rewrites
    /// every existing row through the CJK transform, and re-installs triggers
    /// that apply the same transform on future writes.
    ///
    /// Idempotent: safe to run repeatedly (it is a migration body, so GRDB runs
    /// it once per database, but the statements themselves are also re-runnable).
    private static func rebuildFTSIndexes(in db: Database) throws {
        // Drop the v2-era triggers first: they write raw (untransformed) text,
        // and SQLite does not complain if a trigger targets a missing table
        // until it fires.
        for table in ["highlights", "notes", "reader_items"] {
            for suffix in ["ai", "ad", "au"] {
                try db.execute(sql: "DROP TRIGGER IF EXISTS \(table)_\(suffix)")
            }
        }

        for table in ["highlights_fts", "notes_fts", "reader_items_fts"] {
            try db.execute(sql: "DROP TABLE IF EXISTS \(table)")
        }

        // highlights
        try db.create(virtualTable: "highlights_fts", using: FTS5()) { t in
            t.column("selectedText")
            t.column("contextBefore")
            t.column("contextAfter")
            t.tokenizer = .porter(wrapping: .unicode61())
        }
        try db.execute(sql: """
            INSERT INTO highlights_fts(rowid, selectedText, contextBefore, contextAfter)
            SELECT rowid,
                   \(ftsTransformFunction)(selectedText),
                   \(ftsTransformFunction)(contextBefore),
                   \(ftsTransformFunction)(contextAfter)
            FROM highlights;
            """)

        // notes
        try db.create(virtualTable: "notes_fts", using: FTS5()) { t in
            t.column("content")
            t.tokenizer = .porter(wrapping: .unicode61())
        }
        try db.execute(sql: """
            INSERT INTO notes_fts(rowid, content)
            SELECT rowid, \(ftsTransformFunction)(content) FROM notes;
            """)

        // reader items
        try db.create(virtualTable: "reader_items_fts", using: FTS5()) { t in
            t.column("content")
            t.tokenizer = .porter(wrapping: .unicode61())
        }
        try db.execute(sql: """
            INSERT INTO reader_items_fts(rowid, content)
            SELECT rowid, \(ftsTransformFunction)(content) FROM reader_items;
            """)

        // Sync triggers. Every statement that writes indexable text routes it
        // through the same SQL function the queries use.
        for spec in [
            (table: "highlights", columns: ["selectedText", "contextBefore", "contextAfter"]),
            (table: "notes", columns: ["content"]),
            (table: "reader_items", columns: ["content"]),
        ] {
            let fts = "\(spec.table)_fts"
            try db.execute(sql: "DROP TRIGGER IF EXISTS \(spec.table)_ai")
            try db.execute(sql: "DROP TRIGGER IF EXISTS \(spec.table)_ad")
            try db.execute(sql: "DROP TRIGGER IF EXISTS \(spec.table)_au")

            let columnList = spec.columns.joined(separator: ", ")
            let transformedNew = spec.columns
                .map { "\(ftsTransformFunction)(new.\($0))" }
                .joined(separator: ", ")
            let updateAssignments = spec.columns
                .map { "\($0) = \(ftsTransformFunction)(new.\($0))" }
                .joined(separator: ", ")

            try db.execute(sql: """
                CREATE TRIGGER \(spec.table)_ai AFTER INSERT ON \(spec.table) BEGIN
                    INSERT INTO \(fts)(rowid, \(columnList))
                    VALUES (new.rowid, \(transformedNew));
                END;
                """)
            try db.execute(sql: """
                CREATE TRIGGER \(spec.table)_ad AFTER DELETE ON \(spec.table) BEGIN
                    DELETE FROM \(fts) WHERE rowid = old.rowid;
                END;
                """)
            try db.execute(sql: """
                CREATE TRIGGER \(spec.table)_au AFTER UPDATE ON \(spec.table) BEGIN
                    UPDATE \(fts) SET \(updateAssignments) WHERE rowid = new.rowid;
                END;
                """)
        }
    }

    // MARK: - Source Operations

    func createSource(_ source: Source) throws {
        try dbQueue.write { db in
            try source.insert(db)

            // A source must reach the server before child highlights and notes
            // that reference it, so record its create in the same transaction.
            let payload = try JSONEncoder().encode(source)
            try SyncOutbox(
                entityType: "source",
                entityID: source.id,
                operation: "create",
                payload: payload
            ).insert(db)
        }
    }

    func getSource(id: UUID) throws -> Source? {
        try dbQueue.read { db in
            try Source.fetchOne(db, key: id)
        }
    }

    func listSources(limit: Int = 100, offset: Int = 0) throws -> [Source] {
        try dbQueue.read { db in
            try Source
                .order(Source.Columns.createdAt.desc)
                .limit(limit, offset: offset)
                .fetchAll(db)
        }
    }

    func updateSource(_ source: Source) throws {
        try dbQueue.write { db in
            try source.update(db)
        }
    }

    func deleteSource(id: UUID) throws {
        try dbQueue.write { db in
            _ = try Source.deleteOne(db, key: id)
        }
    }

    /// Insert-or-replace used when *applying* server state during a pull.
    ///
    /// Deliberately does not write a `sync_outbox` row: enqueueing the very
    /// change we just received from the server would make the next push echo it
    /// straight back.
    func upsertSource(_ source: Source) throws {
        try dbQueue.write { db in
            try source.save(db)
        }
    }

    // MARK: - ReaderItem Operations (with Outbox Pattern)

    func createReaderItem(_ item: ReaderItem) throws {
        try dbQueue.write { db in
            try item.insert(db)

            // Add to outbox for sync
            let payload = try JSONEncoder().encode(item)
            let outboxEntry = SyncOutbox(
                entityType: "reader_item",
                entityID: item.id,
                operation: "create",
                payload: payload
            )
            try outboxEntry.insert(db)
        }
    }

    func updateReaderItem(_ item: ReaderItem) throws {
        try dbQueue.write { db in
            // Update item
            try item.update(db)

            // Add to outbox for sync
            let payload = try JSONEncoder().encode(item)
            let outboxEntry = SyncOutbox(
                entityType: "reader_item",
                entityID: item.id,
                operation: "update",
                payload: payload
            )
            try outboxEntry.insert(db)
        }
    }

    func getReaderItem(id: UUID) throws -> ReaderItem? {
        try dbQueue.read { db in
            try ReaderItem.fetchOne(db, key: id)
        }
    }

    func listReaderItems(sourceID: UUID? = nil, type: ReaderItem.ItemType? = nil, limit: Int = 100) throws -> [ReaderItem] {
        try dbQueue.read { db in
            var request = ReaderItem.order(ReaderItem.Columns.createdAt.desc)

            if let sourceID = sourceID {
                request = request.filter(ReaderItem.Columns.sourceID == sourceID)
            }

            if let type = type {
                request = request.filter(ReaderItem.Columns.type == type.rawValue)
            }

            return try request.limit(limit).fetchAll(db)
        }
    }

    func deleteReaderItem(id: UUID) throws {
        try dbQueue.write { db in
            // Delete item
            try ReaderItem.deleteOne(db, key: id)

            // Add to outbox for sync
            let outboxEntry = SyncOutbox(
                entityType: "reader_item",
                entityID: id,
                operation: "delete",
                payload: Data()
            )
            try outboxEntry.insert(db)
        }
    }

    /// Insert-or-replace when applying server state; see `upsertSource(_:)`.
    func upsertReaderItem(_ item: ReaderItem) throws {
        try dbQueue.write { db in
            try item.save(db)
        }
    }

    // MARK: - Highlight Operations (with Outbox Pattern)

    func createHighlight(_ highlight: Highlight) throws {
        try dbQueue.write { db in
            try highlight.insert(db)

            let payload = try JSONEncoder().encode(highlight)
            let outboxEntry = SyncOutbox(
                entityType: "highlight",
                entityID: highlight.id,
                operation: "create",
                payload: payload
            )
            try outboxEntry.insert(db)
        }
    }

    func getHighlight(id: UUID) throws -> Highlight? {
        try dbQueue.read { db in
            try Highlight.fetchOne(db, key: id)
        }
    }

    func listHighlights(sourceID: UUID, limit: Int = 100) throws -> [Highlight] {
        try dbQueue.read { db in
            try Highlight
                .filter(Highlight.Columns.sourceID == sourceID)
                .order(Highlight.Columns.createdAt.desc)
                .limit(limit)
                .fetchAll(db)
        }
    }

    func deleteHighlight(id: UUID) throws {
        try dbQueue.write { db in
            try Highlight.deleteOne(db, key: id)

            let outboxEntry = SyncOutbox(
                entityType: "highlight",
                entityID: id,
                operation: "delete",
                payload: Data()
            )
            try outboxEntry.insert(db)
        }
    }

    /// Insert-or-replace when applying server state; see `upsertSource(_:)`.
    func upsertHighlight(_ highlight: Highlight) throws {
        try dbQueue.write { db in
            try highlight.save(db)
        }
    }

    /// Local edit that must be propagated to the server.
    func updateHighlight(_ highlight: Highlight) throws {
        try dbQueue.write { db in
            try highlight.update(db)

            let payload = try JSONEncoder().encode(highlight)
            let outboxEntry = SyncOutbox(
                entityType: "highlight",
                entityID: highlight.id,
                operation: "update",
                payload: payload
            )
            try outboxEntry.insert(db)
        }
    }

    // MARK: - Note Operations (with Outbox Pattern)

    func createNote(_ note: Note) throws {
        try dbQueue.write { db in
            try note.insert(db)

            let payload = try JSONEncoder().encode(note)
            let outboxEntry = SyncOutbox(
                entityType: "note",
                entityID: note.id,
                operation: "create",
                payload: payload
            )
            try outboxEntry.insert(db)
        }
    }

    func updateNote(_ note: Note) throws {
        try dbQueue.write { db in
            try note.update(db)

            let payload = try JSONEncoder().encode(note)
            let outboxEntry = SyncOutbox(
                entityType: "note",
                entityID: note.id,
                operation: "update",
                payload: payload
            )
            try outboxEntry.insert(db)
        }
    }

    func getNote(id: UUID) throws -> Note? {
        try dbQueue.read { db in
            try Note.fetchOne(db, key: id)
        }
    }

    func listNotes(sourceID: UUID? = nil, highlightID: UUID? = nil, limit: Int = 100) throws -> [Note] {
        try dbQueue.read { db in
            var request = Note.order(Note.Columns.updatedAt.desc)

            if let sourceID = sourceID {
                request = request.filter(Note.Columns.sourceID == sourceID)
            }

            if let highlightID = highlightID {
                request = request.filter(Note.Columns.highlightID == highlightID)
            }

            return try request.limit(limit).fetchAll(db)
        }
    }

    func deleteNote(id: UUID) throws {
        try dbQueue.write { db in
            try Note.deleteOne(db, key: id)

            let outboxEntry = SyncOutbox(
                entityType: "note",
                entityID: id,
                operation: "delete",
                payload: Data()
            )
            try outboxEntry.insert(db)
        }
    }

    /// Insert-or-replace when applying server state; see `upsertSource(_:)`.
    func upsertNote(_ note: Note) throws {
        try dbQueue.write { db in
            try note.save(db)
        }
    }

    // MARK: - Full-Text Search (contract C25 surface)

    /// Local FTS5 search over highlights, mapped to the local result type.
    ///
    /// C25 requires the search entry points to return `LocalSearchResult`
    /// rather than the concrete record types: a result has to be able to say
    /// "the source title is unknown" (`nil`) instead of failing to construct,
    /// and it must not smuggle a sentinel string in place of a missing value.
    /// The record-typed queries remain available under `searchHighlightRecords`
    /// for internal adapters and the storage self-check.
    func searchHighlights(query: String, limit: Int = 50) throws -> [LocalSearchResult] {
        try searchHighlightRecords(query: query, limit: limit).map { highlight in
            LocalSearchResult(
                highlight: highlight,
                sourceTitle: try getSource(id: highlight.sourceID)?.title
            )
        }
    }

    /// Local FTS5 search over notes, mapped to the local result type.
    func searchNotes(query: String, limit: Int = 50) throws -> [LocalSearchResult] {
        try searchNoteRecords(query: query, limit: limit).map { note in
            LocalSearchResult(
                note: note,
                sourceTitle: try getSource(id: note.sourceID)?.title
            )
        }
    }

    /// Local FTS5 search over reader items, mapped to the local result type.
    func searchReaderItems(query: String, type: ReaderItem.ItemType? = nil, limit: Int = 50) throws -> [LocalSearchResult] {
        try searchReaderItemRecords(query: query, type: type, limit: limit).map { item in
            let title = try item.sourceID.flatMap { try getSource(id: $0)?.title }
            return LocalSearchResult(readerItem: item, sourceTitle: title)
        }
    }

    // MARK: - Full-Text Search (record-level, internal)

    func searchHighlightRecords(query: String, limit: Int = 50) throws -> [Highlight] {
        try dbQueue.read { db in
            guard let pattern = Self.ftsPattern(for: query) else { return [] }
            let sql = """
                SELECT h.* FROM highlights h
                INNER JOIN highlights_fts fts ON h.rowid = fts.rowid
                WHERE highlights_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """
            return try Highlight.fetchAll(db, sql: sql, arguments: [pattern, limit])
        }
    }

    func searchNoteRecords(query: String, limit: Int = 50) throws -> [Note] {
        try dbQueue.read { db in
            guard let pattern = Self.ftsPattern(for: query) else { return [] }
            let sql = """
                SELECT n.* FROM notes n
                INNER JOIN notes_fts fts ON n.rowid = fts.rowid
                WHERE notes_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """
            return try Note.fetchAll(db, sql: sql, arguments: [pattern, limit])
        }
    }

    func searchReaderItemRecords(query: String, type: ReaderItem.ItemType? = nil, limit: Int = 50) throws -> [ReaderItem] {
        try dbQueue.read { db in
            guard let pattern = Self.ftsPattern(for: query) else { return [] }
            var sql = """
                SELECT r.* FROM reader_items r
                INNER JOIN reader_items_fts fts ON r.rowid = fts.rowid
                WHERE reader_items_fts MATCH ?
                """

            var arguments: [DatabaseValueConvertible] = [pattern]

            if let type = type {
                sql += " AND r.type = ?"
                arguments.append(type.rawValue)
            }

            sql += " ORDER BY rank LIMIT ?"
            arguments.append(limit)

            return try ReaderItem.fetchAll(db, sql: sql, arguments: StatementArguments(arguments))
        }
    }

    // MARK: - Sync Outbox Operations

    func getPendingSyncItems(limit: Int = 100) throws -> [SyncOutbox] {
        try dbQueue.read { db in
            try SyncOutbox
                // The row id preserves insertion order when several related
                // records share the same timestamp (source before child).
                .order(SyncOutbox.Columns.createdAt.asc, SyncOutbox.Columns.id.asc)
                .limit(limit)
                .fetchAll(db)
        }
    }

    func markSyncItemSuccess(id: Int64) throws {
        try dbQueue.write { db in
            _ = try SyncOutbox.deleteOne(db, key: id)
        }
    }

    func markSyncItemFailed(id: Int64, error: String) throws {
        try dbQueue.write { db in
            if var item = try SyncOutbox.fetchOne(db, key: id) {
                item.retryCount += 1
                item.lastAttemptAt = Date()
                item.lastError = error
                try item.update(db)
            }
        }
    }

    func clearOutbox() throws {
        try dbQueue.write { db in
            _ = try SyncOutbox.deleteAll(db)
        }
    }
}

// MARK: - CJK detection

extension Character {
    /// True for Han ideographs and the CJK ranges the app's content actually
    /// uses. Deliberately excludes kana-only text (Japanese does not need
    /// bigrams to search well with `unicode61`).
    var isCJK: Bool {
        guard let scalar = unicodeScalars.first else { return false }
        switch scalar.value {
        case 0x3400...0x4DBF,     // CJK Unified Ideographs Extension A
             0x4E00...0x9FFF,     // CJK Unified Ideographs
             0xF900...0xFAFF,     // CJK Compatibility Ideographs
             0x20000...0x2A6DF,   // Extension B
             0x2A700...0x2EBEF,   // Extensions C–F
             0x3040...0x30FF,     // Hiragana + Katakana
             0xAC00...0xD7AF:     // Hangul syllables
            return true
        default:
            return false
        }
    }
}
