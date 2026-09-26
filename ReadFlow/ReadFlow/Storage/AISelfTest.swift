//
//  AISelfTest.swift
//  ReadFlow
//
//  `--self-test <scenario>` — a framework-free, text-observable outlet for the
//  AI/persistence behaviours that cannot be seen in this environment.
//
//  Why a CLI instead of a test target: `ReadFlowTests` cannot compile here
//  (`no such module 'XCTest'` under Command Line Tools), and the swift-testing
//  route fails for any Foundation-typed assertion because `_Testing_Foundation`
//  ships without a `Modules/` directory. This outlet needs no framework, no
//  `-F`/`-rpath`, and no `@testable import`, so verification and a real user can
//  both run it:
//
//      ReadFlow.app/Contents/MacOS/ReadFlow --self-test roundtrip-nil
//      ReadFlow.app/Contents/MacOS/ReadFlow --self-test attribution-nil
//
//  Exit code is the verdict: 0 = every assertion held.
//
//  Scope note: this is a *derivation* witness, not a visual one. It proves which
//  strings and values the render layer would derive; it cannot prove how they
//  look. Contract C30 records that no role in this environment can observe the
//  interface, so visual acceptance stays explicitly uncovered.
//

import Foundation
import GRDB

enum AISelfTest {
    /// Command line flag that triggers the suite.
    static let flag = "--self-test"

    /// Every scenario this outlet understands, in the order they are documented.
    static let knownScenarios = [
        "roundtrip-nil", "attribution-nil", "render-row", "render-search-row",
        "db-init", "roundtrip-sentinel-guard", "localbook-connection", "cjk-recall",
        "s1-unconfigured", "s2-unreachable", "success",
        "unauthorized", "timeout", "bad-response", "rate-limited",
    ]

    private static var failures = 0
    private static var assertions = 0

    // MARK: - Entry point

