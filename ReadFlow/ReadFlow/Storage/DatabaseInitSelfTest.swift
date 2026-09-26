//
//  DatabaseInitSelfTest.swift
//  ReadFlow
//
//  `--self-test db-init` — removes the ambiguity around "the app started but no
//  `.sqlite` appeared at READFLOW_DB_PATH".
//
//  Why it is needed: a launched `.app` prints to nowhere a human can see, and
//  `ReaderDatabase.shared` is a lazy `static let`. So two very different states
//  were indistinguishable from outside:
//
//    (i)  the lazy open had not run yet — nothing was ever attempted;
//    (ii) it ran, and the requested location failed, so the store silently
//         continued in one of the fallback locations (or in memory).
//
//  This scenario forces the open (so (i) cannot survive) and then prints, as
//  plain text: the path that was *requested*, the path actually *in use*, the
//  mode, the file's existence/size on disk, and which of the three FTS5 virtual
//  tables exist. Any of those being wrong is a non-zero exit — the caller does
//  not have to interpret the output to get an answer.
//
//  Output contract (t5/V1 can rely on these prefixes):
//    self-test: info — db-init requested=<path|:memory:|<unset>>
//    self-test: info — db-init inUse=<path|in-memory> mode=<onDisk|inMemory>
//    self-test: info — db-init file exists=<yes|no> size=<bytes>
//    self-test: info — db-init fts highlights_fts=<yes|no> notes_fts=<yes|no> reader_items_fts=<yes|no>
//    self-test: info — db-init tables=<comma-separated>
//    self-test: info — db-init verify-with: sqlite3 "<path>" ".tables"
//
//  Self-contained on purpose (own counters, own output prefix) so it can live
//  beside `AISelfTest` without two writers editing one file; registration there
//  is three lines.
//
//  `??` note (C26 / A34): every `??` below formats log text or supplies a
//  diagnostic default (byte count, empty table list). None of them is a
//  `Citation(...)` / `AICitation(...)` construction argument and none is on a
//  path that writes to the store — this file only reads.
//

import Foundation
import GRDB

enum DatabaseInitSelfTest {

    /// The FTS5 virtual tables a migrated store must contain (contract A11).
    static let expectedFTS = ["highlights_fts", "notes_fts", "reader_items_fts"]

    static func run() -> (assertions: Int, failures: Int) {
        var assertions = 0
        var failures = 0

        func check(_ description: String, _ body: () -> Bool) {
            assertions += 1
            if body() {
                log("self-test: PASS  [db-init] \(description)")
            } else {
                failures += 1
                log("self-test: FAIL  [db-init] \(description)")
            }
        }

        let environment = ProcessInfo.processInfo.environment
        let requested = environment["READFLOW_DB_PATH"].flatMap { $0.isEmpty ? nil : $0 }

        log("self-test: info — db-init requested=\(requested ?? "<unset>")")
        log("self-test: info — db-init candidates=\(ReaderDatabase.candidateStoreURLs.map(\.path).joined(separator: " , "))")

        // Touching `.shared` is what forces the lazy `static let`. Asserting the
        // diagnostic is non-nil afterwards is the direct check that the open ran
        // in *this* process — the state that used to be unobservable.
        let shared = ReaderDatabase.shared
        let diagnostic = ReaderDatabase.lastInitDiagnostic
        check("touching ReaderDatabase.shared runs the init once (lazy open is forced)") {
            diagnostic != nil
        }

        guard let diagnostic else {
            log("self-test: FAIL — no init diagnostic after touching .shared; cannot say anything about the store")
            return (assertions, failures)
        }

        // 1. Where it ended up.
        let inUsePath: String?
        switch shared.storageMode {
        case .onDisk(let url):
            inUsePath = url.path
            log("self-test: info — db-init inUse=\(url.path) mode=onDisk")
        case .inMemory(let reason):
            inUsePath = nil
            log("self-test: info — db-init inUse=in-memory mode=inMemory reason=\(reason)")
        }
        log("self-test: info — db-init attempted=\(diagnostic.attemptedPaths.joined(separator: " , "))")
        if !diagnostic.failures.isEmpty {
            for failure in diagnostic.failures {
                log("self-test: info — db-init failure: \(failure)")
            }
        }
        log("self-test: info — db-init log-file=\(diagnostic.logFile ?? "<not writable>")")

        // 2. The requested location must be honored, not merely attempted. A
        //    fallback that keeps the app running is correct behaviour, but a
        //    silent one is indistinguishable from "nothing happened at all" —
        //    which is exactly the report this scenario answers.
        let wantsMemory = ReaderDatabase.overrideRequestsInMemory
        if let requested, !wantsMemory {
            check("the requested READFLOW_DB_PATH is the store actually in use") {
                inUsePath == requested
            }
            if inUsePath != requested {
                log("self-test: FAIL — requested \(requested) but in use \(inUsePath ?? "<in-memory>"); "
                    + "the requested location failed and the app fell back (reason: \(diagnostic.summary))")
            }
        }

        // 3. An explicit open at the requested path, which separates "the path
        //    is not writable" from "the shared instance never looked at it".
        if let requested, !wantsMemory {
            check("an explicit open at READFLOW_DB_PATH succeeds") {
                do {
                    _ = try ReaderDatabase(path: requested)
                    log("self-test: info — db-init explicit open at \(requested): OK")
                    return true
                } catch {
                    log("self-test: FAIL — db-init explicit open at \(requested) threw: \(error)")
                    return false
                }
            }
        }

        // 4. The file the verifier will look for must actually be on disk.
        if let inUsePath {
            check("the store file exists on disk (this is what `sqlite3` will open)") {
                let exists = FileManager.default.fileExists(atPath: inUsePath)
                let attributes = try? FileManager.default.attributesOfItem(atPath: inUsePath)
                let size = (attributes?[.size] as? Int) ?? 0
                log("self-test: info — db-init file exists=\(exists ? "yes" : "no") size=\(size)")
                return exists
            }
        } else {
            check("no on-disk store was requested as :memory:, so no file is expected") {
                wantsMemory
            }
        }

        // 5. The three FTS5 virtual tables, read from the store in use.
        let tables = (try? shared.read { db in
            try String.fetchAll(db, sql: "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        }) ?? []
        let present = Set(tables)
        log("self-test: info — db-init fts "
            + Self.expectedFTS.map { "\($0)=\(present.contains($0) ? "yes" : "no")" }.joined(separator: " "))
        log("self-test: info — db-init tables=\(tables.joined(separator: ","))")
        if let inUsePath {
            log("self-test: info — db-init verify-with: sqlite3 \"\(inUsePath)\" \".tables\"")
        }

        for table in Self.expectedFTS {
            check("\(table) exists in the store in use") {
                present.contains(table)
            }
        }

        return (assertions, failures)
    }

    private static func log(_ message: String) {
        FileHandle.standardError.write(Data("[ReadFlow] \(message)\n".utf8))
    }
}
