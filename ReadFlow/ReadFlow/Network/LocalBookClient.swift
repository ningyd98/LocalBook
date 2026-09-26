//
//  LocalBookClient.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import Alamofire

/// Talks to the LocalBook server over HTTP.
///
/// `@unchecked Sendable` rather than a plain class because the client is a
/// process-wide singleton and Alamofire invokes its completion handlers on its
/// own queues, which captures `self` in `@Sendable` closures. What makes that
/// sound is the lock below: the only mutable state (`_baseURL`, `_accessToken`)
/// is always read and written under `stateLock`. `session` is immutable and
/// Alamofire documents `Session` as thread-safe.
final class LocalBookClient: @unchecked Sendable {
    static let shared = LocalBookClient()

    /// Guards `_baseURL` / `_accessToken`.
    private let stateLock = NSLock()
    private var _baseURL: URL?
    private var _accessToken: String?
    private var _basicAuth: (user: String, password: String)?
    private let session: Session

    /// Most recent keychain failure, if any (contract K5).
    ///
    /// Recorded because the keychain is touched from places that cannot throw —
    /// notably `init` via `loadConfiguration()`. Recording plus logging it keeps
    /// the failure observable instead of silently degrading to "no token".
    ///
    /// Typed as `any Error` (rather than `KeychainError`) so nothing is ever
    /// dropped by a failed downcast; in practice it is always a `KeychainError`,
    /// which is `LocalizedError`, so `localizedDescription` is presentable.
    private(set) var lastKeychainError: (any Error)?

    /// Unbuffered diagnostic line on stderr, matching the app's launch marker so
    /// a supervisor can see it even when stdout is block-buffered.
    static func logToStderr(_ message: String) {
        FileHandle.standardError.write(Data("[ReadFlow] \(message)\n".utf8))
    }

    private var baseURL: URL? {
        get {
            stateLock.lock()
            defer { stateLock.unlock() }
            return _baseURL
        }
        set {
            stateLock.lock()
            defer { stateLock.unlock() }
            _baseURL = newValue
        }
    }

    /// HTTP Basic credential, when the deployment is gated in front of the API.
    ///
    /// On the LAN the app sends its paired token as Authorization: Bearer. A
    /// public reverse proxy may require HTTP Basic in that header instead; in
    /// that case Reader requests carry the paired token in their own header.
    private var basicAuth: (user: String, password: String)? {
        get {
            stateLock.lock()
            defer { stateLock.unlock() }
            return _basicAuth
        }
        set {
            stateLock.lock()
            defer { stateLock.unlock() }
            _basicAuth = newValue
        }
    }

    /// Build the Authorization value for a deployment gated with HTTP Basic.
    ///
    /// Exposed as a pure function so the encoding can be asserted without a
    /// keychain, a network or a real credential — the alternative is a check that
    /// only runs when someone happens to have a password to hand.
    static func basicAuthorization(user: String, password: String) -> String {
        "Basic " + Data("\(user):\(password)".utf8).base64EncodedString()
    }

    func authorizationValue() -> String? {
        if let basic = basicAuth {
            return Self.basicAuthorization(user: basic.user, password: basic.password)
        }
        if let token = accessToken {
            return "Bearer \(token)"
        }
        return nil
    }

    /// Reader endpoints always require the paired device token. A public
    /// reverse proxy may also require HTTP Basic in Authorization, so carry
    /// the device token in its dedicated header in that configuration.
    private func readerHeaders(json: Bool = false) throws -> HTTPHeaders {
        stateLock.lock()
        let token = _accessToken
        let basic = _basicAuth
        stateLock.unlock()
        guard let token, !token.isEmpty else { throw NetworkError.unauthorized }
        var headers = HTTPHeaders()
        if let basic {
            headers.add(name: "Authorization", value: Self.basicAuthorization(user: basic.user, password: basic.password))
            headers.add(name: "X-LocalBook-Reader-Token", value: token)
        } else {
            headers.add(name: "Authorization", value: "Bearer \(token)")
        }
        if json { headers.add(name: "Content-Type", value: "application/json") }
        return headers
    }

