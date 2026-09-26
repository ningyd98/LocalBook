//
//  PopoverLivePathSelfTest.swift
//  ReadFlow
//
//  `--self-test popover-send <text>` — drives the **live** answering path and
//  reports which backend actually answered.
//
//  Why this exists, in the captain's words: `A15`–`A19` were verified against the
//  coordinator while production built it *nowhere* — `AIPopoverViewModel` called
//  `LocalBookClient.ask` directly, so a configured cloud endpoint was never used,
//  `OfflineAIProvider` never ran, and every fresh reply was persisted without
//  attribution. Component-level green said nothing about the product path.
//
//  So this outlet deliberately does **not** construct a coordinator. It builds a
//  `AIPopoverViewModel` and calls `sendQuery(_:)` — the same method the popover's
//  send button calls — then reads the assistant row **back out of the database**
//  and prints the two attribution columns as stored. If the live path were still
//  bypassing the coordinator, the columns would come back NULL and this scenario
//  would fail. That is the difference between "the component works" and "the
//  product uses it".
//
//  Scenarios (both run on a private store; the shared store and the user's real
//  defaults are saved and restored around the run):
//    · `cloud` mode — cloud configured at `READFLOW_SELFTEST_BASE` and LocalBook
//      unconfigured. Whether the endpoint answers decides the expected outcome, so
//      the expectation is chosen from `READFLOW_SELFTEST_EXPECT` rather than
//      guessed:
//        · `expect=success` ⇒ `provider=cloud`, `isDegraded=0`, no fallback, and
//          the persisted row must say `cloud` — this is the scenario that proves a
//          user's endpoint is actually used, which the old live path never did;
//        · `expect=degraded` (default when nothing listens) ⇒ `provider=offline`,
//          `isDegraded=1`, with a recorded `cloud → offline` fallback.
//      A run whose endpoint answers but whose expectation says "degraded" fails
//      loudly instead of passing for the wrong reason.
//    · `unconfigured` — no cloud configuration at all ⇒ expected `offline`,
//      `isDegraded=0`, no fallback: S1 is "not enabled", not "degraded".
//
//  Exit codes: 0 when every assertion held, 1 on failure, 2 usage/misuse.
//

import Foundation
import GRDB

@MainActor
enum PopoverLivePathSelfTest {

    /// Scenario name accepted by `--self-test`.
    static let scenario = "popover-send"

    private enum Exit {
        static let passed: Int32 = 0
        static let failed: Int32 = 1
        static let usage: Int32 = 2
    }

    private static var failures = 0
    private static var assertions = 0

    private static let cloudKeys = [
        CloudSettingsStore.Key.baseURL,
        CloudSettingsStore.Key.model,
        CloudSettingsStore.Key.enabled,
    ]

    static func run(query: String?) -> Int32 {
        guard let query, !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            log("popover-send: FAIL — usage: --self-test \(scenario) <text>")
            return Exit.usage
        }

        log("popover-send: start query=\"\(query)\"")

        // The user's real defaults must not be disturbed by a verification run.
        let savedDefaults = cloudKeys.map { ($0, UserDefaults.standard.object(forKey: $0)) }
        defer {
            for (key, value) in savedDefaults {
                if let value {
                    UserDefaults.standard.set(value, forKey: key)
                } else {
                    UserDefaults.standard.removeObject(forKey: key)
                }
            }
        }

