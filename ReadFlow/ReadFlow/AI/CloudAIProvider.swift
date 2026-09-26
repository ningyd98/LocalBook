//
//  CloudAIProvider.swift
//  ReadFlow
//
//  The `.cloud` backend: a user-configured, OpenAI-compatible endpoint
//  (contract §3.4, §5.3).
//
//  Two operations, deliberately different in shape:
//    · `ask`             — `POST {base}/chat/completions`, the paid path.
//    · `testConnection`  — `GET  {base}/models`, the *free* probe.
//
//  The probe never issues a completion request. A `/models` failure is reported
//  as a failure rather than being escalated into a chat call that could bill
//  the user — contract §3.4 makes this unconditional, with no fallback clause.
//

import Foundation

/// Endpoint validation and normalization (contract E1–E4, frozen).
enum EndpointNormalizer {
    /// Turns user input into the base URL requests are built from.
    ///
    /// Rules:
    ///  · E1 — only `http`/`https` survive; anything else is rejected.
    ///  · E2 — a parseable host is required.
    ///  · E3 — trailing `/` trimmed; an empty or `/` path becomes `/v1`; a path
    ///         already ending in `/v1` is left alone.
    ///  · E4 — input that already ends in `/chat/completions` or `/models` is
    ///         tolerated and must **not** become `.../chat/completions/chat/completions`.
    static func normalize(_ raw: String) throws -> URL {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else {
            throw AIProviderError.invalidEndpoint("地址为空")
        }
        guard let url = URL(string: trimmed), let scheme = url.scheme?.lowercased() else {
            throw AIProviderError.invalidEndpoint("不是合法的 URL")
        }
        guard scheme == "http" || scheme == "https" else {
            throw AIProviderError.invalidEndpoint("只支持 http/https，收到「\(scheme)」")
        }
        guard let host = url.host, !host.isEmpty else {
            throw AIProviderError.invalidEndpoint("缺少主机名")
        }

        var path = url.path
        // E4: tolerate a fully-specified operation path instead of stacking on it.
        for suffix in ["/chat/completions", "/models"] where path.hasSuffix(suffix) {
            path = String(path.dropLast(suffix.count))
        }
        // E3: trim trailing slashes, then default to /v1.
        while path.hasSuffix("/") { path = String(path.dropLast()) }
        if path.isEmpty {
            path = "/v1"
        }

        var components = URLComponents()
        components.scheme = scheme
        components.host = host
        components.port = url.port
        components.path = path

        guard let normalized = components.url else {
            throw AIProviderError.invalidEndpoint("无法规范化该地址")
        }
        return normalized
    }

    /// Whether the given scheme is plain `http` (contract E5 requires the UI to
    /// warn about cleartext transport).
    static func isCleartext(_ raw: String) -> Bool {
        (try? normalize(raw))?.scheme?.lowercased() == "http"
    }
}

/// Talks to an OpenAI-compatible endpoint.
final class CloudAIProvider: AIProvider {
    let kind: AIProviderKind = .cloud

    private let baseURL: URL
    private let apiKey: String
    private let model: String
    private let session: URLSession

    /// Contract C6 — the request timeout must be explicitly bounded. The lower
    /// bound matters as much as the upper one: a too-short default is what once
    /// made a working cloud endpoint look permanently offline.
    static let defaultTimeout: TimeInterval = 30

    init(baseURL: URL, apiKey: String, model: String, timeout: TimeInterval = defaultTimeout) {
        precondition(
            (10...60).contains(timeout),
            "request timeout must stay within 10...60s (contract C6), got \(timeout)"
        )
        self.baseURL = baseURL
        self.apiKey = apiKey
        self.model = model

        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = timeout
        config.timeoutIntervalForResource = min(120, timeout * 4)
        self.session = URLSession(configuration: config)
    }

    /// Cheap probe: no completion request is issued (contract §3.4).
    func testConnection() async throws {
        var request = URLRequest(url: baseURL.appendingPathComponent("models"))
        request.httpMethod = "GET"
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")

        let (_, response) = try await perform(request)
        guard let http = response as? HTTPURLResponse else {
            throw AIProviderError.badResponse
        }
        switch http.statusCode {
        case 200..<300:
            return
        case 401, 403:
            throw AIProviderError.unauthorized
        case 500..<600:
            throw AIProviderError.serverError(http.statusCode, "连接测试失败")
        default:
            throw AIProviderError.badResponse
        }
    }

    func isAvailable() async -> Bool {
        do {
            try await testConnection()
            return true
        } catch {
            return false
        }
    }

    func ask(_ request: AIRequest) async throws -> AIResponse {
        var httpRequest = URLRequest(url: baseURL.appendingPathComponent("chat/completions"))
        httpRequest.httpMethod = "POST"
        httpRequest.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        httpRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")

        // A single user message, per contract §3.4. `context` is allowed to be
        // folded in, but the message count must stay at one. The API key is
        // never placed in the body.
        let composed = request.context.isEmpty
            ? request.query
            : "\(request.context)\n\n\(request.query)"

        var body: [String: Any] = [
            "model": model,
            "messages": [["role": "user", "content": composed]],
            "stream": false,
        ]
        if baseURL.host?.lowercased() == "api.deepseek.com" {
            // DeepSeek enables high-effort thinking by default. ReadFlow's
            // short interactive timeout expects a final answer directly.
            body["thinking"] = ["type": "disabled"]
        }
        httpRequest.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await perform(httpRequest)
        guard let http = response as? HTTPURLResponse else {
            throw AIProviderError.badResponse
        }

        switch http.statusCode {
        case 401, 403:
            throw AIProviderError.unauthorized
        case 500..<600:
            throw AIProviderError.serverError(http.statusCode, "服务端错误")
        case 429:
            // Rate limiting is a server-side error, not a degradation: the
            // contract forbids silently falling back here, and retrying would
            // risk duplicate billing (§3.5.1, C28).
            throw AIProviderError.serverError(429, "请求过于频繁，已被限流")
        case 200..<300:
            break
        default:
            throw AIProviderError.serverError(http.statusCode, "未预期的响应状态")
        }

        // `choices[0].message.content` must be a string, else the body is not
        // the shape we can trust.
        guard
            let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let choices = root["choices"] as? [[String: Any]],
            let first = choices.first,
            let message = first["message"] as? [String: Any],
            let content = message["content"] as? String,
            !content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
            !["length", "content_filter", "aborted", "insufficient_system_resource"]
                .contains(first["finish_reason"] as? String ?? "")
        else {
            throw AIProviderError.badResponse
        }

        return AIResponse(
            requestID: request.requestID,
            text: content,
            citations: [],
            provider: .cloud,
            isDegraded: false,
            model: root["model"] as? String ?? model
        )
    }

    // MARK: - Transport

    /// One request, one attempt — no retry loop.
    ///
    /// Contract C5 freezes the fallback order, and an automatic retry would
    /// both bypass "no silent fallback" and risk billing the user twice for the
    /// same question. Retrying is a user action, not a provider policy.
    private func perform(_ request: URLRequest) async throws -> (Data, URLResponse) {
        do {
            return try await session.data(for: request)
        } catch let error as URLError {
            switch error.code {
            case .timedOut:
                throw AIProviderError.timeout
            case .cannotConnectToHost, .cannotFindHost, .networkConnectionLost,
                 .notConnectedToInternet, .dnsLookupFailed:
                throw AIProviderError.unreachable
            default:
                throw AIProviderError.unreachable
            }
        } catch {
            throw AIProviderError.unreachable
        }
    }
}