    private var accessToken: String? {
        get {
            stateLock.lock()
            defer { stateLock.unlock() }
            return _accessToken
        }
        set {
            stateLock.lock()
            defer { stateLock.unlock() }
            _accessToken = newValue
        }
    }

    private init() {
        // Configure Alamofire session
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 60

        self.session = Session(configuration: configuration)
        loadConfiguration()
    }

    // MARK: - Configuration

    /// Best-effort load during `init`, which cannot throw.
    ///
    /// A keychain failure here must not be swallowed and must not be silently
    /// turned into "no token": it is logged (unbuffered stderr) and kept in
    /// `lastKeychainError` so callers and the UI can surface it. `try?` would
    /// recreate exactly the silent failure that contract K5 forbids.
    private func loadConfiguration() {
        if let urlString = UserDefaults.standard.string(forKey: "localbook_base_url"),
           let url = URL(string: urlString) {
            self.baseURL = url
        }

        do {
            if let token = try KeychainService.shared.getAccessToken() {
                self.accessToken = token
            }
        } catch {
            lastKeychainError = error
            LocalBookClient.logToStderr("keychain read failed while loading configuration: \(error.localizedDescription)")
        }

        // Basic credential: the username is not a secret (UserDefaults), the
        // password is (keychain). A read failure is surfaced, never folded into
        // "not configured" — that would silently turn a broken keychain into a
        // 401 the user cannot explain.
        if let user = UserDefaults.standard.string(forKey: "localbook_basic_user"),
           !user.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            do {
                if let password = try KeychainService.shared.getLocalBookPassword() {
                    self.basicAuth = (user, password)
                }
            } catch {
                lastKeychainError = error
                LocalBookClient.logToStderr("keychain read failed while loading the LocalBook password: \(error.localizedDescription)")
            }
        }
    }

    /// Persists the access token, so it **throws** when the keychain refuses the
    /// write (contract K5). Previously the `OSStatus` was discarded and a failed
    /// write was indistinguishable from a successful one.
    func configure(baseURL: URL, accessToken: String? = nil) throws {
        // A Reader bearer token belongs to one endpoint. Do not present a
        // previous server's token when the user changes the address.
        if self.baseURL != baseURL {
            try KeychainService.shared.deleteAccessToken()
            self.accessToken = nil
        }
        // Switching to a non-Basic endpoint must not carry the old host's
        // credentials into requests made to the new address.
        self.basicAuth = nil

        if let token = accessToken {
            try KeychainService.shared.saveAccessToken(token)
            self.accessToken = token
        }

        self.baseURL = baseURL
        UserDefaults.standard.set(baseURL.absoluteString, forKey: "localbook_base_url")
    }

    /// Configures a deployment that is gated with HTTP Basic.
    ///
    /// Persists the URL and username in UserDefaults and the password in the
    /// keychain (throwing if that write is refused, contract K5). A paired
    /// device token is retained for the same endpoint and sent separately.
    func configure(baseURL: URL, basicUser: String, basicPassword: String) throws {
        try KeychainService.shared.saveLocalBookPassword(basicPassword)
        if self.baseURL != baseURL {
            try KeychainService.shared.deleteAccessToken()
            self.accessToken = nil
        }
        self.basicAuth = (basicUser, basicPassword)
        self.baseURL = baseURL
        UserDefaults.standard.set(baseURL.absoluteString, forKey: "localbook_base_url")
        UserDefaults.standard.set(basicUser, forKey: "localbook_basic_user")
    }

    /// Forgets the address and every credential, including the paired bearer
    /// token. Used by "clear" in the settings panel, where the user's intent is
    /// that nothing about the backend remains.
    func clearConfiguration() {
        self.baseURL = nil
        self.accessToken = nil
        self.basicAuth = nil
        UserDefaults.standard.removeObject(forKey: "localbook_base_url")
        UserDefaults.standard.removeObject(forKey: "localbook_basic_user")
        // Best-effort: a keychain refusal here must not leave the UI claiming a
        // state it did not reach, but there is nothing left to fall back to.
        do {
            try KeychainService.shared.deleteAccessToken()
            try KeychainService.shared.deleteLocalBookPassword()
        } catch {
            lastKeychainError = error
            LocalBookClient.logToStderr("keychain delete failed while clearing configuration: \(error.localizedDescription)")
        }
    }

    func isConfigured() -> Bool {
        return baseURL != nil && accessToken?.isEmpty == false
    }

    /// Converts the Codable payload produced by the local database into the
    /// snake_case keys used by the FastAPI Reader schemas. The same helper walks
    /// nested objects and arrays, so metadata fields are not silently dropped.
    private static func snakeCaseJSON(_ value: Any) -> Any {
        if let object = value as? [String: Any] {
            return object.reduce(into: [String: Any]()) { result, entry in
                result[entry.key.replacingOccurrences(
                    of: "([a-z0-9])([A-Z])",
                    with: "$1_$2",
                    options: .regularExpression
                ).lowercased()] = snakeCaseJSON(entry.value)
            }
        }
        if let array = value as? [Any] {
            return array.map(snakeCaseJSON)
        }
        return value
    }

    // MARK: - Pairing

    /// Requests a short-lived pairing code. The server does not authenticate this
    /// endpoint: a human must confirm the returned code in LocalBook before the
    /// client can exchange it with `register` for a bearer token.
    func pair(deviceName: String, deviceType: String = "macos") async throws -> PairResponse {
        guard let baseURL = baseURL else {
            throw NetworkError.invalidURL
        }

        let url = baseURL.appendingPathComponent("/api/v1/reader/pair")
        let parameters: [String: Any] = [
            "device_name": deviceName,
            "device_type": deviceType
        ]

        var headers = HTTPHeaders()
        if let authorization = authorizationValue() {
            headers.add(name: "Authorization", value: authorization)
        }

        return try await withCheckedThrowingContinuation { continuation in
            session.request(url, method: .post, parameters: parameters, encoding: JSONEncoding.default, headers: headers)
                .validate()
                .responseDecodable(of: PairResponse.self) { response in
                    switch response.result {
                    case .success(let pairResponse):
                        continuation.resume(returning: pairResponse)
                    case .failure(let error):
                        continuation.resume(throwing: self.mapError(error, response: response.response, data: response.data))
                    }
                }
        }
    }

    /// Exchanges a confirmed pairing code for the durable access token.
    func register(
        deviceID: UUID,
        deviceName: String,
        deviceType: String = "macos",
        pairingToken: String,
        publicKey: String? = nil
    ) async throws -> RegisterResponse {
        guard let baseURL = baseURL else {
            throw NetworkError.invalidURL
        }

        let url = baseURL.appendingPathComponent("/api/v1/reader/register")
        var parameters: [String: Any] = [
            "device_id": deviceID.uuidString,
            "device_name": deviceName,
            "device_type": deviceType,
            "pairing_token": pairingToken
        ]
        if let publicKey {
            parameters["public_key"] = publicKey
        }

        var headers = HTTPHeaders()
        if let authorization = authorizationValue() {
            headers.add(name: "Authorization", value: authorization)
        }

        return try await withCheckedThrowingContinuation { continuation in
            session.request(url, method: .post, parameters: parameters, encoding: JSONEncoding.default, headers: headers)
                .validate()
                .responseDecodable(of: RegisterResponse.self) { response in
                    switch response.result {
                    case .success(let registerResponse):
                        do {
                            try KeychainService.shared.saveAccessToken(registerResponse.accessToken)
                            self.accessToken = registerResponse.accessToken
                        } catch {
                            self.lastKeychainError = error
                            LocalBookClient.logToStderr("registration succeeded but the token could not be stored: \(error.localizedDescription)")
                            continuation.resume(throwing: error)
                            return
                        }
                        continuation.resume(returning: registerResponse)
                    case .failure(let error):
                        continuation.resume(throwing: self.mapError(error, response: response.response, data: response.data))
                    }
                }
        }
    }

    // MARK: - Sync

    func pushSync(items: [SyncOutboxItem]) async throws -> PushSyncResponse {
        guard let baseURL = baseURL else {
            throw NetworkError.invalidURL
        }

        let url = baseURL.appendingPathComponent("/api/v1/reader/sync/push")
        let headers = try readerHeaders(json: true)

        let operations = try items.map { item -> [String: Any] in
            let payload: Any
            if item.payload.isEmpty {
                payload = [String: Any]()
            } else {
                payload = Self.snakeCaseJSON(
                    try JSONSerialization.jsonObject(with: item.payload, options: [.fragmentsAllowed])
                )
            }
            guard JSONSerialization.isValidJSONObject(payload) else {
                throw NetworkError.invalidPayload
            }
            return [
                "entity_type": item.entityType,
                "entity_id": item.entityID.uuidString,
                "operation": item.operation,
                "data": payload,
                "client_timestamp": Int(item.createdAt.timeIntervalSince1970)
            ]
        }

        let parameters: [String: Any] = [
            "operations": operations
        ]

        return try await withCheckedThrowingContinuation { continuation in
            session.request(url, method: .post, parameters: parameters, encoding: JSONEncoding.default, headers: headers)
                .validate()
                .responseDecodable(of: PushSyncResponse.self) { response in
                    switch response.result {
                    case .success(let pushResponse):
                        continuation.resume(returning: pushResponse)
                    case .failure(let error):
                        continuation.resume(throwing: self.mapError(error, response: response.response, data: response.data))
                    }
                }
        }
    }

    func pullSync(cursor: String?) async throws -> PullSyncResponse {
        guard let baseURL = baseURL else {
            throw NetworkError.invalidURL
        }

        var url = baseURL.appendingPathComponent("/api/v1/reader/sync/pull")

        if let cursor = cursor {
            var components = URLComponents(url: url, resolvingAgainstBaseURL: false)
            components?.queryItems = [URLQueryItem(name: "cursor", value: cursor)]
            if let newURL = components?.url {
                url = newURL
            }
        }

        let headers = try readerHeaders()

        return try await withCheckedThrowingContinuation { continuation in
            session.request(url, method: .get, headers: headers)
                .validate()
                .responseDecodable(of: PullSyncResponse.self) { response in
                    switch response.result {
                    case .success(let pullResponse):
                        continuation.resume(returning: pullResponse)
                    case .failure(let error):
                        continuation.resume(throwing: self.mapError(error, response: response.response, data: response.data))
                    }
                }
        }
    }

    // MARK: - Device Status

    func getStatus() async throws -> DeviceStatusResponse {
        guard let baseURL = baseURL else {
            throw NetworkError.invalidURL
        }

        let url = baseURL.appendingPathComponent("/api/v1/reader/status")
        let headers = try readerHeaders()

        return try await withCheckedThrowingContinuation { continuation in
            session.request(url, method: .get, headers: headers)
                .validate()
                .responseDecodable(of: DeviceStatusResponse.self) { response in
                    switch response.result {
                    case .success(let statusResponse):
                        continuation.resume(returning: statusResponse)
                    case .failure(let error):
                        continuation.resume(throwing: self.mapError(error, response: response.response, data: response.data))
                    }
                }
        }
    }

    // MARK: - AI

    func ask(
        conversationID: UUID?,
        sourceID: UUID?,
        highlightID: UUID?,
        query: String,
        context: String,
        scope: String
    ) async throws -> AIAnswer {
        guard let baseURL = baseURL else {
            throw NetworkError.invalidURL
        }

        let url = baseURL.appendingPathComponent("/api/v1/reader/ask")
        let headers = try readerHeaders(json: true)

        var parameters: [String: Any] = [
            "query": query,
            "scope": scope
        ]

        if let sourceID {
            parameters["source_id"] = sourceID.uuidString
        }
        if !context.isEmpty {
            parameters["context"] = context
        }
        if let conversationID {
            parameters["conversation_id"] = conversationID.uuidString
        }
        if let highlightID {
            parameters["highlight_id"] = highlightID.uuidString
        }

        return try await withCheckedThrowingContinuation { continuation in
            session.request(url, method: .post, parameters: parameters, encoding: JSONEncoding.default, headers: headers)
                .validate()
                .responseDecodable(of: AIAnswer.self) { response in
                    switch response.result {
                    case .success(let answer):
                        continuation.resume(returning: answer)
                    case .failure(let error):
                        continuation.resume(throwing: self.mapError(error, response: response.response, data: response.data))
                    }
                }
        }
    }

    func search(query: String, limit: Int = 50) async throws -> SearchResponse {
        guard let baseURL = baseURL else {
            throw NetworkError.invalidURL
        }

        let url = baseURL.appendingPathComponent("/api/v1/reader/search")
        let headers = try readerHeaders(json: true)

        let parameters: [String: Any] = [
            "query": query,
            "limit": limit
        ]

        return try await withCheckedThrowingContinuation { continuation in
            session.request(url, method: .post, parameters: parameters, encoding: JSONEncoding.default, headers: headers)
                .validate()
                .responseDecodable(of: SearchResponse.self) { response in
                    switch response.result {
                    case .success(let searchResponse):
                        continuation.resume(returning: searchResponse)
                    case .failure(let error):
                        continuation.resume(throwing: self.mapError(error, response: response.response, data: response.data))
                    }
                }
        }
    }

    // MARK: - Error Mapping

    private func mapError(_ error: AFError, response: HTTPURLResponse?, data: Data? = nil) -> NetworkError {
        if let statusCode = response?.statusCode {
            var apiCode: String?
            var apiMessage: String?
            if let data,
               let body = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
               let errorBody = body["error"] as? [String: Any] {
                apiCode = errorBody["code"] as? String
                apiMessage = errorBody["message"] as? String
            }
            if statusCode == 403 && apiCode == "pairing_approval_required" {
                return .pairingApprovalRequired
            }
            if statusCode == 503 && apiCode == "ai_unavailable" {
                return .networkUnavailable
            }
            switch statusCode {
            case 401:
                return .unauthorized
            case 404:
                return .notFound
            case 500...599:
                return .serverError(apiMessage ?? "服务器错误 (\(statusCode))")
            default:
                break
            }
        }

        if error.isSessionTaskError || error.isSessionInvalidatedError {
            return .networkUnavailable
        }

        return .serverError(error.localizedDescription)
    }
}