    /// Runs the named scenario. Returns the process exit code (0 = all passed).
    static func run(scenario: String) -> Int32 {
        log("self-test: start scenario=\(scenario)")

        let workDir: URL
        do {
            let dir = FileManager.default.temporaryDirectory
                .appendingPathComponent("readflow-self-test-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            workDir = dir
        } catch {
            log("self-test: FAIL — could not create workspace: \(error)")
            return 1
        }
        defer { try? FileManager.default.removeItem(at: workDir) }

        let storeURL = workDir.appendingPathComponent("readflow.sqlite")

        switch scenario {
        case "roundtrip-nil":
            scenarioRoundTripNil(storeURL: storeURL)
        case "attribution-nil":
            scenarioAttributionNil()
        case "render-row":
            scenarioRenderRow()
        case "render-search-row":
            // Local FTS5 hits, not server `SearchResult`s — see
            // LocalSearchRowSelfTest for why the distinction is the whole point.
            // The body is self-contained so that this file keeps one writer.
            let rowOutcome = LocalSearchRowSelfTest.run()
            assertions += rowOutcome.assertions
            failures += rowOutcome.failures
        case "roundtrip-sentinel-guard":
            // The negative control for roundtrip-nil: judges the detector
            // itself. Self-contained, same as the other added outlets.
            let guardOutcome = SentinelGuardSelfTest.run()
            assertions += guardOutcome.assertions
            failures += guardOutcome.failures
        case "cjk-recall":
            // Prints the whole C7 proof (fresh store + a store rewound to the
            // pre-v4 shape). Self-contained, same as the other added outlets.
            let cjkOutcome = CJKRecallSelfTest.run()
            assertions += cjkOutcome.assertions
            failures += cjkOutcome.failures
        case "localbook-connection":
            let outcome = LocalBookConnectionSelfTest.run()
            return outcome

        case "db-init":
            // Forces the shared store open and reports the path actually in use,
            // the file on disk, and the three FTS5 tables — see
            // DatabaseInitSelfTest. Self-contained, same as above.
            let dbOutcome = DatabaseInitSelfTest.run()
            assertions += dbOutcome.assertions
            failures += dbOutcome.failures
        case "s1-unconfigured":
            scenarioS1Unconfigured()
        case "s2-unreachable":
            scenarioS2Unreachable()
        case "success":
            scenarioCloudSuccess()
        case "unauthorized":
            scenarioCloudUnauthorized()
        case "timeout":
            scenarioCloudTimeout()
        case "bad-response":
            scenarioCloudBadResponse()
        case "rate-limited":
            scenarioCloudRateLimited()
        default:
            log("self-test: FAIL — unknown scenario '\(scenario)'")
            log("self-test: known scenarios: \(knownScenarios.joined(separator: ", "))")
            return 2
        }

        if failures == 0 {
            log("self-test: PASS (\(assertions) assertions)")
            return 0
        }
        log("self-test: FAIL (\(failures) of \(assertions) assertions failed)")
        return 1
    }

    // MARK: - Scenario: roundtrip-nil

    /// Proves that a `nil`-valued citation survives the real persistence path.
    ///
    /// The assertion is a **count-preserving round trip**, not merely "the fields
    /// are nil". That distinction is the whole point: the storage layer wraps
    /// both the encoder and the decoder in `try?`, so "decoding failed",
    /// "the column is NULL" and "there were no citations" are mutually
    /// indistinguishable by return value. A row whose blob is corrupt would
    /// satisfy a bare nil-check. Requiring `count == 1` makes an unwritten or
    /// unreadable row fail instead of pass.
    ///
    /// The fixture writes **one element**. A `citations: nil` fixture would make
    /// the scenario unfailable, which is the vacuity this check exists to avoid.
    private static func scenarioRoundTripNil(storeURL: URL) {
        var conversationID: UUID?
        var expectedCitationID: UUID?
        var seededSourceID: UUID?
        var seededHighlightID: UUID?

        // Seeding is mandatory, not incidental: `foreignKeysEnabled` is true by
        // default in GRDB and the chain is
        // sources ← highlights ← conversations ← conversation_messages, so a
        // message insert without its three parents dies with
        // `FOREIGN KEY constraint failed` — an error that reads like a defect
        // but is really a missing fixture. The chain is printed below so that
        // distinction is visible in the output rather than inferred by a reader.
        check("seed", "sources → highlights → conversations can be created") {
            let database = try ReaderDatabase(path: storeURL.path)
            let source = Source(type: .web, title: "self-test source", url: "https://example.com/a")
            try database.createSource(source)
            let highlight = Highlight(sourceID: source.id, selectedText: "被划选的文本")
            try database.createHighlight(highlight)

            let storage = ConversationStorage(database: database)
            let conversation = try storage.createConversation(
                highlightID: highlight.id,
                scope: .currentContent
            )
            seededSourceID = source.id
            seededHighlightID = highlight.id
            conversationID = conversation.id
            return true
        }

        // What the fixture actually built, so a future FK failure is recognisable
        // as a missing-parent problem instead of looking like a storage defect.
        if let seededSourceID, let seededHighlightID, let conversationID {
            log("self-test: info — fixture chain: source=\(seededSourceID.uuidString) "
                + "→ highlight=\(seededHighlightID.uuidString) "
                + "→ conversation=\(conversationID.uuidString) → message "
                + "(foreign_keys=ON, so all three parents are required)")
        }

        // The fixture is selectable so the negative control can be demonstrated
        // with **the same command, only the fixture changed** (captain's judging
        // rule — a written claim that "this would fail" is not evidence):
        //
        //     --self-test roundtrip-nil                            → exit 0
        //     READFLOW_SELFTEST_POISON=1 --self-test roundtrip-nil → exit 1
        //
        // The poisoned values are exactly what a naive `?? "无标题来源"` /
        // `?? ""` at a construction site would persist.
        let poisoning = ProcessInfo.processInfo.environment["READFLOW_SELFTEST_POISON"] == "1"
        log("self-test: info — fixture mode=\(poisoning ? "POISONED (READFLOW_SELFTEST_POISON=1)" : "nil-valued")")

        // The write half: assert on what `addMessage` returns.
        check("roundtrip-nil", "addMessage returns exactly one nil-valued citation") {
            guard let conversationID else { return false }
            let database = try ReaderDatabase(path: storeURL.path)
            let storage = ConversationStorage(database: database)
            let citation = poisoning
                ? Citation(sourceID: UUID(), title: "无标题来源", snippet: "", url: nil)
                : Citation(sourceID: UUID(), title: nil, snippet: nil, url: nil)
            expectedCitationID = citation.id

            let record = try storage.addMessage(
                conversationID: conversationID,
                role: .assistant,
                content: "离线降级回答",
                citations: [citation],
                provider: AIProviderKind.offline.rawValue,
                isDegraded: false
            )

            guard let stored = record.citations else {
                log("self-test: info — returned citations was nil (encode failure?)")
                return false
            }
            log("self-test: info — returned count=\(stored.count) blob=\(describe(record.citationsJSON))")
            // LOAD-BEARING CLAUSE. The element-count check is what makes this
            // assertion able to fail: `ConversationMessageRecord.citations` answers
            // `nil` for a corrupt blob, for a NULL column (encode failure) and for
            // a row that was never written — three different faults, one return
            // value. Only "exactly one element came back" separates them from a
            // correct row, and it does so without depending on the storage layer's
            // `try?` choices. Do not replace it with a bare nil-check on the
            // fields: such an assertion holds on `x'DEADBEEF'` too.
            guard stored.count == 1 else { return false }
            return stored[0].title == nil && stored[0].snippet == nil && stored[0].url == nil
        }

        // The read half: a *separate* connection, so the assertion covers real
        // persistence rather than an in-memory object graph.
        check("roundtrip-nil", "a reopened store still reports exactly one nil-valued citation") {
            guard let conversationID, let expectedCitationID else { return false }
            let reopened = try ReaderDatabase(path: storeURL.path)
            let storage = ConversationStorage(database: reopened)
            // The raw column as it sits in the database, so the evidence shows
            // what actually persisted rather than only what the decoder made of
            // it. The whole point of A30 is that a sentinel reaching this BLOB is
            // indistinguishable from real content later, and an in-memory
            // encode/decode would never exercise this column at all.
            let rawJSON = try reopened.read { db in
                try Data.fetchOne(
                    db,
                    sql: """
                        SELECT citationsJSON FROM conversation_messages
                        WHERE conversationID = ? AND role = 'assistant'
                        """,
                    arguments: [conversationID]
                )
            }
            log("self-test: info — re-read raw blob=\(describe(rawJSON))")
            let messages = try storage.conversationMessages(conversationID: conversationID)
            guard let message = messages.first(where: { $0.role == .assistant }) else {
                log("self-test: info — no assistant row came back")
                return false
            }
            guard let stored = message.citations else {
                log("self-test: info — re-read citations was nil (corrupt blob, or nothing stored)")
                return false
            }
            log("self-test: info — re-read count=\(stored.count) "
                + "title=\(String(describing: stored[0].title)) "
                + "snippet=\(String(describing: stored[0].snippet)) "
                + "url=\(String(describing: stored[0].url))")
            guard stored.count == 1 else { return false }
            guard stored[0].id == expectedCitationID else {
                log("self-test: info — citation identity changed across the round trip")
                return false
            }
            return stored[0].title == nil && stored[0].snippet == nil && stored[0].url == nil
        }

        // Attribution written in the same insert must survive too, and both
        // columns must come back — a half-written row is the mixed shape that
        // `attribution-nil` exists to catch downstream.
        //
        // Read through the raw record rather than `conversationMessages`, which
        // returns the view model `ConversationMessage` (no attribution fields).
        check("roundtrip-nil", "both attribution columns survive the round trip") {
            guard let conversationID else { return false }
            let reopened = try ReaderDatabase(path: storeURL.path)
            let record = try reopened.read { db in
                try ConversationMessageRecord
                    .filter(ConversationMessageRecord.Columns.conversationID == conversationID)
                    .filter(ConversationMessageRecord.Columns.role == "assistant")
                    .fetchOne(db)
            }
            guard let record else {
                log("self-test: info — no assistant record came back")
                return false
            }
            log("self-test: info — provider=\(String(describing: record.provider)) "
                + "isDegraded=\(String(describing: record.isDegraded))")
            return record.provider == AIProviderKind.offline.rawValue
                && record.isDegraded == false
        }

        // The negative control. A gate that cannot fail is not evidence, so this
        // asserts the *inverse*: a deliberately poisoned value must be
        // detectable. Written as its own row so it cannot mask the positive case.
        check("roundtrip-nil", "NEGATIVE CONTROL: a sentinel value is distinguishable from nil") {
            guard let conversationID else { return false }
            let database = try ReaderDatabase(path: storeURL.path)
            let storage = ConversationStorage(database: database)
            let sentinel = Citation(sourceID: UUID(), title: "无标题来源", snippet: "", url: nil)

            let record = try storage.addMessage(
                conversationID: conversationID,
                role: .assistant,
                content: "投毒样本",
                citations: [sentinel]
            )
            guard let stored = record.citations else { return false }
            guard stored.count == 1 else { return false }
            // The poisoned row must NOT look like a nil row: this is what makes
            // the positive assertions above meaningful.
            let looksLikeNil = stored[0].title == nil && stored[0].snippet == nil
            log("self-test: info — sentinel title=\(String(describing: stored[0].title)) "
                + "looksLikeNil=\(looksLikeNil)")
            return !looksLikeNil
        }

        // Three states, told apart. The lenient `record.citations` answers `nil`
        // both for "the column is NULL" and for "the bytes are undecodable", so an
        // assertion of the form "title is nil after the round trip" would pass on
        // a corrupt blob. This check requires the *decoded* state specifically.
        check("roundtrip-nil", "the strict reader reports decoded — not absent, not corrupt") {
            guard let conversationID else { return false }
            let database = try ReaderDatabase(path: storeURL.path)
            let record = try database.read { db in
                try ConversationMessageRecord
                    .filter(ConversationMessageRecord.Columns.conversationID == conversationID)
                    .filter(ConversationMessageRecord.Columns.role == "assistant")
                    .fetchOne(db)
            }
            guard let record else {
                log("self-test: info — no assistant record to inspect")
                return false
            }
            switch record.citationBlobState() {
            case .decoded(let citations):
                log("self-test: info — blob state=decoded (\(citations.count) citation(s))")
                return citations.count == 1
            case .absent:
                log("self-test: info — blob state=absent (column NULL): the write stored nothing")
                return false
            case .corrupt(let error):
                log("self-test: FAIL — blob state=corrupt: \(error)")
                return false
            }
        }

        // The probe that gives the check above its teeth: damage a blob the way a
        // truncated write or a bad migration would, then require the damage to be
        // *visible* as damage. A lenient reader would report nil here and the
        // whole A30 chain would be measured against a row that cannot decode.
        check("roundtrip-nil", "CORRUPT-BLOB PROBE: a damaged blob is not read as 'no citations'") {
            guard let conversationID else { return false }
            let database = try ReaderDatabase(path: storeURL.path)
            let storage = ConversationStorage(database: database)
            let probe = try storage.addMessage(
                conversationID: conversationID,
                role: .assistant,
                content: "损坏样本",
                citations: [Citation(sourceID: UUID(), title: "会被毁掉", snippet: "x", url: nil)]
            )
            try database.write { db in
                try db.execute(
                    sql: "UPDATE conversation_messages SET citationsJSON = x'DEADBEEF' WHERE id = ?",
                    arguments: [probe.id]
                )
            }
            let record = try database.read { db in
                try ConversationMessageRecord.fetchOne(db, key: probe.id)
            }
            guard let record else { return false }

            let state = record.citationBlobState()
            let lenient = record.citations
            var strictThrew = false
            do {
                _ = try record.decodedCitations()
            } catch {
                strictThrew = true
            }
            log("self-test: info — probe: state=\(state) strict reader threw=\(strictThrew) "
                + "lenient reader=\(lenient == nil ? "nil (indistinguishable from absent)" : "non-nil")")

            if case .corrupt = state {} else {
                log("self-test: FAIL — a damaged blob was not reported as corrupt")
                return false
            }
            // The strict path must refuse to answer rather than answer "nothing".
            guard strictThrew else {
                log("self-test: FAIL — decodedCitations() returned instead of throwing on a corrupt blob")
                return false
            }
            // And the lenient path's conflation is asserted rather than assumed,
            // so §4.8's risk entry stays true.
            return lenient == nil
        }

        // Why the count clause is the load-bearing one, demonstrated rather than
        // argued: the same lenient reader the UI uses is applied to four states of
        // the same row, and only the count clause separates them. This is the
        // exact table the "corrupt blob passes the nil-check" trap comes from.
        check("roundtrip-nil", "LOAD-BEARING: only the count clause separates good from corrupt/absent/missing") {
            guard let conversationID else { return false }
            let database = try ReaderDatabase(path: storeURL.path)
            let storage = ConversationStorage(database: database)

            func countClause(for messageID: UUID) throws -> Int? {
                let record = try database.read { db in
                    try ConversationMessageRecord.fetchOne(db, key: messageID)
                }
                // Deliberately the LENIENT accessor — this is what the UI and the
                // naive assertion use, and it is what conflates the faults.
                return record?.citations?.count
            }

            // (1) good row: written with ONE citation whose fields are nil.
            let good = try storage.addMessage(
                conversationID: conversationID,
                role: .assistant,
                content: "正常样本",
                citations: [Citation(sourceID: UUID(), title: nil, snippet: nil, url: nil)]
            )
            // (2) corrupt blob: bytes present, undecodable.
            let corrupt = try storage.addMessage(
                conversationID: conversationID,
                role: .assistant,
                content: "损坏样本",
                citations: [Citation(sourceID: UUID(), title: nil, snippet: nil, url: nil)]
            )
            try database.write { db in
                try db.execute(
                    sql: "UPDATE conversation_messages SET citationsJSON = x'DEADBEEF' WHERE id = ?",
                    arguments: [corrupt.id]
                )
            }
            // (3) NULL column: what an encode failure leaves behind.
            let nullColumn = try storage.addMessage(
                conversationID: conversationID,
                role: .assistant,
                content: "空列样本",
                citations: [Citation(sourceID: UUID(), title: nil, snippet: nil, url: nil)]
            )
            try database.write { db in
                try db.execute(
                    sql: "UPDATE conversation_messages SET citationsJSON = NULL WHERE id = ?",
                    arguments: [nullColumn.id]
                )
            }
            // (4) a row that was never written: a random id.
            let missing = UUID()

            let goodCount = try countClause(for: good.id)
            let corruptCount = try countClause(for: corrupt.id)
            let nullCount = try countClause(for: nullColumn.id)
            let missingCount = try countClause(for: missing)
            log("self-test: info — count clause by state: good=\(String(describing: goodCount)) "
                + "corrupt=\(String(describing: corruptCount)) "
                + "nullColumn=\(String(describing: nullCount)) "
                + "missing=\(String(describing: missingCount))")

            // Every faulty state must read back as "not exactly one", and the good
            // one as exactly one. If any faulty state also yielded 1, the count
            // clause would not be load-bearing and this scenario would be vacuous.
            return goodCount == 1
                && corruptCount != 1
                && nullCount != 1
                && missingCount != 1
        }

        // C27 fixture coverage, through the store: `nil`, `""` and `"   "` must
        // all render the same way. `""` is reachable from a server
        // (`{"snippet": ""}`) and is precisely the input a `snippet != nil` gate
        // would wrongly treat as displayable, so it is written and read back here
        // rather than assumed to be covered elsewhere.
        check("roundtrip-nil", "nil / \"\" / \"   \" round-trip to one and the same rendered result") {
            guard let conversationID else { return false }
            let database = try ReaderDatabase(path: storeURL.path)
            let storage = ConversationStorage(database: database)
            let inputs: [(label: String, content: String, citation: Citation)] = [
                ("nil", "渲染样本 nil",
                 Citation(sourceID: UUID(), title: nil, snippet: nil, url: nil)),
                ("\"\"", "渲染样本 empty",
                 Citation(sourceID: UUID(), title: "", snippet: "", url: nil)),
                ("\"   \"", "渲染样本 blank",
                 Citation(sourceID: UUID(), title: "   ", snippet: "   ", url: nil)),
            ]

            var rendered: [String] = []
            for input in inputs {
                _ = try storage.addMessage(
                    conversationID: conversationID,
                    role: .assistant,
                    content: input.content,
                    citations: [input.citation]
                )
                // A fresh read, so this exercises the stored BLOB rather than the
                // object graph that was just handed back.
                let record = try database.read { db in
                    try ConversationMessageRecord
                        .filter(ConversationMessageRecord.Columns.conversationID == conversationID)
                        .filter(ConversationMessageRecord.Columns.content == input.content)
                        .fetchOne(db)
                }
                guard let record, let back = try record.decodedCitations(), back.count == 1 else {
                    log("self-test: FAIL — \(input.label) did not come back as exactly one citation")
                    return false
                }
                let element = back[0]
                log("self-test: info — \(input.label): stored title=\(String(describing: element.title)) "
                    + "snippet=\(String(describing: element.snippet)) "
                    + "→ displayTitle=[\(element.displayTitle)] showsSnippet=\(element.showsSnippet)")
                // Only the *rendered derivation* has to agree across the inputs;
                // the stored values legitimately differ (`nil` vs `""`).
                rendered.append("\(element.displayTitle)|\(element.showsSnippet ? "snippet" : "no-snippet")")
            }

            let expected = "\(Citation.untitledPlaceholder)|no-snippet"
            guard Set(rendered).count == 1, rendered.first == expected else {
                log("self-test: FAIL — rendered results differ across inputs: \(rendered) (expected all \(expected))")
                return false
            }
            log("self-test: info — all three inputs render as [\(expected)]")
            return true
        }

        // POISONED-INPUT PROBE, as a conjunction: `wrote && caught`.
        //
        // `caught` alone is not enough. A poisoned input can "fail" for the wrong
        // reason — the write was silently skipped, the store was unwritable, the
        // insert hit an FK error, the path fell back to another database — and the
        // scenario would then cite that failure as proof that its assertion has
        // teeth, while in fact nothing was tested. So the probe first requires
        // evidence that the poison **reached the database** (the raw blob contains
        // the sentinel text), and only then that the assertion caught it.
        //
        // It also prints the poisoned **read-back value**, because a bare "the
        // poison was caught" line cannot be checked by a reader.
        check("roundtrip-nil", "POISONED-INPUT PROBE: wrote && caught (both required)") {
            guard let conversationID else { return false }
            let database = try ReaderDatabase(path: storeURL.path)
            let storage = ConversationStorage(database: database)
            let marker = "投毒哨兵串-\(UUID().uuidString)"
            let poisoned = Citation(sourceID: UUID(), title: "无标题来源", snippet: marker, url: nil)
            let probe = try storage.addMessage(
                conversationID: conversationID,
                role: .assistant,
                content: "持久化投毒样本",
                citations: [poisoned]
            )

            // Re-read from the database, not from the returned object graph: the
            // question is whether the *stored* row carries the sentinel.
            guard let record = try database.read({ db in
                try ConversationMessageRecord.fetchOne(db, key: probe.id)
            }) else {
                log("self-test: FAIL — self-test is vacuous: the poisoned row could not be read back at all")
                return false
            }
            let rawText: String? = record.citationsJSON.flatMap { String(data: $0, encoding: .utf8) }
            let wrote = (rawText?.contains(marker) ?? false)
            log("self-test: info — poisoned blob written=\(wrote) raw=[\(String(describing: rawText))]")

            let readBack = try record.decodedCitations() ?? []
            let rendered = readBack.map { "title=\(String(describing: $0.title)) snippet=\(String(describing: $0.snippet))" }
            let caught = !readBack.isEmpty && !SentinelGuardSelfTest.violations(in: readBack).isEmpty
            log("self-test: info — poisoned read-back count=\(readBack.count) \(rendered.joined(separator: " | "))")
            log("self-test: info — probe verdict: wrote=\(wrote) caught=\(caught) ⇒ \(wrote && caught ? "assertion has teeth" : "VACUOUS")")

            if !wrote {
                log("self-test: FAIL — self-test is vacuous: the poisoned write never landed, so nothing was proven "
                    + "(a silently skipped write, an unwritable store or a fallback path all look like this)")
                return false
            }
            if !caught {
                log("self-test: FAIL — self-test is vacuous: the poisoned citation was judged compliant")
                return false
            }
            return true
        }

        // Vacuity check (captain's requirement): the positive assertions above are
        // only evidence if the *same* predicate can also fail. So the predicate is
        // expressed once, and applied to two inputs — a genuinely nil citation and
        // a known-poisoned one. If the poison is also judged compliant, this
        // scenario is measuring nothing and says so, in those words, before
        // returning non-zero.
        check("roundtrip-nil", "VACUITY CHECK: the same predicate fails on a poisoned citation") {
            let predicate: ([Citation]) -> Bool = { SentinelGuardSelfTest.violations(in: $0).isEmpty }
            let forNil = predicate([Citation(sourceID: UUID(), title: nil, snippet: nil, url: nil)])
            let poison = Citation(sourceID: UUID(), title: "无标题来源", snippet: "", url: nil)
            let forPoison = predicate([poison])
            log("self-test: info — vacuity: predicate holds for nil=\(forNil) holds for poison=\(forPoison)")
            if forPoison {
                log("self-test: FAIL — self-test is vacuous: the poisoned citation "
                    + "(title=\"无标题来源\", snippet=\"\") was judged compliant, so the "
                    + "positive assertions above cannot distinguish good from poisoned input")
                return false
            }
            return forNil
        }
    }

    // MARK: - Scenario: attribution-nil

    /// Proves that an unattributable row is never presented as a known producer.
    ///
    /// Two inputs, because one run must catch two different regressions:
    ///   · legacy shape (`nil`/`nil`)        — a row written before the
    ///     attribution columns existed;
    ///   · mixed shape (`offline`/`nil`)     — a predicate that only checks
    ///     `provider`, which would render a confident badge for a row whose
    ///     degradation state was never written.
    ///
    /// **What this scenario does *not* prove.** A reverse control settled this: a
    /// variant that reads `isDegraded` first and `provider` second passes every
    /// row here, because both nil-checks still funnel into `.unknown`. The
    /// contract fixes the *criterion* (`provider == nil` ⇒ unknown), not the
    /// order in which the two columns are examined — so column order is a code
    /// convention, not a behaviour, and no input can falsify it. Treating order
    /// as testable would invite someone to "prove ordering" and get a green run
    /// from a reordered implementation.
    ///
    /// The real, falsifiable property is the one asserted below: an unknown row
    /// must not be *promoted* to a known producer. The negative control at the
    /// end of this scenario demonstrates exactly that failure.
    private static func scenarioAttributionNil() {
        struct Row {
            let label: String
            let provider: AIProviderKind?
            let isDegraded: Bool?
            let expectUnknown: Bool
        }

        let rows: [Row] = [
            Row(label: "legacy assistant (both NULL)", provider: nil, isDegraded: nil, expectUnknown: true),
            Row(label: "mixed: provider set, isDegraded NULL", provider: .cloud, isDegraded: nil, expectUnknown: true),
            Row(label: "mixed: offline set, isDegraded NULL", provider: .offline, isDegraded: nil, expectUnknown: true),
            Row(label: "S1 unconfigured", provider: .offline, isDegraded: false, expectUnknown: false),
            Row(label: "S2 unreachable", provider: .offline, isDegraded: true, expectUnknown: false),
            Row(label: "S3 cloud", provider: .cloud, isDegraded: false, expectUnknown: false),
        ]

        for row in rows {
            check("attribution-nil", "\(row.label) → \(row.expectUnknown ? "unknown" : "known")") {
                let display = attributionDisplay(
                    provider: row.provider,
                    isDegraded: row.isDegraded,
                    reason: "连接超时"
                )
                log("self-test: info — state=\(display) badge=\(display.badgeText ?? "<none>")")

                if row.expectUnknown {
                    guard display == .unknown else { return false }
                    // An unknown producer must not be advertised as offline, and
                    // must not carry a degradation badge.
                    guard display.badgeText == nil else { return false }
                    guard !display.isDegraded else { return false }
                    return true
                }
                guard display != .unknown else { return false }
                return true
            }
        }

        // S1/S2 must stay distinguishable — C18: one tells the user to configure,
        // the other to retry. Collapsing them would satisfy "not unknown" while
        // losing the actionable difference.
        check("attribution-nil", "S1 and S2 are not collapsed into one state") {
            let s1 = attributionDisplay(provider: .offline, isDegraded: false)
            let s2 = attributionDisplay(provider: .offline, isDegraded: true, reason: "连接超时")
            log("self-test: info — S1=\(s1) badge=\(s1.badgeText ?? "<none>") | "
                + "S2=\(s2) badge=\(s2.badgeText ?? "<none>")")
            return s1 == .unconfigured && s1.isDegraded == false
                && s2 == .degradedFallback(reason: "连接超时") && s2.isDegraded == true
        }

        // Badge text is frozen by §6.3; assert it verbatim so a copy drift fails
        // here rather than being discovered visually (which is impossible here).
        check("attribution-nil", "badge copy matches the frozen strings") {
            guard attributionDisplay(provider: .offline, isDegraded: false).badgeText == "未配置",
                  attributionDisplay(provider: .offline, isDegraded: true).badgeText == "不可达",
                  attributionDisplay(provider: .cloud, isDegraded: false).badgeText == "cloud"
            else { return false }
            return true
        }

        // NEGATIVE CONTROL — the table above is only evidence if it can fail.
        //
        // The wrong implementation modelled here is the plausible one: treat a
        // missing `provider` as "offline / not configured", i.e. *promote* an
        // unknown row to a known producer. Its badge is「未配置」and it is not
        // degraded, so it satisfies the "no degraded badge" half of the contract
        // while asserting something the row never said.
        //
        // The control asserts the failure, and is also run against the real
        // function: if `attributionDisplay` ever started promoting unknown rows,
        // this check would flip from PASS to FAIL.
        check("attribution-nil", "NEGATIVE CONTROL: promoting an unknown row to a known one is detected") {
            // 1. The control implementation must disagree with the contract on a
            //    legacy row — i.e. the property really is falsifiable.
            func collapsed(_ provider: AIProviderKind?, _ isDegraded: Bool?) -> AttributionDisplay {
                // The bug, in order: an absent `isDegraded` reads as "not
                // degraded" and an absent `provider` as "offline", so a row with
                // no attribution at all acquires one it never had.
                let effectiveKind = provider ?? .offline
                let effectiveDegraded = isDegraded ?? false
                if effectiveKind == .offline {
                    return effectiveDegraded ? .degradedFallback(reason: "") : .unconfigured
                }
                return .provided(kind: effectiveKind, model: nil)
            }

            let legacyCollapsed = collapsed(nil, nil)
            let legacyReal = attributionDisplay(provider: nil, isDegraded: nil)
            log("self-test: info — negative control: collapsed(legacy)=\(legacyCollapsed) "
                + "badge=\(legacyCollapsed.badgeText ?? "<none>") vs real=\(legacyReal)")

            // The control must produce a *known* state for a row that has no
            // attribution at all — otherwise it is not modelling the bug.
            guard legacyCollapsed != .unknown else { return false }
            guard legacyCollapsed.badgeText != nil else { return false }
            // And the real implementation must still refuse that promotion.
            guard legacyReal == .unknown, legacyReal.badgeText == nil, !legacyReal.isDegraded else {
                return false
            }
            return true
        }
    }

    // MARK: - Scenario: render-row

    /// Prints the strings a citation row actually renders, across the four input
    /// states for all three fields (contract C27/A35).
    ///
    /// This is the outlet the interface cannot otherwise provide: no role in this
    /// environment can observe the UI (`screencapture` yields an all-black image,
    /// Accessibility returns `-25211 apiDisabled`), so the only checkable claim
    /// is textual. It is deliberately **not** a visual acceptance — see C30. What
    /// it does establish is that the values handed to `Text(...)` are the ones
    /// the accessors derive, because both render sites consume exactly these
    /// members and nothing else.
    ///
    /// The four states exist because `nil` alone is not the interesting case:
    /// once `snippet`/`url` became `String?`, an empty or whitespace-only value
    /// passes an `if let` and would construct a real `Text`, reserving line
    /// height for nothing — the same defect as `Text("")` via a different input.
    private static func scenarioRenderRow() {
        struct Row {
            let label: String
            let title: String?
            let snippet: String?
            let url: String?
        }

        let rows: [Row] = [
            Row(label: "all nil", title: nil, snippet: nil, url: nil),
            Row(label: "all empty", title: "", snippet: "", url: ""),
            Row(label: "all whitespace", title: " \t\n", snippet: "   ", url: " "),
            // Format-class scalars survive `trimmingCharacters`, so they are the
            // inputs a whitespace-only test would wrongly pass.
            Row(label: "format scalars", title: "\u{2060}", snippet: "\u{200C}\u{200D}", url: "\u{FEFF}"),
            Row(label: "has content", title: "真标题", snippet: "有内容", url: "https://ok"),
            // A mark attached to a base letter is content in both variants.
            Row(label: "base + combining mark", title: "e\u{0301}", snippet: "标\u{0301}题", url: nil),
        ]

        for row in rows {
            check("render-row", "\(row.label)") {
                let citation = Citation(
                    sourceID: UUID(),
                    title: row.title,
                    snippet: row.snippet,
                    url: row.url
                )

                // The exact operands the two render sites pass to `Text(...)`.
                let titleText = citation.displayTitle
                let snippetText = citation.displaySnippet
                let urlText = citation.displayURL

                log("self-test: info — [\(row.label)] "
                    + "title=[\(titleText)] "
                    + "showsSnippet=\(citation.showsSnippet) "
                    + "snippet=[\(snippetText ?? "<omitted>")] "
                    + "url=[\(urlText ?? "<omitted>")]")

                // Invariant 1: the title is never empty, for any input.
                guard !titleText.isEmpty else { return false }
                // Invariant 2: `displaySnippet` is non-nil exactly when the
                // decision says to show it — this is what pins the omit path,
                // which `Text(snippet ?? "")` could not express.
                guard (snippetText != nil) == citation.showsSnippet else { return false }
                // Invariant 3: nothing rendered is ever blank or whitespace-only.
                if let snippetText, snippetText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    return false
                }
                if let urlText, urlText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    return false
                }
                // Invariant 4: no rendered value may look like an unformatted
                // optional, which is what a botched interpolation would produce.
                for rendered in [titleText, snippetText ?? "", urlText ?? ""] {
                    if rendered.contains("Optional(") || rendered == "nil" { return false }
                }
                return true
            }
        }

