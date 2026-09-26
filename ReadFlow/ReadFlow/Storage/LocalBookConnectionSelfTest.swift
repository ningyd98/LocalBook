//
//  LocalBookConnectionSelfTest.swift
//  ReadFlow
//
//  `--self-test localbook-connection` — judges the rules that stand between a
//  typed-in address and a password going out over the network.
//
//  Two halves, deliberately separated:
//
//    · **Pure rules** (always run): address validation and Basic header encoding.
//      Deterministic, need no credential, asserted on every run.
//    · **Live probe** (only with READFLOW_LOCALBOOK_LIVE=1): uses whatever the app
//      itself has stored and performs a real request. This is the half a *user*
//      runs, because only they have the password — the tool never asks for it and
//      never prints it.
//
//  Why the negative direction matters: the public deployment gates every path, so
//  "no credential ⇒ 401" is the evidence that the gate is real. Without it, a 200
//  served by some other host would look like success.
//
//  Three runtime lessons are baked in, each learned by measurement on this box:
//    · never block the main thread during launch (SIGABRT, exit 134);
//    · never share a plain `var` with a URLSession completion ("Fatal access
//      conflict detected");
//    · never take the counters `inout` while a closure also captures them — the
//      same exclusivity trap, which is why they live in a small class.
//
//  Exit: 0 all assertions hold · 1 an assertion failed · 2 usage.
//

import Foundation

/// Shared counters. A class rather than `inout Int`s precisely because the
/// `check` closure and the probe both touch them: passing them `inout` while the
/// closure captures them is overlapping access, which traps at runtime.
private final class Counters {
    var assertions = 0
    var failures = 0
}

enum LocalBookConnectionSelfTest {

    static let scenario = "localbook-connection"

    static func run() -> Int32 {
        let counters = Counters()

        func check(_ description: String, _ body: () -> Bool) {
            counters.assertions += 1
            if body() {
                log("PASS  [localbook] \(description)")
            } else {
                counters.failures += 1
                log("FAIL  [localbook] \(description)")
            }
        }

        // ---------------------------------------------------------------- rules
        log("info — pure rules: address validation")

        check("a loopback http address is accepted (the LAN case)") {
            if case .success = LocalBookSettingsStore.resolve("http://127.0.0.1:3780") { return true }
            return false
        }
        check("localhost http is accepted too") {
            if case .success = LocalBookSettingsStore.resolve("http://localhost:3780") { return true }
            return false
        }
        check("the public https address is accepted") {
            if case .success(let url) = LocalBookSettingsStore.resolve("https://note.ningyd.com"),
               url.host == "note.ningyd.com" { return true }
            return false
        }
        check("surrounding whitespace is tolerated") {
            if case .success = LocalBookSettingsStore.resolve("  https://note.ningyd.com  ") { return true }
            return false
        }
        check("http to a REMOTE host is refused (the password would travel in clear)") {
            if case .failure(.insecureRemote) = LocalBookSettingsStore.resolve("http://note.ningyd.com") { return true }
            return false
        }
        check("a non-http scheme is refused, naming the scheme") {
            if case .failure(.unsupportedScheme(let scheme)) = LocalBookSettingsStore.resolve("ftp://example.com"),
               scheme == "ftp" { return true }
            return false
        }
        check("an unparseable address is refused") {
            if case .failure(.malformed) = LocalBookSettingsStore.resolve("not a url") { return true }
            return false
        }
        check("an empty address is refused") {
            if case .failure(.malformed) = LocalBookSettingsStore.resolve("   ") { return true }
            return false
        }

        // --------------------------------------------------------------- header
        log("info — pure rules: Basic header encoding")

        check("the header is exactly `Basic base64(user:password)`") {
            // base64("u:p") == "dTpw" — a fixed, checkable value.
            LocalBookClient.basicAuthorization(user: "u", password: "p") == "Basic dTpw"
        }
        check("the encoded payload decodes back to `user:password`") {
            let header = LocalBookClient.basicAuthorization(user: "alice", password: "s3cr3t:with:colons")
            guard header.hasPrefix("Basic "),
                  let data = Data(base64Encoded: String(header.dropFirst("Basic ".count))),
                  let decoded = String(data: data, encoding: .utf8) else { return false }
            // A colon inside the password must survive: a server splits on the
            // FIRST colon only (RFC 7617).
            return decoded == "alice:s3cr3t:with:colons"
        }
        check("non-ASCII credentials survive the round trip") {
            let header = LocalBookClient.basicAuthorization(user: "宁也东", password: "密码")
            guard let data = Data(base64Encoded: String(header.dropFirst("Basic ".count))),
                  let decoded = String(data: data, encoding: .utf8) else { return false }
            return decoded == "宁也东:密码"
        }

        // ---------------------------------------------------------------- live
        guard ProcessInfo.processInfo.environment["READFLOW_LOCALBOOK_LIVE"] == "1" else {
            log("note — live probe skipped (set READFLOW_LOCALBOOK_LIVE=1 to run it against the "
                + "configuration the app has stored; the credential is read from the keychain and "
                + "never printed)")
            return finish(counters)
        }

        log("info — live probe")
        runLive(check: check, counters: counters)
        return finish(counters)
    }