// MARK: - Response Models

struct PairResponse: Codable {
    let pairingToken: String
    let expiresAt: Int

    enum CodingKeys: String, CodingKey {
        case pairingToken = "pairing_token"
        case expiresAt = "expires_at"
    }
}

struct RegisterResponse: Codable {
    let accessToken: String
    let deviceID: UUID

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case deviceID = "device_id"
    }
}

struct PushSyncResponse: Codable, Sendable {
    let accepted: Int
    let rejected: Int
    let acceptedIDs: [String]
    let rejectedIDs: [String]
    let conflicts: [SyncConflict]

    enum CodingKeys: String, CodingKey {
        case accepted
        case rejected
        case acceptedIDs = "accepted_ids"
        case rejectedIDs = "rejected_ids"
        case conflicts
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        accepted = try container.decode(Int.self, forKey: .accepted)
        rejected = try container.decode(Int.self, forKey: .rejected)
        acceptedIDs = try container.decodeIfPresent([String].self, forKey: .acceptedIDs) ?? []
        rejectedIDs = try container.decodeIfPresent([String].self, forKey: .rejectedIDs) ?? []
        conflicts = try container.decodeIfPresent([SyncConflict].self, forKey: .conflicts) ?? []
    }
}

struct PullSyncResponse: Codable, Sendable {
    let entities: [SyncOperation]
    let nextCursor: String?
    let hasMore: Bool

