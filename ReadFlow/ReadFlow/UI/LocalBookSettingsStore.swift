//
//  LocalBookSettingsStore.swift
//  ReadFlow
//
//  Where the LocalBook backend address, username and password live, and how they
//  are judged before anything is sent to the network.
//
//  Two deployments have to work with one build:
//    · LAN / same machine — `http://127.0.0.1:3780`, no gate, pairs for a token.
//    · public — `https://note.ningyd.com`, gated by nginx with HTTP Basic on
//      every path, so the credential is what makes the connection work at all.
//  The rules below exist because the second case sends a password over the wire.
//

import Foundation
import Combine

@MainActor
final class LocalBookSettingsStore: ObservableObject {

    enum Key {
        static let baseURL = "localbook_base_url"      // not a secret
        static let user = "localbook_basic_user"       // not a secret
    }

    struct Settings: Equatable {
        var baseURL: String = ""
        var user: String = ""
        var password: String = ""

        var isBlank: Bool {
            baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
    }

    /// Why an address is refused, in the user's words. Refusing is deliberate:
    /// the alternative is sending a password in clear text to a public host and
    /// reporting success when the network happens to answer.
    enum EndpointIssue: Equatable, Error {
        case malformed
        case unsupportedScheme(String)
        case insecureRemote

        var message: String {
            switch self {
            case .malformed:
                return "地址无法解析，请形如 https://note.ningyd.com"
            case .unsupportedScheme(let scheme):
                return "不支持的协议「\(scheme)」，只接受 http 或 https"
            case .insecureRemote:
                return "非本机地址必须使用 https：http 会让用户名与密码在网络上明文传输"
            }
        }
    }

    /// The outcome of a connection probe. `reached` means an HTTP response came
    /// back at all — which is what "connected" honestly means; the status code
    /// then says whether the credential was accepted.
    enum Probe: Equatable {
        case idle
        case running
        case reached(status: Int, authenticated: Bool)
        case unreachable(String)

        var message: String? {
            switch self {
            case .idle: return nil
            case .running: return "正在测试…"
            case .reached(let status, let authenticated):
                if authenticated { return "已连接（HTTP \(status)）" }
                if status == 401 || status == 403 {
                    return "服务器要求鉴权，但当前凭据未被接受（HTTP \(status)）"
                }
                return "可达（HTTP \(status)）"
            case .unreachable(let reason):
                return "不可达：\(reason)"
            }
        }

        var isSuccess: Bool {
            if case .reached(_, true) = self { return true }
            return false
        }
    }

    /// Settable so the pane can bind fields directly. Writes still go through
    /// `save()`, which validates first and is the only thing that touches the
    /// keychain — editing a field never persists a half-typed password.
    @Published var settings = Settings()
    @Published private(set) var probe: Probe = .idle
    @Published private(set) var pairingCode: String?
    @Published private(set) var pairingStatus: String?
    @Published private(set) var isPairing = false
    private var pairingDeviceName: String?

    private let defaults: UserDefaults
    private let keychain: KeychainService
    private let session: URLSession

    init(defaults: UserDefaults = .standard,
         keychain: KeychainService = .shared,
         session: URLSession = .shared) {
        self.defaults = defaults
        self.keychain = keychain
        self.session = session
    }

    // MARK: - Loading

    /// Reads the stored configuration. A keychain failure is reported, never
    /// folded into "no password": those two states lead the user to different
    /// actions, and only one of them is fixable by typing the password again.
    func load() -> String? {
        var loaded = Settings()
        loaded.baseURL = defaults.string(forKey: Key.baseURL) ?? ""
        loaded.user = defaults.string(forKey: Key.user) ?? ""
        do {
            loaded.password = try keychain.getLocalBookPassword() ?? ""
        } catch {
            settings = loaded
            return "读取钥匙串失败：\(error.localizedDescription)"
        }
        settings = loaded
        return nil
    }

    // MARK: - Validation

    /// Normalises and validates an address, or explains why it is refused.
    nonisolated static func resolve(_ raw: String) -> Result<URL, EndpointIssue> {
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return .failure(.malformed) }
        guard let url = URL(string: trimmed), let scheme = url.scheme?.lowercased(),
              let host = url.host, !host.isEmpty else {
            return .failure(.malformed)
        }
        guard scheme == "http" || scheme == "https" else {
            return .failure(.unsupportedScheme(scheme))
        }
        if scheme == "http" && !isLoopback(host) {
            return .failure(.insecureRemote)
        }
        return .success(url)
    }

    nonisolated static func isLoopback(_ host: String) -> Bool {
        let h = host.lowercased()
        return h == "127.0.0.1" || h == "localhost" || h == "::1" || h == "[::1]"
    }

    /// Current issue for the typed-in address, or nil when it is usable.
    var endpointIssue: EndpointIssue? {
        guard !settings.isBlank else { return nil }
        if case .failure(let issue) = Self.resolve(settings.baseURL) { return issue }
        return nil
    }

    var isUsable: Bool {
        !settings.isBlank && endpointIssue == nil
    }

    // MARK: - Saving

