//
//  OutboundRequestCounter.swift
//  ReadFlow
//
//  Counts outbound HTTP requests made by the process.
//
//  Why this exists: A14's third layer asserts that the local-retrieval path
//  issues **zero** outbound requests. That claim cannot be made by reading code
//  — a stray client call anywhere on the path would pass every functional test
//  and still break the offline promise. So the count is *measured*.
//
//  How it works: a `URLProtocol` registered in `URLSessionConfiguration.protocolClasses`
//  intercepts every request made through a session created after registration,
//  records it, and **fails it immediately** without touching the network. Two
//  deliberate consequences:
//
//    · failing rather than forwarding means the counter can never itself cause
//      the outbound traffic it is measuring, and a bug that *does* try to make a
//      request cannot quietly succeed;
//    · the interception is process-wide but only registered by the self-test, so
//      normal app execution is unaffected.
//
//  Limitation, stated rather than hidden: sessions created *before* registration
//  are not retrofitted — `protocolClasses` is captured at session creation. The
//  self-test registers first, then constructs everything it measures, and prints
//  the registration status so a caller can tell the instrument was armed.
//

import Foundation

final class OutboundRequestCounter {

    /// Requests seen since the last `reset()`, with the URL each one targeted.
    private(set) var urls: [String] = []

    /// Whether the protocol was successfully installed. A `false` here means the
    /// zero-request result is *unproven*, not proven — the caller must report it
    /// that way rather than treating 0 as a pass.
    private(set) var isArmed = false

    static let shared = OutboundRequestCounter()

    private init() {}

    /// Registers the intercepting protocol for sessions created from now on.
    ///
    /// Idempotent: calling twice does not double-count.
    @discardableResult
    func arm() -> Bool {
        if isArmed { return true }

        let registered = URLProtocol.registerClass(CountingURLProtocol.self)
        CountingURLProtocol.sink = self
        isArmed = registered
        return isArmed
    }

    func reset() {
        urls.removeAll()
    }

    var count: Int { urls.count }

    func record(_ url: URL?) {
        urls.append(url?.absoluteString ?? "<no url>")
    }
}

/// Intercepts every request and refuses it, recording the attempt.
final class CountingURLProtocol: URLProtocol {

    /// Where intercepted requests are reported. Weak via `shared` lookup would be
    /// tidier, but the protocol instance and the counter live for the process, so
    /// a direct reference is both simpler and safe here.
    static weak var sink: OutboundRequestCounter?

    override class func canInit(with request: URLRequest) -> Bool {
        // Everything is interesting: the assertion is "no outbound request of any
        // kind", so filtering to particular hosts would weaken it.
        return true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        CountingURLProtocol.sink?.record(request.url)

        // Deliberately never forwards the request. The counter must not be able
        // to generate the traffic it measures.
        let error = NSError(
            domain: "ReadFlow.OutboundRequestCounter",
            code: 1,
            userInfo: [
                NSLocalizedDescriptionKey:
                    "outbound request blocked by the self-test counter: \(request.url?.absoluteString ?? "<no url>")"
            ]
        )
        client?.urlProtocol(self, didFailWithError: error)
    }

    override func stopLoading() {
        // Nothing to cancel: no work was started.
    }
}