    enum CodingKeys: String, CodingKey {
        case entities
        case nextCursor = "next_cursor"
        case hasMore = "has_more"
    }
}

/// JSON object values used by the Reader sync wire contract. Keeping the value
/// typed (rather than base64-encoding arbitrary bytes) means the Swift client
/// sends and receives the same `data` object as the FastAPI schemas.
enum JSONValue: Codable, Sendable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Unsupported JSON value")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null: try container.encodeNil()
        case .bool(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        }
    }

    func encodedData() throws -> Data {
        try JSONEncoder().encode(self)
    }
}

struct SyncOperation: Codable, Sendable {
    let entityType: String
    let entityID: UUID
    let operation: String
    let data: [String: JSONValue]
    let timestamp: Int

    enum CodingKeys: String, CodingKey {
        case entityType = "entity_type"
        case entityID = "entity_id"
        case operation
        case data
        case timestamp
    }

    func encodedData() throws -> Data {
        try JSONEncoder().encode(data)
    }
}

struct SyncConflict: Codable, Sendable {
    let entityType: String?
    let entityID: String
    let clientVersion: Int?
    let serverVersion: Int?
    let reason: String

    enum CodingKeys: String, CodingKey {
        case entityType = "entity_type"
        case entityID = "entity_id"
        case clientVersion = "client_version"
        case serverVersion = "server_version"
        case reason
    }
}