    /// Persists URL + username in UserDefaults and the password in the keychain,
    /// then repoints the shared client. Throws when the keychain refuses the
    /// write, so the panel can say "not saved" instead of implying success.
    @discardableResult
    func save() throws -> URL {
        switch Self.resolve(settings.baseURL) {
        case .failure(let issue):
            throw LocalBookSettingsError.invalidEndpoint(issue)
        case .success(let url):
            var trimmed = settings
            trimmed.baseURL = url.absoluteString
            trimmed.user = trimmed.user.trimmingCharacters(in: .whitespacesAndNewlines)

            if trimmed.user.isEmpty {
                // No username ⇒ not a Basic deployment. Store the address only and
                // drop any stale password, so the app never presents a credential
                // the user believes they removed.
                try? keychain.deleteLocalBookPassword()
                defaults.set(trimmed.baseURL, forKey: Key.baseURL)
                defaults.removeObject(forKey: Key.user)
                try LocalBookClient.shared.configure(baseURL: url)
            } else {
                try LocalBookClient.shared.configure(baseURL: url,
                                                     basicUser: trimmed.user,
                                                     basicPassword: trimmed.password)
                defaults.set(trimmed.user, forKey: Key.user)
            }
            settings = trimmed
            return url
        }
    }

    /// Request a short-lived code. The owner must approve it in LocalBook's
    /// System settings before registration can issue this device a token.
    func beginPairing(deviceName: String = "ReadFlow Mac") async {
        isPairing = true
        pairingCode = nil
        pairingDeviceName = nil
        pairingStatus = "正在生成配对码…"
        defer { isPairing = false }

        do {
            _ = try save()
            let pairing = try await LocalBookClient.shared.pair(
                deviceName: deviceName,
                deviceType: "macos"
            )
            pairingCode = pairing.pairingToken
            pairingDeviceName = deviceName
            pairingStatus = "请在 LocalBook 网页的设置 → 系统中输入配对码并批准，然后点击“完成配对”。"
        } catch {
            pairingStatus = "生成配对码失败：\(error.localizedDescription)"
        }
    }

    /// Exchange an owner-approved code for a token, then persist the device ID.
    func finishPairing() async {
        guard let code = pairingCode, let deviceName = pairingDeviceName else {
            pairingStatus = "请先生成配对码"
            return
        }
        isPairing = true
        pairingStatus = "正在完成配对…"
        defer { isPairing = false }
        do {
            let storedID = defaults.string(forKey: "localbook_device_id")
            let deviceID = storedID.flatMap(UUID.init(uuidString:)) ?? UUID()
            let registered = try await LocalBookClient.shared.register(
                deviceID: deviceID,
                deviceName: deviceName,
                deviceType: "macos",
                pairingToken: code
            )
            defaults.set(registered.deviceID.uuidString, forKey: "localbook_device_id")
            pairingCode = nil
            pairingDeviceName = nil
            pairingStatus = "已配对（设备 \(registered.deviceID.uuidString.prefix(8))）"
            SyncEngine.shared.refreshConnection()
        } catch NetworkError.pairingApprovalRequired {
            pairingStatus = "尚未获得批准。请先在 LocalBook 网页的设置 → 系统中批准，再重试。"
        } catch {
            pairingStatus = "配对失败：\(error.localizedDescription)"
        }
    }

    /// Removes every stored trace: URL, username, keychain entry, and the shared
    /// client's configuration.
    func clear() {
        defaults.removeObject(forKey: Key.baseURL)
        defaults.removeObject(forKey: Key.user)
        defaults.removeObject(forKey: "localbook_device_id")
        try? keychain.deleteLocalBookPassword()
        settings = Settings()
        probe = .idle
        pairingCode = nil
        pairingStatus = nil
        pairingDeviceName = nil
        LocalBookClient.shared.clearConfiguration()
    }

    // MARK: - Connection test

    /// Probes the address and reports what actually came back.
    ///
    /// Requests `/` on purpose: the public deployment challenges *every* path, so
    /// any path proves the same thing, and `/` is the one URL that exists on both
    /// deployments (the local service answers 404 there, which is still a
    /// response — "reachable" must not mean "returned 200").
    func testConnection() async {
        probe = .running
        guard case .success(let url) = Self.resolve(settings.baseURL) else {
            probe = .unreachable(endpointIssue?.message ?? "地址无效")
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 12
        request.cachePolicy = URLRequest.CachePolicy.reloadIgnoringLocalCacheData

        let user = settings.user.trimmingCharacters(in: .whitespacesAndNewlines)
        if !user.isEmpty {
            request.setValue(LocalBookClient.basicAuthorization(user: user,
                                                                password: settings.password),
                             forHTTPHeaderField: "Authorization")
        }

        do {
            let (_, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                probe = .unreachable("响应不是 HTTP")
                return
            }
            let authenticated = (200..<400).contains(http.statusCode)
            probe = .reached(status: http.statusCode, authenticated: authenticated)
        } catch {
            probe = .unreachable(error.localizedDescription)
        }
    }
}

enum LocalBookSettingsError: LocalizedError {
    case invalidEndpoint(LocalBookSettingsStore.EndpointIssue)

    var errorDescription: String? {
        switch self {
        case .invalidEndpoint(let issue): return issue.message
        }
    }
}