        let workDir: URL
        do {
            let dir = FileManager.default.temporaryDirectory
                .appendingPathComponent("readflow-popover-send-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            workDir = dir
        } catch {
            log("popover-send: FAIL — could not create workspace: \(error)")
            return Exit.failed
        }
        defer { try? FileManager.default.removeItem(at: workDir) }

        let storeURL = workDir.appendingPathComponent("readflow.sqlite")

        if let mustSurface = mustSurfaceMode() {
            // Runs *instead of* the cloud scenario: with a must-surface stub in
            // place the endpoint answers with an error status, so the
            // success/degradation expectations cannot hold and running them would
            // only produce noise that hides the signal.
            scenarioMustSurface(
                query: query,
                storeURL: storeURL,
                expectedError: mustSurface
            )
        } else {
            scenarioCloud(
                query: query,
                storeURL: storeURL,
                label: "cloud",
                expectSuccess: expectedCloudOutcomeIsSuccess()
            )
        }
        scenarioUnconfigured(query: query, storeURL: storeURL, label: "unconfigured")

        if failures == 0 {
            log("popover-send: PASS (\(assertions) assertions)")
            return Exit.passed
        }
        log("popover-send: FAIL (\(failures) of \(assertions) assertions failed)")
        return Exit.failed
    }

    // MARK: - Scenario A: cloud configured but unreachable