struct DeviceStatusResponse: Codable {
    let deviceID: UUID
    let deviceName: String
    let deviceType: String
    let createdAt: Int
    let lastSeen: Int
    let isActive: Bool

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case deviceName = "device_name"
        case deviceType = "device_type"
        case createdAt = "created_at"
        case lastSeen = "last_seen"
        case isActive = "is_active"
    }
}

struct AIAnswer: Codable {
    let answer: String
    let citations: [AICitation]
    let conversationID: UUID
    let messageID: UUID
    let model: String?

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        answer = try container.decode(String.self, forKey: .answer)
        citations = try container.decodeIfPresent([AICitation].self, forKey: .citations) ?? []
        conversationID = try container.decode(UUID.self, forKey: .conversationID)
        messageID = try container.decode(UUID.self, forKey: .messageID)
        model = try container.decodeIfPresent(String.self, forKey: .model)
    }

    enum CodingKeys: String, CodingKey {
        case answer
        case citations
        case conversationID = "conversation_id"
        case messageID = "message_id"
        case model
    }
}

struct AICitation: Codable {
    let sourceID: String
    /// Optional because the server declares it nullable.
    let title: String?
    let snippet: String?
    let url: String?

    enum CodingKeys: String, CodingKey {
        case sourceID = "source_id"
        case title
        case snippet
        case url
    }
}

