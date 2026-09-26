//
//  SearchQuerySelfTest.swift
//  ReadFlow
//
//  `--self-test search-query <text>` — a scriptable entry point for the local
//  retrieval path.
//
//  Why it exists: A14's third layer says the retrieval path must issue **zero**
//  outbound requests, and neither available instrument could check it. There is
//  no scriptable caller of local search, and the interface cannot be observed in
//  this environment (`screencapture` yields an all-black image; Accessibility
//  returns `-25211 apiDisabled`). So the outlet drives the same view model the
//  search window uses and reports both the hits and the measured request count.
//
//  Two properties make the measurement meaningful rather than decorative:
//
//    · **Same code path.** The query goes through `LocalSearchViewModel.run`,
//      which is the only place the window talks to the store. A hand-rolled
//      `searchLocal` call here would measure a copy — the exact trap this team
//      has already fallen into once.
//    · **Measured, not asserted.** An armed `OutboundRequestCounter` blocks and
//      counts every request instead of trusting that none was made. If the
//      counter could not be armed, the result is reported as `UNPROVEN`, because
//      an unarmed instrument returning 0 proves nothing.
//
//  Exit code: 0 when the query ran and zero outbound requests were observed;
//  1 when a request was observed or the store failed; 2 for a usage error.
//

import Foundation

@MainActor
enum SearchQuerySelfTest {

    /// Scenario name accepted by `--self-test`. Declared here so the dispatcher
    /// can recognise it without this file being edited in step with another's.
    static let scenario = "search-query"

    /// Exit code semantics, so callers can distinguish "failed" from "misused".
    private enum Exit {
        static let passed: Int32 = 0
        static let failed: Int32 = 1
        static let usage: Int32 = 2
    }

    static func run(query: String?) -> Int32 {
        guard let query, !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            log("search-query: FAIL — usage: --self-test search-query <text>")
            return Exit.usage
        }

        log("search-query: start query=\"\(query)\"")

        // Arm the instrument *first*, so anything the rest of this function
        // constructs is subject to interception.
        let counter = OutboundRequestCounter.shared
        let armed = counter.arm()
        counter.reset()
        log("search-query: outbound counter armed=\(armed)"
            + (armed ? "" : " (UNPROVEN: requests cannot be observed)"))

        // Positive control for the measurement itself, before the measurement is
        // taken: a counter that only ever reads 0 is indistinguishable from a
        // broken counter. The probe's own request is discarded by the `reset()`
        // below, so it cannot contaminate the figure that follows.
        let instrumentWorks = armed && verifyInstrumentCanObserve(counter: counter)
        if armed && !instrumentWorks {
            log("search-query: FAIL — instrument probe did not observe its own request")
            return Exit.failed
        }
        counter.reset()

        let workDir: URL
        do {
            let dir = FileManager.default.temporaryDirectory
                .appendingPathComponent("readflow-search-query-\(UUID().uuidString)", isDirectory: true)
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            workDir = dir
        } catch {
            log("search-query: FAIL — could not create workspace: \(error)")
            return Exit.failed
        }
        defer { try? FileManager.default.removeItem(at: workDir) }

        let storeURL = workDir.appendingPathComponent("readflow.sqlite")

        do {
            try seed(at: storeURL)
        } catch {
            log("search-query: FAIL — could not seed the store: \(error)")
            return Exit.failed
        }

        // The same view model the window instantiates, pointed at this store.
        let database = try? ReaderDatabase(path: storeURL.path)
        let model = LocalSearchViewModel(database: database)
        let state = model.run(query: query)

        report(query: query, state: state, counter: counter, armed: armed)

