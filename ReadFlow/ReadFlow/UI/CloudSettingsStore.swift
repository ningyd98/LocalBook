//
//  CloudSettingsStore.swift
//  ReadFlow
//
//  Reads and writes the cloud-provider configuration (contract §6.4).
//
//  The storage split is not a preference, it is the contract:
//    · `cloud_base_url` / `cloud_model` / `cloud_enabled` → **UserDefaults**
//      (§6.4.1 names these keys exactly);
//    · the API key → **keychain only** (K1). It must never be written to
//      UserDefaults, a plist, a config file, a log line or an error message, and
//      it must not appear in a URL query string.
//
//  Two further rules shape this type:
//    · an empty endpoint is *unconfigured*, not invalid — the panel must not
//      shout at a user who has not typed anything yet;
//    · a rejected keychain write is a **failure**, never a quiet success. So
//      `save` throws, and callers surface it. `try?` here would let the UI claim
//      "saved" over a keychain that refused the item.
//

import Foundation

/// The persisted cloud configuration, plus validation state for the UI.
struct CloudSettings: Equatable {
    var baseURL: String
    var model: String
    var enabled: Bool
    /// The key is held in memory only for the duration of an edit; the
    /// authoritative copy is in the keychain.
    var apiKey: String

    /// Whether cloud is usable per `CloudConfiguration.isUsable`.
    var isUsable: Bool {
        enabled
            && !baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !apiKey.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// The provider state the UI renders (§6.3 S1/S2/S3 vocabulary).
    ///
    /// Note this is *configuration* state, not per-message attribution: it
    /// answers "what would happen if the user asked right now", whereas
    /// `AttributionDisplay` answers "who produced this existing message".
    var configurationState: CloudConfigurationState {
        if isUsable { return .configured }
        return .notConfigured
    }
}

/// How the provider panel describes the current configuration (§6.3).
enum CloudConfigurationState: Equatable {
    /// S1 — nothing usable is configured. The badge reads 「未配置」(grey) and
    /// the panel must point the user at the fields rather than just greying out.
    case notConfigured
    /// Endpoint + key present and enabled. Reachability is a *separate* question
    /// answered by the connection test, so this state does not imply S3.
    case configured
}

/// Loads and stores the cloud configuration.
final class CloudSettingsStore {

    /// Contract §6.4.1 pins these key names; they are part of the interface, not
    /// an implementation detail.
    enum Key {
        static let baseURL = "cloud_base_url"
        static let model = "cloud_model"
        static let enabled = "cloud_enabled"
    }

    private let defaults: UserDefaults
    private let keychain: KeychainService

    init(defaults: UserDefaults = .standard, keychain: KeychainService = .shared) {
        self.defaults = defaults
        self.keychain = keychain
    }

    /// Reads the stored configuration.
    ///
    /// A keychain *read* failure is reported separately from an absent key:
    /// "never configured" is a normal state that sends the user to the fields,
    /// whereas "the keychain refused to be read" is an error worth showing. The
    /// contract keeps those distinct (K4), so this does too.
    func load() -> (settings: CloudSettings, keychainError: KeychainError?) {
        let baseURL = defaults.string(forKey: Key.baseURL) ?? ""
        let model = defaults.string(forKey: Key.model) ?? ""
        let enabled = defaults.bool(forKey: Key.enabled)

        var apiKey = ""
        var keychainError: KeychainError?
        do {
            apiKey = try keychain.getCloudAPIKey() ?? ""
        } catch let error as KeychainError {
            keychainError = error
        } catch {
            // Any non-KeychainError is still surfaced rather than dropped.
            keychainError = .encodingFailed(operation: "读取云端 API Key")
        }

        return (
            CloudSettings(baseURL: baseURL, model: model, enabled: enabled, apiKey: apiKey),
            keychainError
        )
    }

    /// Persists the configuration.
    ///
    /// Order matters: the keychain write goes first, so a rejected key does not
    /// leave UserDefaults holding a configuration that can never authenticate.
    func save(_ settings: CloudSettings) throws {
        let trimmedKey = settings.apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmedKey.isEmpty {
            // Clearing the field is a deliberate removal, not a failed save.
            try keychain.deleteCloudAPIKey()
        } else {
            try keychain.saveCloudAPIKey(trimmedKey)
        }

        defaults.set(settings.baseURL, forKey: Key.baseURL)
        defaults.set(settings.model, forKey: Key.model)
        defaults.set(settings.enabled, forKey: Key.enabled)
    }

    /// Builds the value the coordinator consumes.
    ///
    /// Returns `nil` when nothing is usable, which is precisely what makes S1 a
    /// real state: `AICoordinator.make` then receives no cloud provider at all,
    /// so a disabled or half-filled endpoint can never be dialled by accident.
    func cloudConfiguration(from settings: CloudSettings) -> CloudConfiguration? {
        guard settings.isUsable else { return nil }
        return CloudConfiguration(
            baseURL: settings.baseURL,
            model: settings.model,
            apiKey: settings.apiKey,
            enabled: settings.enabled
        )
    }

    /// The stored configuration in the form `AICoordinator.make` wants.
    ///
    /// Convenience for the *live* answering path, which must build its
    /// coordinator from whatever the user configured rather than from a constant.
    /// It lives here and not in `AI/` so the provider layer stays unaware of where
    /// settings are persisted: the store knows about defaults and the keychain,
    /// the coordinator only knows a `CloudConfiguration`.
    var cloudConfiguration: CloudConfiguration? {
        let (settings, _) = load()
        return cloudConfiguration(from: settings)
    }
}
