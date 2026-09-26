//
//  KeychainService.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import Security

/// Something went wrong talking to the keychain.
///
/// Exists so callers can tell "there is no such item" (a normal, non-error
/// answer) apart from "the keychain refused to do this" (a real failure that
/// must not be swallowed). Contract K5: no `OSStatus` may be discarded.
enum KeychainError: Error, LocalizedError, Equatable {
    /// An operation returned a status other than `errSecSuccess` (and other than
    /// `errSecItemNotFound`, where that is a legitimate answer).
    case unexpectedStatus(OSStatus, operation: String)
    /// The value could not be encoded/decoded as UTF-8. Practically unreachable
    /// for Swift `String`, but it is a real precondition, not a no-op.
    case encodingFailed(operation: String)

    var errorDescription: String? {
        switch self {
        case .unexpectedStatus(let status, let operation):
            let detail = SecCopyErrorMessageString(status, nil) as String?
            return "钥匙串操作失败：\(operation)（OSStatus \(status)\(detail.map { " · \($0)" } ?? "")）"
        case .encodingFailed(let operation):
            return "钥匙串操作失败：\(operation)（值无法编解码为 UTF-8）"
        }
    }

    /// The raw `OSStatus`, when there was one.
    var status: OSStatus? {
        if case .unexpectedStatus(let status, _) = self { return status }
        return nil
    }
}

/// The three `Security` calls `KeychainService` needs, as one protocol.
///
/// Why a protocol rather than only the per-call function seams below: a function
/// seam can express "this call returns status X", which is all a *failure* test
/// needs, but it cannot express **state** — so the success path (save, then read
/// it back, then delete it) had no in-process assertion at all: the only
/// implementation that could answer `errSecSuccess` *and* return the stored data
/// was the real login keychain, which a self-check must not write to.
///
/// `InMemoryKeychainBackend` closes that gap, and `case10` in
/// `StorageSelfCheck.swift` is what runs it.
protocol KeychainBackend {
    func add(_ query: CFDictionary) -> OSStatus
    func copyMatching(_ query: CFDictionary, _ result: UnsafeMutablePointer<AnyObject?>?) -> OSStatus
    func delete(_ query: CFDictionary) -> OSStatus
}

/// The production backend: a straight pass-through, no behaviour of its own.
struct SystemKeychainBackend: KeychainBackend {
    func add(_ query: CFDictionary) -> OSStatus {
        SecItemAdd(query, nil)
    }

    func copyMatching(_ query: CFDictionary, _ result: UnsafeMutablePointer<AnyObject?>?) -> OSStatus {
        SecItemCopyMatching(query, result)
    }

    func delete(_ query: CFDictionary) -> OSStatus {
        SecItemDelete(query)
    }
}

/// A `KeychainBackend` that stores items in memory.
///
/// Semantics are deliberately those of the real generic-password keychain for the
/// operations this service performs: an item is identified by
/// `(service, account)`, adding a key that already exists returns
/// `errSecDuplicateItem`, reading a missing key returns `errSecItemNotFound`, and
/// deleting a missing key returns `errSecItemNotFound` too (which `delete(key:)`
/// treats as success).
///
/// It touches no security API, so a self-check may run the **success** path
/// freely. `forcedAddStatus` re-introduces failure deterministically.
final class InMemoryKeychainBackend: KeychainBackend {
    private var storage: [String: Data] = [:]

    /// When set, every `add` returns this status and stores nothing.
    var forcedAddStatus: OSStatus?

    /// Number of stored items — asserted so "the write happened" is a real check
    /// rather than an inference from a returned value.
    var itemCount: Int { storage.count }

    private func key(for query: CFDictionary) -> String? {
        let dictionary = query as NSDictionary
        guard let service = dictionary[kSecAttrService] as? String,
              let account = dictionary[kSecAttrAccount] as? String
        else { return nil }
        // U+001F (unit separator): cannot appear in a keychain service or account.
        return service + "\u{1F}" + account
    }

    func add(_ query: CFDictionary) -> OSStatus {
        if let forcedAddStatus { return forcedAddStatus }
        guard let key = key(for: query),
              let data = (query as NSDictionary)[kSecValueData] as? Data
        else { return errSecParam }
        guard storage[key] == nil else { return errSecDuplicateItem }
        storage[key] = data
        return errSecSuccess
    }

    func copyMatching(_ query: CFDictionary, _ result: UnsafeMutablePointer<AnyObject?>?) -> OSStatus {
        guard let key = key(for: query), let data = storage[key] else {
            return errSecItemNotFound
        }
        result?.pointee = data as AnyObject
        return errSecSuccess
    }

    func delete(_ query: CFDictionary) -> OSStatus {
        guard let key = key(for: query) else { return errSecParam }
        return storage.removeValue(forKey: key) == nil ? errSecItemNotFound : errSecSuccess
    }
}