struct SearchResponse: Codable {
    let results: [SearchResult]
    let total: Int
}

struct SearchResult: Codable {
    let entityType: String
    let entityID: String
    let title: String?
    let snippet: String
    let score: Double
    let url: String?
    let sourceID: String?

    enum CodingKeys: String, CodingKey {
        case entityType = "entity_type"
        case entityID = "entity_id"
        case title
        case snippet
        case score
        case url
        case sourceID = "source_id"
    }
}

// MARK: - Request Models

struct SyncOutboxItem: Codable {
    let entityType: String
    let entityID: UUID
    let operation: String
    let payload: Data
    let createdAt: Date
}

// MARK: - Errors

enum NetworkError: Error, LocalizedError {
    case notImplemented
    case invalidURL
    case invalidPayload
    case unauthorized
    case pairingApprovalRequired
    case notFound
    case serverError(String)
    case networkUnavailable

    var errorDescription: String? {
        switch self {
        case .notImplemented:
            return "功能尚未实现"
        case .invalidURL:
            return "无效的 URL"
        case .invalidPayload:
            return "同步数据不是有效的 JSON"
        case .unauthorized:
            return "未授权，请重新配对设备"
        case .pairingApprovalRequired:
            return "请先在 LocalBook 设置中批准配对码"
        case .notFound:
            return "资源未找到"
        case .serverError(let message):
            return "服务器错误: \(message)"
        case .networkUnavailable:
            return "网络不可用"
        }
    }
}