    /// Whether the caller expects the configured endpoint to answer.
    ///
    /// Read from the environment so a run against a **working** stub is not
    /// scored as a failure: the first version assumed "unreachable" and reported
    /// three failures against a healthy endpoint — every value it printed was
    /// correct, only the expectation was wrong.
    /// Which must-surface error the stub is expected to return.
    ///
    /// Unset ⇒ this run is a success/degradation run. Set ⇒ the endpoint answers
    /// with that error and the error path must be asserted instead of an outcome.
    private static func mustSurfaceMode() -> String? {
        let raw = (ProcessInfo.processInfo.environment["READFLOW_SELFTEST_MUST_SURFACE"] ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        guard ["unauthorized", "bad-response", "server-error"].contains(raw) else { return nil }
        return raw
    }

    /// Drives the live path against an endpoint that answers with a must-surface
    /// error and asserts the **error** path.
    ///
    /// Why this scenario has to exist: without it, contract `A40`④ ("a backend
    /// that answers with `unauthorized` / `badResponse` / `serverError` must reach
    /// the caller instead of degrading") was only ever *inferred* from the
    /// coordinator's source. Two independent reviewers hit the same trap reaching
    /// for a fixture — both pointed at a **live** server (a local Caddy returning
    /// 404, and a 401 stub) and read the resulting failure as a product defect,
    /// when the product was behaving correctly and the fixture simply was not
    /// triggering the condition it claimed. This case makes ④ fixtured.
    private static func scenarioMustSurface(
        query: String,
        storeURL: URL,
        expectedError: String
    ) {
        configureCloudForSelfTest()
        let label = "must-surface:\(expectedError)"

        driveLivePath(query: query, storeURL: storeURL, label: label, allowFailure: true) { outcome, rowCount, _, model in
            check(label, "no answer was produced (the error was not degraded into one)") {
                log("popover-send: info — outcome=\(outcome == nil ? "nil" : "present")")
                return outcome == nil
            }
            check(label, "no assistant row was written for the failed question") {
                log("popover-send: info — assistantRows=\(rowCount)")
                return rowCount == 0
            }
            check(label, "the failure is surfaced to the user, not swallowed") {
                let message = model?.error ?? ""
                log("popover-send: info — surfaced error=[\(message)]")
                return !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            }
            check(label, "a system notice is appended instead of a fabricated answer") {
                guard let model else { return false }
                let notice = model.messages.last
                log("popover-send: info — last message role=\(notice.map { "\($0.role)" } ?? "none")")
                return notice?.role == .system
            }
        }
    }

    private static func expectedCloudOutcomeIsSuccess() -> Bool {
        let raw = (ProcessInfo.processInfo.environment["READFLOW_SELFTEST_EXPECT"] ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
        // `degraded` is the only value meaning "the endpoint will not answer".
        // Anything else — including another project's `STUB-OK` marker — means the
        // caller believes the endpoint answers. Treating unknown values as
        // "degraded" is what made a healthy endpoint score as a failure once.
        if raw.isEmpty { return false }
        return raw != "degraded"
    }

    /// Configures a cloud endpoint, then drives the live path.
    ///
    /// `LocalBookClient` is left unconfigured so the first backend is skipped for a
    /// stated reason rather than by accident (its `isConfigured()` is false). The
    /// expected outcome is therefore a **recorded fallback to offline**: the
    /// coordinator tried the cloud, failed at the connection level, and degraded —
    /// which must be visible both in the response and in the persisted columns.
    private static func scenarioCloud(
        query: String,
        storeURL: URL,
        label: String,
        expectSuccess: Bool
    ) {
        configureCloudForSelfTest()
        let mode = expectSuccess ? "expect=success" : "expect=degraded"

        driveLivePath(query: query, storeURL: storeURL, label: label, allowFailure: true) { outcome, rowCount, persisted, model in
            check(label, "endpoint reachability behaviour matches \(mode)") {
                // Precondition, checked inside this assertion rather than as an
                // extra one so the default run keeps its original count and
                // labels — evidence already recorded must not be invalidated by a
                // new switch merely existing.
                //
                // If the endpoint answered with an error the coordinator must
                // surface, there is no outcome and no fallback, and a degradation
                // expectation would be satisfied *vacuously*. Two reviewers
                // reached for exactly that fixture (a local Caddy answering 404)
                // and read the result as a product defect; the product was correct
                // and the fixture was misfiring. Fail loudly, with the reason, so
                // the run cannot pass having tested nothing.
                if let model, !expectSuccess, model.lastSendFailed {
                    log("popover-send: FAIL [\(label)] the endpoint answered with a must-surface error, "
                        + "so this run did not test an unreachable endpoint")
                    log("popover-send: hint — re-run with "
                        + "READFLOW_SELFTEST_MUST_SURFACE=unauthorized|bad-response|server-error")
                    return false
                }
                guard let outcome else { return false }
                let fallbacks = outcome.fallbacks.map { "\($0.from)→\($0.to): \($0.reason)" }
                log("popover-send: info — fallbacks=\(fallbacks) provider=\(outcome.response.provider.rawValue) isDegraded=\(outcome.response.isDegraded)")

                if expectSuccess {
                    // The cloud backend answered: the user's configured endpoint
                    // was used, and nothing degraded.
                    return outcome.response.provider == .cloud
                        && outcome.response.isDegraded == false
                        && outcome.fallbacks.isEmpty
                }
                // Cloud was configured but unreachable: the coordinator must have
                // tried it and degraded, not skipped straight to offline.
                return outcome.response.provider == .offline
                    && outcome.response.isDegraded == true
                    && outcome.fallbacks.contains { $0.from == .cloud }
            }
            check(label, "the answer carries non-empty text") {
                guard let outcome else { return false }
                log("popover-send: info — text=[\(outcome.response.text)]")
                return !outcome.response.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            }
            check(label, "persisted row records provider=\(expectSuccess ? "cloud" : "offline")") {
                guard let persisted else { return false }
                log("popover-send: info — persisted provider=\(String(describing: persisted.provider)) isDegraded=\(String(describing: persisted.isDegraded))")
                if expectSuccess {
                    return persisted.provider == AIProviderKind.cloud.rawValue
                        && persisted.isDegraded == false
                }
                return persisted.provider == AIProviderKind.offline.rawValue
                    && persisted.isDegraded == true
            }
        }
    }

    // MARK: - Scenario B: nothing configured (S1)

    /// Clears every cloud key, so the coordinator must be built without a cloud
    /// backend at all. The expected outcome is S1: `offline` with
    /// `isDegraded = false` — "not enabled", which is *not* a degradation, and no
    /// fallback is recorded because nothing was tried.
    private static func scenarioUnconfigured(query: String, storeURL: URL, label: String) {
        for key in cloudKeys {
            UserDefaults.standard.removeObject(forKey: key)
        }

        driveLivePath(query: query, storeURL: storeURL, label: label, allowFailure: false) { outcome, _, persisted, _ in
            check(label, "nothing was tried, so no fallback is recorded") {
                let fallbacks = outcome?.fallbacks ?? []
                log("popover-send: info — fallbacks=\(fallbacks.count)")
                return fallbacks.isEmpty
            }
            check(label, "S1 is not a degradation: isDegraded must be false") {
                guard let outcome else { return false }
                log("popover-send: info — provider=\(outcome.response.provider.rawValue) isDegraded=\(outcome.response.isDegraded)")
                return outcome.response.provider == .offline && outcome.response.isDegraded == false
            }
            check(label, "the S1 answer uses the frozen copy") {
                guard let outcome else { return false }
                let matches = outcome.response.text == OfflineCopy.notConfigured
                log("popover-send: info — text=\"\(outcome.response.text)\"")
                return matches
            }
            check(label, "persisted row records provider=offline, isDegraded=0") {
                guard let persisted else { return false }
                log("popover-send: info — persisted provider=\(String(describing: persisted.provider)) isDegraded=\(String(describing: persisted.isDegraded))")
                return persisted.provider == AIProviderKind.offline.rawValue && persisted.isDegraded == false
            }
        }
    }

    // MARK: - The live path

    /// Builds a view model, sends one query **through its own send method**, then
    /// reads the assistant row back from the database.
    ///
    /// Everything asserted by the callers comes from this: `outcome` is what the
    /// live path decided, `assistantRows` is how many assistant rows it actually
    /// wrote, and the model exposes the surfaced error. No coordinator is
    /// constructed here — that would defeat the purpose.
    /// - Parameter allowFailure: when `false` (the success/degradation cases) a
    ///   thrown `sendQuery` is reported as an instrument failure, because those
    ///   scenarios exist precisely to observe an outcome. The must-surface case
    ///   passes `true`: there, *throwing* is the expected behaviour, so aborting
    ///   the fixture on it would make the scenario untestable.
    private static func driveLivePath(
        query: String,
        storeURL: URL,
        label: String,
        allowFailure: Bool,
        body: (AIOutcome?, Int, ConversationMessageRecord?, AIPopoverViewModel?) -> Void
    ) {
        do {
            let database = try ReaderDatabase(path: storeURL.path)

            // A real highlight to hang the conversation on: `conversations` has a
            // foreign key to `highlights`, so the chain must exist first.
            let source = Source(type: .web, title: "实时路径语料", url: "https://example.com/live")
            try database.createSource(source)
            let highlight = Highlight(
                sourceID: source.id,
                selectedText: "实时路径需要一条高亮行",
                contextBefore: "",
                contextAfter: ""
            )
            try database.createHighlight(highlight)

            let storage = ConversationStorage(database: database)
            let model = AIPopoverViewModel(
                highlightID: highlight.id,
                sourceID: source.id,
                context: "实时路径上下文",
                storage: storage,
                database: database,
                keychain: Self.inMemoryKeychain
            )

            // Wait by **pumping the run loop**, not by blocking on a semaphore.
            // A semaphore would hold the main thread, and `sendQuery` is
            // `@MainActor`, so it could never resume — the first version of this
            // inlet timed out at 30s in exactly that way. Running the loop keeps
            // the main actor free and also lets URLSession deliver its callbacks.
            // Drives the **public** method the send button calls, not the throwing
            // core — otherwise the wrapper that surfaces the error would never run
            // and the scenario would assert behaviour it had bypassed. A small
            // box reports whether the core threw, observed from the same actor.
            var finished = false
            Task { @MainActor in
                // The public method, exactly as the send button calls it.
                await model.sendQuery(query)
                finished = true
            }
            let deadline = Date().addingTimeInterval(30)
            while !finished && Date() < deadline {
                RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.05))
            }
            if !finished {
                log("popover-send: FAIL [\(label)] sendQuery did not finish within 30s")
                failures += 1
                return
            }

            // Read the row back — the whole point is that the *database* shows what
            // the live path wrote, not that an in-memory object holds it.
            //
            // Scoped to **this** run's conversation. Both scenarios share one store,
            // so an unscoped `role == "assistant"` query returns the *first* row
            // ever written: the first version of this inlet did exactly that and
            // reported Scenario A's attribution for Scenario B, which looked like a
            // code bug and was a fixture bug.
            let conversation = try database.read { db in
                try Conversation
                    .filter(Conversation.Columns.highlightID == highlight.id)
                    .order(Conversation.Columns.createdAt.desc)
                    .fetchOne(db)
            }
            guard let conversation else {
                log("popover-send: FAIL [\(label)] no conversation row was created")
                failures += 1
                return
            }

            // **Count** assistant rows rather than fetching one. The must-surface
            // case must prove that *no* assistant row was written at all; a
            // `fetchOne` returning nil would be indistinguishable from "the query
            // failed", which is exactly the kind of vacuous pass this inlet exists
            // to avoid.
            let assistantCount = try database.read { db in
                try ConversationMessageRecord
                    .filter(ConversationMessageRecord.Columns.conversationID == conversation.id)
                    .filter(ConversationMessageRecord.Columns.role == "assistant")
                    .fetchCount(db)
            }
            let assistantRecord = try database.read { db in
                try ConversationMessageRecord
                    .filter(ConversationMessageRecord.Columns.conversationID == conversation.id)
                    .filter(ConversationMessageRecord.Columns.role == "assistant")
                    .fetchOne(db)
            }

            if model.lastSendFailed && !allowFailure {
                log("popover-send: FAIL [\(label)] sendQuery threw but this scenario expects an outcome")
                failures += 1
                return
            }

            body(model.lastOutcome, assistantCount, assistantRecord, model)
        } catch {
            log("popover-send: FAIL [\(label)] fixture setup threw: \(error)")
            failures += 1
        }
    }