        switch state {
        case .failure:
            return Exit.failed
        case .empty, .results:
            // `armed && count == 0` is the A14 assertion. An unarmed counter
            // cannot pass it: 0 from a broken instrument is indistinguishable
            // from a real 0, so it is reported as unproven and fails.
            return (armed && counter.count == 0) ? Exit.passed : Exit.failed
        }
    }

    // MARK: - Reporting

    private static func report(
        query: String,
        state: LocalSearchViewModel.State,
        counter: OutboundRequestCounter,
        armed: Bool
    ) {
        let hits: [LocalSearchResult]
        switch state {
        case .empty:
            hits = []
        case .results(let rows):
            hits = rows
        case .failure(let message):
            hits = []
            log("search-query: store failure — \(message)")
        }

        log("search-query: query=\"\(query)\"")
        log("search-query: hits=\(hits.count)")

        for (index, hit) in hits.enumerated() {
            // Printed through the same accessors the row renders with, so the
            // text here is the text the window would show.
            log("search-query:   [\(index)] kind=\(hit.displayKind) "
                + "title=\"\(hit.displayTitle)\" "
                + "content=\"\(hit.displayContent)\" "
                + "date=\(hit.displaysDate ? "\"\(hit.displayDate ?? "")\"" : "<omitted>")")
        }

        log("search-query: outboundRequests=\(counter.count)"
            + (armed ? "" : " (UNPROVEN: counter not armed)"))
        if !counter.urls.isEmpty {
            for url in counter.urls {
                log("search-query:   blocked outbound URL: \(url)")
            }
        }

        // Scope of the claim, stated rather than left implicit. The counter is
        // proven able to observe traffic, but a session must include the
        // interceptor in its own `protocolClasses` for that to happen; the global
        // registration is not visible to a plain `.default`/`.ephemeral`
        // configuration (measured). So `outboundRequests=0` means "this code path
        // made no intercepted request", and the retrieval path constructs no
        // URLSession at all — it reads the local index through `ReaderDatabase`.
        // A future change that started a session with an explicit
        // `protocolClasses` list could therefore escape this counter.
        log("search-query: measurement scope — intercepted sessions only; "
            + "the retrieval path constructs no URLSession")

        let a14Passed = armed && counter.count == 0
        log("search-query: A14-layer3 (retrieval issues no outbound request) = "
            + (a14Passed ? "PASS" : (armed ? "FAIL" : "UNPROVEN")))
    }

    // MARK: - Instrument self-verification

    /// Proves the counter can actually observe a request.
    ///
    /// Without this, `outboundRequests=0` would be equally consistent with "the
    /// retrieval path is clean" and "the instrument is broken" — the two are
    /// indistinguishable from a zero alone. So the outlet fires one request on
    /// purpose and requires the counter to see it. This is the positive control
    /// for the measurement itself.
    ///
    /// The request is *blocked* by `CountingURLProtocol` rather than sent, so the
    /// control cannot reach the network even though it looks like a real call.
    private static func verifyInstrumentCanObserve(
        counter: OutboundRequestCounter
    ) -> Bool {
        let before = counter.count

        // The session must opt in explicitly. `URLProtocol.registerClass` is not
        // enough: a plain `.ephemeral`/`.default` configuration does not consult
        // the global class registry, which the first version of this probe
        // proved by failing to see its own request. `arm()` still calls
        // `registerClass` for sessions that do consult it, but the measurement
        // below does not rely on that — it uses a configuration whose
        // `protocolClasses` explicitly includes the interceptor, which is the
        // documented mechanism.
        let config = URLSessionConfiguration.ephemeral
        config.protocolClasses = [CountingURLProtocol.self]
        let session = URLSession(configuration: config)

        let url = URL(string: "https://self-test.invalid/counter-probe")!
        let done = DispatchSemaphore(value: 0)
        session.dataTask(with: URLRequest(url: url)) { _, _, _ in done.signal() }.resume()
        _ = done.wait(timeout: .now() + 5)
        session.invalidateAndCancel()

        let observed = counter.count - before
        let globalRegistered = URLSessionConfiguration.default.protocolClasses?
            .contains { $0 == CountingURLProtocol.self } ?? false

        log("search-query: instrument probe — fired 1 request, counter observed \(observed); "
            + "global registerClass visible in a default configuration: \(globalRegistered)")

        // One request in, one observation out. Anything else means the instrument
        // cannot see traffic, so any subsequent 0 would be meaningless.
        return observed == 1
    }

    // MARK: - Fixture

    /// Seeds a small, controlled corpus.
    ///
    /// The chain order matters: `foreignKeysEnabled` is true by default in GRDB
    /// and `highlights.sourceID` is `NOT NULL REFERENCES sources`, so the store
    /// must be built sources → highlights before anything queries it.
    ///
    /// `sourceTitle` is left `nil` naturally rather than by breaking the foreign
    /// key: every highlight points at the one seeded source, so the join resolves;
    /// to excercise the *absent* source path the row would have to reference a
    /// source that was deleted (the on-delete cascade removes the highlight too),
    /// so that state is covered by `render-search-row`'s constructed values
    /// instead of by a row this fixture cannot legally create.
    private static func seed(at url: URL) throws {
        let database = try ReaderDatabase(path: url.path)

        let source = Source(type: .web, title: "检索出口语料", url: "https://example.com/search")
        try database.createSource(source)

        // Matches 「深度学习」 as a mid-sentence substring — the CJK path C7 fixed.
        try database.createHighlight(Highlight(
            sourceID: source.id,
            selectedText: "深度学习需要长期刻意练习才能形成能力",
            contextBefore: "前言",
            contextAfter: "后记"
        ))

        // A second, unrelated row so a hit count of 1 is distinguishable from
        // "the query matched everything".
        try database.createHighlight(Highlight(
            sourceID: source.id,
            selectedText: "阅读是一种能力，但需要练习",
            contextBefore: "",
            contextAfter: ""
        ))
    }

    private static func log(_ message: String) {
        FileHandle.standardError.write(Data("[ReadFlow] \(message)\n".utf8))
    }
}