        // The three inputs below must behave *identically*: `nil`, empty and
        // whitespace-only all mean "nothing to show". Asserting the equality
        // rather than the three outcomes separately is what makes a regression in
        // any one of them fail here.
        check("render-row", "nil / \"\" / whitespace are indistinguishable in output") {
            let nilCitation = Citation(sourceID: UUID(), title: nil, snippet: nil, url: nil)
            let emptyCitation = Citation(sourceID: UUID(), title: "", snippet: "", url: "")
            let blankCitation = Citation(sourceID: UUID(), title: "   ", snippet: " \t", url: " ")

            log("self-test: info — nil=[\(nilCitation.displayTitle)]/[\(nilCitation.displaySnippet ?? "<omitted>")] "
                + "empty=[\(emptyCitation.displayTitle)]/[\(emptyCitation.displaySnippet ?? "<omitted>")] "
                + "blank=[\(blankCitation.displayTitle)]/[\(blankCitation.displaySnippet ?? "<omitted>")]")

            return nilCitation.displayTitle == emptyCitation.displayTitle
                && emptyCitation.displayTitle == blankCitation.displayTitle
                && nilCitation.displaySnippet == emptyCitation.displaySnippet
                && emptyCitation.displaySnippet == blankCitation.displaySnippet
                && nilCitation.displayURL == emptyCitation.displayURL
                && emptyCitation.displayURL == blankCitation.displayURL
        }