    // MARK: - Helpers

    /// Points the cloud configuration at the self-test stub address.
    ///
    /// Reuses `READFLOW_SELFTEST_BASE` so this outlet and the coordinator-level
    /// scenarios dial the same place; with nothing set it is a closed port, which
    /// is a genuine connection failure rather than a fabricated one.
    private static func configureCloudForSelfTest() {
        let raw = ProcessInfo.processInfo.environment["READFLOW_SELFTEST_BASE"] ?? ""
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        let base = trimmed.isEmpty ? "http://127.0.0.1:1" : trimmed
        let key = ProcessInfo.processInfo.environment["READFLOW_SELFTEST_KEY"] ?? "stub-key"
        let model = ProcessInfo.processInfo.environment["READFLOW_SELFTEST_MODEL"] ?? "stub-model"

        UserDefaults.standard.set(base, forKey: CloudSettingsStore.Key.baseURL)
        UserDefaults.standard.set(model, forKey: CloudSettingsStore.Key.model)
        UserDefaults.standard.set(true, forKey: CloudSettingsStore.Key.enabled)

        // The key goes to the keychain, never to defaults (K1/K2). A headless
        // process has no login session, so the *real* keychain rejects the write
        // with `errSecMissingEntitlement` (100001) — measured. That would make the
        // cloud configuration unusable and the scenario would pass as
        // "unconfigured" for entirely the wrong reason. The in-memory backend is
        // the same seam `StorageSelfCheck` uses.
        do {
            try Self.inMemoryKeychain.saveCloudAPIKey(key)
        } catch {
            log("popover-send: FAIL — could not store the self-test key: \(error)")
            failures += 1
        }
    }

    /// Keychain substitute for headless runs.
    private static let inMemoryKeychain = KeychainService(backend: InMemoryKeychainBackend())

    private static func check(_ group: String, _ description: String, _ body: () -> Bool) {
        assertions += 1
        if body() {
            log("popover-send: PASS  [\(group)] \(description)")
        } else {
            failures += 1
            log("popover-send: FAIL  [\(group)] \(description)")
        }
    }

    private static func log(_ message: String) {
        FileHandle.standardError.write(Data("[ReadFlow] \(message)\n".utf8))
    }
}