    // MARK: - Live probe

    /// Reads exactly what the app stores — UserDefaults for the address and
    /// username, keychain for the password — so what is verified is the path a
    /// user takes. It lives here rather than in the settings store because this
    /// file is a storage-level self-test and must run without the UI layer.
    private static func runLive(check: (String, () -> Bool) -> Void, counters: Counters) {
        let address = UserDefaults.standard.string(forKey: "localbook_base_url") ?? ""
        let user = UserDefaults.standard.string(forKey: "localbook_basic_user") ?? ""

        var password = ""
        do {
            password = try KeychainService.shared.getLocalBookPassword() ?? ""
        } catch {
            // A keychain failure must not read as "no password": that would turn a
            // broken keychain into a 401 the user cannot explain.
            counters.assertions += 1
            counters.failures += 1
            log("FAIL  [localbook] could not read the stored password: \(error.localizedDescription)")
            return
        }

        let hasCredential = !user.isEmpty && !password.isEmpty
        log("info — stored address=[\(address.isEmpty ? "<none>" : address)] "
            + "user=[\(user.isEmpty ? "<none>" : user)] "
            + "credential=\(hasCredential ? "present (value not printed)" : "absent")")

        guard !address.isEmpty else {
            log("note — nothing stored, so there is nothing to probe. Configure it in "
                + "设置 → LocalBook first.")
            return
        }

        let result = Probe(urlString: address, user: user, password: password).run()
        log("info — probe result: \(result.describe)")

        check("the probe produced an HTTP response (the server is reachable)") {
            result.status != nil
        }
        if hasCredential {
            check("with a stored credential the server accepts it (2xx/3xx)") {
                guard let status = result.status else { return false }
                return (200..<400).contains(status)
            }
        } else {
            check("without a credential the gated deployment refuses the request (401/403)") {
                guard let status = result.status else { return false }
                return status == 401 || status == 403
            }
        }
    }

    private struct Probe {
        let urlString: String
        let user: String
        let password: String

        struct Result {
            var status: Int?
            var error: String?
            var describe: String {
                if let status { return "HTTP \(status)" }
                return "no response (\(error ?? "unknown"))"
            }
        }

        /// Hand-off between the URLSession completion (background queue) and the
        /// main thread pumping the run loop. Locked, because sharing a plain `var`
        /// makes the runtime abort with "Fatal access conflict detected".
        final class ResultBox: @unchecked Sendable {
            private let lock = NSLock()
            private var stored: Result?

            func publish(_ result: Result) {
                lock.lock()
                stored = result
                lock.unlock()
            }

            var value: Result? {
                lock.lock()
                defer { lock.unlock() }
                return stored
            }
        }

        func run() -> Result {
            guard case .success(let url) = LocalBookSettingsStore.resolve(urlString) else {
                return Result(status: nil, error: "the stored address is not valid")
            }
            var request = URLRequest(url: url)
            request.httpMethod = "GET"
            request.timeoutInterval = 15
            request.cachePolicy = URLRequest.CachePolicy.reloadIgnoringLocalCacheData
            if !user.isEmpty {
                request.setValue(LocalBookClient.basicAuthorization(user: user, password: password),
                                 forHTTPHeaderField: "Authorization")
            }

            let box = ResultBox()
            URLSession.shared.dataTask(with: request) { _, response, error in
                if let http = response as? HTTPURLResponse {
                    box.publish(Result(status: http.statusCode, error: nil))
                } else {
                    box.publish(Result(status: nil,
                                       error: error?.localizedDescription ?? "not an HTTP response"))
                }
            }.resume()

            // Pump — never block the main thread: blocking it during launch is
            // what aborts the process.
            let deadline = Date().addingTimeInterval(20)
            while box.value == nil && Date() < deadline {
                RunLoop.current.run(until: Date().addingTimeInterval(0.05))
            }
            return box.value ?? Result(status: nil, error: "timed out after 20s")
        }
    }

    // MARK: - Reporting

    private static func finish(_ counters: Counters) -> Int32 {
        if counters.failures == 0 {
            log("\(scenario): PASS (\(counters.assertions) assertions)")
            return 0
        }
        log("\(scenario): FAIL (\(counters.failures) of \(counters.assertions) assertions failed)")
        return 1
    }

    private static func log(_ message: String) {
        FileHandle.standardError.write(Data("[ReadFlow] self-test: \(message)\n".utf8))
    }
}