        // Known boundary, asserted so it is recorded rather than assumed: a lone
        // combining mark counts as content. Kept deliberately — `.nonspacingMark`
        // is "modifies the preceding character", which is a different property
        // from the format/control class, and closing it would cost three more
        // GeneralCategory exclusions for an input that does not occur in practice.
        check("render-row", "known boundary: a lone combining mark counts as content") {
            let markOnly = Citation(sourceID: UUID(), title: "\u{0301}", snippet: nil, url: nil)
            log("self-test: info — lone mark title=[\(markOnly.displayTitle)] showsSnippet=\(markOnly.showsSnippet)")
            return markOnly.displayTitle == "\u{0301}"
        }

        // A title with a real base character plus a mark is content — both
        // variants agree here, which is why the boundary above is narrow.
        check("render-row", "a base character with a combining mark is content") {
            let withBase = Citation(sourceID: UUID(), title: "e\u{0301}", snippet: nil, url: nil)
            return withBase.displayTitle == "e\u{0301}"
        }
    }

    // MARK: - Scenarios: provider paths (A15–A19)
    //
    // These drive the **real** `AICoordinator.ask(_:)`, so the frozen selection
    // order (C5) and the error classification (§3.5) are exercised rather than
    // restated. Endpoints are injected by environment variable so a local stub
    // can play every outcome without touching production configuration or the
    // keychain:
    //
    //   READFLOW_SELFTEST_BASE    protocol://host:port of a local stub. Used by
    //                             success / unauthorized / timeout /
    //                             bad-response / rate-limited, which append the
    //                             scenario name and call `<base>/<name>/chat/completions`.
    //   READFLOW_SELFTEST_MODEL   model name to send (default `stub-model`).
    //   READFLOW_SELFTEST_KEY     API key to send (default `stub-key`).
    //   READFLOW_SELFTEST_EXPECT  content the `success` stub returns (default
    //                             `OK`, which is what contract A16 fixes).
    //   READFLOW_SELFTEST_TIMEOUT seconds for the `timeout` scenario (default is
    //                             the production value; must stay in 10...60s).
    //
    // `s2-unreachable` deliberately needs **no** stub: it dials a port nothing
    // listens on, which is a genuine connection-level failure.

    /// Local stub address, or a closed port when the caller supplied none.
    ///
    /// The fallback is `127.0.0.1:1` rather than a plausible endpoint: with no
    /// stub running, a connection failure is the honest outcome, and the error
    /// scenarios must fail rather than silently pass.
    private static func stubBase() -> String {
        let raw = ProcessInfo.processInfo.environment["READFLOW_SELFTEST_BASE"] ?? ""
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "http://127.0.0.1:1" : trimmed
    }

    private static func stubKey() -> String {
        let raw = ProcessInfo.processInfo.environment["READFLOW_SELFTEST_KEY"] ?? ""
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "stub-key" : trimmed
    }

    private static func stubModel() -> String {
        let raw = ProcessInfo.processInfo.environment["READFLOW_SELFTEST_MODEL"] ?? ""
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "stub-model" : trimmed
    }

    /// The timeout requested by the caller, when it is a usable number.
    ///
    /// Returns `nil` (after printing a WARN) for a non-numeric or out-of-range
    /// value, so the caller falls back to the production default and the reason
    /// is visible in the log rather than silently absorbed.
    private static func injectedTimeout() -> TimeInterval? {
        guard let raw = ProcessInfo.processInfo.environment["READFLOW_SELFTEST_TIMEOUT"] else {
            return nil
        }
        guard let value = TimeInterval(raw) else {
            log("self-test: WARN — READFLOW_SELFTEST_TIMEOUT=\(raw) is not a number; valid range is 10...60s (contract C6); using production default \(CloudAIProvider.defaultTimeout)s")
            return nil
        }
        guard (10...60).contains(value) else {
            // Not clamped: an out-of-range bound is reported and the production
            // default is used, so a typo cannot silently alter what is measured.
            log("self-test: WARN — READFLOW_SELFTEST_TIMEOUT=\(raw) is outside 10...60s (contract C6);"
                + " using production default \(CloudAIProvider.defaultTimeout)s")
            return nil
        }
        return value
    }

    /// One `ask` against the real coordinator, returned as a `Result` so a
    /// scenario can assert *either* a value *or* a specific error case.
    private static func driveAsk(_ coordinator: AICoordinator, query: String = "stub query") -> Result<AIOutcome, Error> {
        let semaphore = DispatchSemaphore(value: 0)
        var outcome: Result<AIOutcome, Error> = .failure(AIProviderError.unreachable)
        Task {
            do { outcome = .success(try await coordinator.ask(AIRequest(query: query))) }
            catch { outcome = .failure(error) }
            semaphore.signal()
        }
        semaphore.wait()
        return outcome
    }

    /// Prints the structured evidence every provider scenario emits.
    private static func report(_ scenario: String, _ outcome: Result<AIOutcome, Error>) {
        switch outcome {
        case .success(let value):
            let response = value.response
            log("self-test: info — \(scenario) provider=\(response.provider.rawValue)"
                + " isDegraded=\(response.isDegraded)"
                + " text=\(response.text)")
            log("self-test: info — \(scenario) fallbacks=\(value.fallbacks.count)"
                + value.fallbacks.map { " {\($0.from.rawValue)->\($0.to.rawValue)}" }.joined())
        case .failure(let error):
            log("self-test: info — \(scenario) threw \(error)")
        }
    }

    /// Renders an error as the case name the assertions compare against.
    private static func errorCase(_ error: Error) -> String {
        guard let providerError = error as? AIProviderError else {
            return String(describing: type(of: error))
        }
        switch providerError {
        case .notConfigured: return "notConfigured"
        case .invalidEndpoint: return "invalidEndpoint"
        case .unreachable: return "unreachable"
        case .timeout: return "timeout"
        case .unauthorized: return "unauthorized"
        case .serverError(let status, _): return "serverError(\(status))"
        case .badResponse: return "badResponse"
        }
    }

    /// S1 — nothing configured. Contract §6.2: structured offline answer, frozen
    /// copy, `isDegraded == false` (not a degradation: it was never enabled).
    private static func scenarioS1Unconfigured() {
        let group = "s1-unconfigured"
        let coordinator = AICoordinator.make(
            cloudConfiguration: CloudConfiguration(baseURL: "", model: "", apiKey: ""),
            localbookProvider: nil
        )
        let outcome = driveAsk(coordinator)
        report(group, outcome)

        check(group, "S1 returns a value rather than throwing") {
            if case .success = outcome { return true }
            return false
        }
        check(group, "S1 provider is .offline") {
            guard case .success(let value) = outcome else { return false }
            return value.response.provider == .offline
        }
        check(group, "S1 isDegraded is false (not enabled, not degraded)") {
            guard case .success(let value) = outcome else { return false }
            return value.response.isDegraded == false
        }
        check(group, "S1 text matches contract §6.2 verbatim") {
            guard case .success(let value) = outcome else { return false }
            return value.response.text == OfflineCopy.notConfigured
        }
        check(group, "S1 took no fallback (nothing was available to try)") {
            guard case .success(let value) = outcome else { return false }
            return value.fallbacks.isEmpty
        }
        check(group, "NEGATIVE CONTROL: S1 copy differs from the S2 copy") {
            guard case .success(let value) = outcome else { return false }
            return value.response.text != OfflineCopy.unreachable(reason: "x")
        }
    }

    /// S2 — configured but unreachable. Same shape, but degraded, with the cause.
    private static func scenarioS2Unreachable() {
        let group = "s2-unreachable"
        let coordinator = AICoordinator.make(
            cloudConfiguration: CloudConfiguration(
                baseURL: "http://127.0.0.1:1", model: stubModel(), apiKey: stubKey()
            ),
            localbookProvider: nil
        )
        let outcome = driveAsk(coordinator)
        report(group, outcome)

        check(group, "S2 returns a value rather than throwing") {
            if case .success = outcome { return true }
            return false
        }
        check(group, "S2 provider is .offline") {
            guard case .success(let value) = outcome else { return false }
            return value.response.provider == .offline
        }
        check(group, "S2 isDegraded is true (a fallback really happened)") {
            guard case .success(let value) = outcome else { return false }
            return value.response.isDegraded == true
        }
        check(group, "S2 text matches contract §6.2 verbatim") {
            guard case .success(let value) = outcome else { return false }
            return value.response.text
                == OfflineCopy.unreachable(reason: AIProviderError.unreachable.localizedDescription)
        }
        check(group, "S2 recorded exactly one fallback cloud->offline") {
            guard case .success(let value) = outcome else { return false }
            return value.fallbacks.count == 1
                && value.fallbacks[0].from == .cloud
                && value.fallbacks[0].to == .offline
        }
        check(group, "NEGATIVE CONTROL: S2 copy differs from the S1 copy") {
            guard case .success(let value) = outcome else { return false }
            return value.response.text != OfflineCopy.notConfigured
        }
    }

    /// Cloud success — 2xx carrying `choices[0].message.content`.
    private static func scenarioCloudSuccess() {
        let group = "success"
        let coordinator = AICoordinator.make(
            cloudConfiguration: CloudConfiguration(baseURL: "\(stubBase())/success", model: stubModel(), apiKey: stubKey()),
            localbookProvider: nil
        )
        let outcome = driveAsk(coordinator)
        report(group, outcome)

        check(group, "cloud 200 returns a value") {
            if case .success = outcome { return true }
            return false
        }
        check(group, "provider is .cloud") {
            guard case .success(let value) = outcome else { return false }
            return value.response.provider == .cloud
        }
        check(group, "isDegraded is false on success") {
            guard case .success(let value) = outcome else { return false }
            return value.response.isDegraded == false
        }
        check(group, "text is the stub content and non-empty") {
            guard case .success(let value) = outcome else { return false }
            // Default is the value the frozen contract fixes for the mock
            // (`A16`: `{"choices":[{"message":{"content":"OK"}}]}` → `text == "OK"`).
            // Overridable so pointing this scenario at a different stub does not
            // turn a correct implementation into a failure — the requirement was
            // that *every* stub parameter be configurable, and this was the one
            // that was hardcoded.
            let expected = ProcessInfo.processInfo.environment["READFLOW_SELFTEST_EXPECT"] ?? "OK"
            return value.response.text == expected && !value.response.text.isEmpty
        }
        check(group, "no fallback was taken") {
            guard case .success(let value) = outcome else { return false }
            return value.fallbacks.isEmpty
        }
    }

    /// 401 must surface rather than degrade (C5.2 / C17 exception list).
    private static func scenarioCloudUnauthorized() {
        let group = "unauthorized"
        let coordinator = AICoordinator.make(
            cloudConfiguration: CloudConfiguration(baseURL: "\(stubBase())/unauthorized", model: stubModel(), apiKey: stubKey()),
            localbookProvider: nil
        )
        let outcome = driveAsk(coordinator)
        report(group, outcome)

        check(group, "401 throws instead of returning") {
            if case .failure = outcome { return true }
            return false
        }
        check(group, "the thrown case is .unauthorized") {
            guard case .failure(let error) = outcome else { return false }
            return errorCase(error) == "unauthorized"
        }
        check(group, "NEGATIVE CONTROL: no offline degradation substituted") {
            if case .success(let value) = outcome {
                return value.response.provider != .offline && value.response.isDegraded == false
            }
            return true   // throwing is the required behaviour
        }
    }

    /// Timeout must be bounded and classified, never confused with `unreachable`.
    ///
    /// The bound asserted here is the **production** request timeout, not the
    /// `READFLOW_SELFTEST_TIMEOUT` value: `CloudConfiguration` carries no timeout
    /// and `AICoordinator.make` constructs `CloudAIProvider` with its default, so
    /// there is currently no injection path. Asserting against the real constant
    /// keeps this scenario reproducible (the stub must outlast that constant),
    /// and comparing the observed wait against it means a future change to the
    /// default is caught rather than silently tolerated.
    private static func scenarioCloudTimeout() {
        let group = "timeout"
        // The bound is now injectable, so A18 can be re-verified quickly instead
        // of always paying the production default. Unset keeps the production
        // value, and `CloudAIProvider.init` rejects anything outside 10...60s.
        let injected = injectedTimeout()          // called once so any WARN prints once
        let bound = injected ?? CloudAIProvider.defaultTimeout
        let coordinator = AICoordinator.make(
            cloudConfiguration: CloudConfiguration(
                baseURL: "\(stubBase())/timeout",
                model: stubModel(),
                apiKey: stubKey(),
                timeout: bound
            ),
            localbookProvider: nil
        )
        let started = Date()
        let outcome = driveAsk(coordinator)
        let elapsed = Date().timeIntervalSince(started)
        report(group, outcome)
        log("self-test: info — \(group) requestTimeout=\(bound)s"
            + " (injected=\(injected != nil))"
            + " elapsed=\(String(format: "%.2f", elapsed))s")

        check(group, "the call returns rather than hanging") {
            if case .success = outcome { return true }
            return false
        }
        check(group, "the degradation records a .timeout cause (not .unreachable)") {
            guard case .success(let value) = outcome else { return false }
            return value.response.text.contains(AIProviderError.timeout.localizedDescription)
        }
        check(group, "elapsed stayed within the request timeout + 2s") {
            elapsed <= bound + 2
        }
        check(group, "the timeout bound is inside the contract C6 range 10...60s") {
            (10...60).contains(bound)
        }
        check(group, "isDegraded is true") {
            guard case .success(let value) = outcome else { return false }
            return value.response.isDegraded == true
        }
        check(group, "NEGATIVE CONTROL: the cause is not the unreachable copy") {
            guard case .success(let value) = outcome else { return false }
            return !value.response.text.contains(AIProviderError.unreachable.localizedDescription)
        }
    }

    /// 2xx whose body is not the expected shape.
    private static func scenarioCloudBadResponse() {
        let group = "bad-response"
        let coordinator = AICoordinator.make(
            cloudConfiguration: CloudConfiguration(baseURL: "\(stubBase())/bad-response", model: stubModel(), apiKey: stubKey()),
            localbookProvider: nil
        )
        let outcome = driveAsk(coordinator)
        report(group, outcome)

        check(group, "an unparseable 200 throws") {
            if case .failure = outcome { return true }
            return false
        }
        check(group, "the thrown case is .badResponse") {
            guard case .failure(let error) = outcome else { return false }
            return errorCase(error) == "badResponse"
        }
        check(group, "NEGATIVE CONTROL: not swallowed into an offline answer") {
            if case .success = outcome { return false }
            return true
        }
    }

    /// 429 is an explicit server error, not a degradation (C28 / §3.5.1).
    private static func scenarioCloudRateLimited() {
        let group = "rate-limited"
        let coordinator = AICoordinator.make(
            cloudConfiguration: CloudConfiguration(baseURL: "\(stubBase())/rate-limited", model: stubModel(), apiKey: stubKey()),
            localbookProvider: nil
        )
        let outcome = driveAsk(coordinator)
        report(group, outcome)

        check(group, "429 throws rather than degrading") {
            if case .failure = outcome { return true }
            return false
        }
        check(group, "the thrown case is .serverError(429)") {
            guard case .failure(let error) = outcome else { return false }
            return errorCase(error) == "serverError(429)"
        }
        check(group, "NEGATIVE CONTROL: no offline answer substituted") {
            if case .success = outcome { return false }
            return true
        }
    }

    // MARK: - Helpers

    /// Renders a blob as a short, printable summary.
    ///
    /// Prints the JSON text rather than a byte count because the interesting
    /// failure is "the sentinel string is sitting in the database", and a count
    /// would hide it.
    private static func describe(_ data: Data?) -> String {
        guard let data else { return "<nil>" }
        return String(data: data, encoding: .utf8) ?? "<\(data.count) bytes>"
    }

    private static func check(_ group: String, _ description: String, _ body: () throws -> Bool) {
        assertions += 1
        do {
            if try body() {
                log("self-test: PASS  [\(group)] \(description)")
            } else {
                failures += 1
                log("self-test: FAIL  [\(group)] \(description)")
            }
        } catch {
            failures += 1
            log("self-test: FAIL  [\(group)] \(description) — threw: \(error)")
        }
    }

    private static func log(_ message: String) {
        FileHandle.standardError.write(Data("[ReadFlow] \(message)\n".utf8))
    }
}