/// Thin wrapper over the generic-password keychain.
///
/// Every operation reports failure. The previous revision called `SecItemAdd`
/// and discarded its return code, so a rejected write looked exactly like a
/// successful one — which is why contract K5 exists.
///
/// Caller contract:
/// - `saveAccessToken(_:)` / `deleteAccessToken()` / `getOrCreateDeviceID()`
///   **throw** on real failures. A caller that cannot propagate the error must
///   surface it explicitly (log it, record it, present it) — `try?` is not an
///   acceptable substitute and would reintroduce exactly the silent point K5
///   forbids.
/// - `getAccessToken()` throws only for real failures; "nothing stored yet" is
///   `nil`, not an error.
class KeychainService {
    static let shared = KeychainService()

    /// Real service name. Verification runs are redirected away from it.
    static let defaultService = "com.localbook.readflow"
    /// Throwaway service used when a failure is forced (see `init`).
    static let selfTestService = "com.localbook.readflow.selftest"

    private let service: String

    /// Injection seams so both paths are testable without touching the real
    /// keychain. Two forms, in this order of preference:
    ///
    /// 1. `backend:` — a whole `KeychainBackend`. Carries state, so it can prove
    ///    the **success** path (save → read back → delete) as well as a pinned
    ///    failure (`InMemoryKeychainBackend.forcedAddStatus`). This is what
    ///    `case10` in `StorageSelfCheck.swift` uses.
    /// 2. `addItem:`/`copyMatching:`/`deleteItem:` — per-call status seams, for a
    ///    test that only needs one operation pinned; `case6` uses these.
    ///
    /// Both are in-process seams, so an *external* driver (a verifier running the
    /// built binary) cannot use them. For that case `init` also honours two
    /// environment variables, which is what makes the K5 failure path observable
    /// from a shell:
    ///
    ///     READFLOW_KEYCHAIN_FAIL=<OSStatus>   make the add step return this
    ///                                         status, e.g. `1` or `-25293`
    ///                                         (`errSecAuthFailed`)
    ///     READFLOW_KEYCHAIN_SERVICE=<name>    use a different service name
    ///
    /// A forced failure additionally (a) switches to `selfTestService` and
    /// (b) reports the pre-save delete as "item not found", so a verification run
    /// **never deletes or overwrites the user's real entry**. The hook can only
    /// make an operation fail — it cannot make one succeed, skip a status check,
    /// or expose data.
    private let addItem: (CFDictionary) -> OSStatus
    private let copyMatching: (CFDictionary, UnsafeMutablePointer<AnyObject?>?) -> OSStatus
    private let deleteItem: (CFDictionary) -> OSStatus

    init(
        service: String? = nil,
        backend: (any KeychainBackend)? = nil,
        addItem: ((CFDictionary) -> OSStatus)? = nil,
        copyMatching: ((CFDictionary, UnsafeMutablePointer<AnyObject?>?) -> OSStatus)? = nil,
        deleteItem: ((CFDictionary) -> OSStatus)? = nil
    ) {
        let environment = ProcessInfo.processInfo.environment
        let forcedAddStatus = environment["READFLOW_KEYCHAIN_FAIL"].flatMap { Int32($0) }
        let serviceOverride = environment["READFLOW_KEYCHAIN_SERVICE"]

        // The forced-failure hook also moves the service name aside, so a
        // verification run cannot reach the user's real entry.
        if forcedAddStatus != nil {
            self.service = service ?? serviceOverride ?? Self.selfTestService
        } else {
            self.service = service ?? serviceOverride ?? Self.defaultService
        }

        // A whole backend is the preferred seam: it carries state, so the
        // success path is testable. The per-call seams stay for callers that only
        // need one status pinned. Precedence is explicit injection (closure, then
        // backend), then the environment hook, then the real API.
        let resolvedAdd: ((CFDictionary) -> OSStatus)?
        let resolvedCopy: ((CFDictionary, UnsafeMutablePointer<AnyObject?>?) -> OSStatus)?
        let resolvedDelete: ((CFDictionary) -> OSStatus)?
        if let backend {
            resolvedAdd = addItem ?? { backend.add($0) }
            resolvedCopy = copyMatching ?? { backend.copyMatching($0, $1) }
            resolvedDelete = deleteItem ?? { backend.delete($0) }
        } else {
            resolvedAdd = addItem
            resolvedCopy = copyMatching
            resolvedDelete = deleteItem
        }

        if let injected = resolvedAdd {
            self.addItem = injected
        } else if let forced = forcedAddStatus {
            self.addItem = { _ in forced }
        } else {
            self.addItem = { SecItemAdd($0, nil) }
        }

        if let injected = resolvedCopy {
            self.copyMatching = injected
        } else {
            self.copyMatching = { SecItemCopyMatching($0, $1) }
        }

        if let injected = resolvedDelete {
            self.deleteItem = injected
        } else if forcedAddStatus != nil {
            // Report "nothing there" so the pre-save delete in `save` cannot
            // remove a real item while a failure is being forced.
            self.deleteItem = { _ in errSecItemNotFound }
        } else {
            self.deleteItem = { SecItemDelete($0) }
        }
    }

    // MARK: - Access Token

    func saveAccessToken(_ token: String) throws {
        try save(key: "access_token", value: token)
    }

    /// Returns `nil` when nothing is stored — a normal state, not an error.
    /// Throws when the keychain itself misbehaves.
    func getAccessToken() throws -> String? {
        try get(key: "access_token")
    }

    func deleteAccessToken() throws {
        try delete(key: "access_token")
    }

    // MARK: - Cloud API Key

    /// Stores the user's OpenAI-compatible API key.
    ///
    /// The key lives in the keychain and **nowhere else** — contract K1/K2 forbid
    /// UserDefaults, plists, config files, logs, error messages and `print`
    /// output as storage for it. The account string is a stable literal
    /// (`cloud_api_key`) per K1, so the entry survives app updates.
    ///
    /// `throws` rather than swallowing the status: a rejected write is a real
    /// failure and the settings panel must be able to say so. Returning silently
    /// here would let the UI report "saved" over a keychain that refused.
    func saveCloudAPIKey(_ key: String) throws {
        try save(key: "cloud_api_key", value: key)
    }

    /// Returns `nil` when no cloud key has been stored — a normal state, not an
    /// error. Throws only when the keychain itself misbehaves, so callers can
    /// keep "never configured" and "read failed" as distinct outcomes.
    func getCloudAPIKey() throws -> String? {
        try get(key: "cloud_api_key")
    }

    func deleteCloudAPIKey() throws {
        try delete(key: "cloud_api_key")
    }

    // MARK: - LocalBook password (HTTP Basic)

    /// Password for a deployment that is gated in front of the API.
    ///
    /// The public deployment answers every path with
    /// `401 / WWW-Authenticate: Basic realm="LocalBook"` (nginx), so a credential
    /// is required before the app's own bearer token is ever consulted. It is a
    /// secret and therefore lives here — username and URL are not secrets and
    /// live in UserDefaults, the same split the cloud key uses (K1/K2).
    func saveLocalBookPassword(_ password: String) throws {
        try save(key: "localbook_basic_password", value: password)
    }

    /// `nil` means "no password stored" — a normal state, not a failure. Throws
    /// only when the keychain itself misbehaves, so "never configured" and
    /// "read failed" stay distinguishable.
    func getLocalBookPassword() throws -> String? {
        try get(key: "localbook_basic_password")
    }

    func deleteLocalBookPassword() throws {
        try delete(key: "localbook_basic_password")
    }

    // MARK: - Device ID

    func getOrCreateDeviceID() throws -> UUID {
        if let stored = try get(key: "device_id"), let uuid = UUID(uuidString: stored) {
            return uuid
        }

        let newID = UUID()
        try save(key: "device_id", value: newID.uuidString)
        return newID
    }

    // MARK: - Private Methods

    private func save(key: String, value: String) throws {
        guard let data = value.data(using: .utf8) else {
            throw KeychainError.encodingFailed(operation: "save(\(key))")
        }

        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecValueData as String: data
        ]

        // Replacing an existing item is the documented two-step: delete, then add.
        // A missing item is the expected outcome of the first step, not a failure.
        let deleteStatus = deleteItem(query as CFDictionary)
        if deleteStatus != errSecSuccess && deleteStatus != errSecItemNotFound {
            throw KeychainError.unexpectedStatus(deleteStatus, operation: "SecItemDelete(\(key)) before save")
        }

        let addStatus = addItem(query as CFDictionary)
        guard addStatus == errSecSuccess else {
            throw KeychainError.unexpectedStatus(addStatus, operation: "SecItemAdd(\(key))")
        }
    }

    private func get(key: String) throws -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true
        ]

        var result: AnyObject?
        let status = copyMatching(query as CFDictionary, &result)

        if status == errSecItemNotFound {
            return nil
        }
        guard status == errSecSuccess else {
            throw KeychainError.unexpectedStatus(status, operation: "SecItemCopyMatching(\(key))")
        }
        guard let data = result as? Data else {
            // Success status with no data is a keychain contract violation.
            throw KeychainError.unexpectedStatus(errSecInternalError, operation: "SecItemCopyMatching(\(key)) returned no data")
        }
        guard let value = String(data: data, encoding: .utf8) else {
            throw KeychainError.encodingFailed(operation: "get(\(key)) decode")
        }

        return value
    }

    private func delete(key: String) throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key
        ]

        let status = deleteItem(query as CFDictionary)
        // Deleting something that is not there already achieves the caller's goal.
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainError.unexpectedStatus(status, operation: "SecItemDelete(\(key))")
        }
    }
}
