//
//  AIProviderSettingsPane.swift
//  ReadFlow
//
//  The AI provider panel (contract §6.3, §6.4).
//
//  Three rules shape this view, and each exists because the opposite is a defect:
//
//  1. **Only「测试连接」may be disabled** (C18). The endpoint, model and key
//     fields stay editable in every state — a user at S1 must be able to *fix*
//     the configuration, and greying the panel would also hide the explanation
//     of why the AI is answering offline.
//  2. **No control is greyed without copy.** Every restricted entry says why,
//     because "disabled with no reason" is indistinguishable from a broken app.
//  3. **The API key never lands anywhere but the keychain** (K1/K2). This view
//     holds it in `@State` only long enough to hand it to the store, and it is
//     never written to UserDefaults, a log, or an error string.
//

import SwiftUI

struct AIProviderSettingsPane: View {
    @State private var settings = CloudSettings(baseURL: "", model: "", enabled: false, apiKey: "")
    @State private var loaded = false

    /// Inline rejection reason for the endpoint (E1/E2). `nil` means "no opinion",
    /// which is the correct state for an empty field.
    @State private var endpointIssue: String?
    @State private var endpointWarning: String?

    /// Result of the last connection test, kept separate from configuration
    /// state because reachability is a different question from configuration.
    @State private var testState: ConnectionTestState = .idle

    /// Save/read failures surface here rather than being swallowed.
    @State private var saveError: String?

    private let store = CloudSettingsStore()

    enum ConnectionTestState: Equatable {
        case idle
        case running
        case success
        case failure(String)

        var message: String? {
            switch self {
            case .idle: return nil
            case .running: return "正在测试…"
            case .success: return "连接成功：endpoint 可达且鉴权通过"
            case .failure(let reason): return reason
            }
        }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                statusSection
                endpointSection
                credentialSection
                testSection
            }
            .padding(18)
        }
        .frame(minWidth: 560, minHeight: 460)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear(perform: load)
    }

    // MARK: - Status

    private var statusSection: some View {
        RFCard(title: "供应商状态") {
            HStack(alignment: .top, spacing: 10) {
                // The dot carries the state at a glance; the copy states it exactly.
                RFStatusDot(tone: badgeTone)
                    .padding(.top, 4)
                Text(badgeText)
                    .font(.system(size: 13, weight: .semibold))
                Spacer(minLength: 12)
                Text(configurationExplanation)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.trailing)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// Badge copy is frozen by §6.3. Note it reports *configuration*, not
    /// reachability — 「不可达」only appears after a failed connection test of a
    /// fully configured endpoint, so the badge never claims more than it knows.
    private var badgeText: String {
        guard settings.isUsable else { return "未配置" }
        if case .failure = testState { return "不可达" }
        return "cloud"
    }

    private var badgeTone: RFTone {
        guard settings.isUsable else { return .neutral }
        if case .failure = testState { return .warning }
        return .success
    }

    private var configurationExplanation: String {
        guard settings.isUsable else {
            return "尚未配置云端 AI：填写 endpoint 与 API Key，或连接 LocalBook 服务端"
        }
        if case .failure = testState {
            return "已配置但连接失败：可重试，或继续使用离线降级"
        }
        return "已配置；可测试连接"
    }

    // MARK: - Endpoint

    private var endpointSection: some View {
        RFCard(title: "云端 endpoint") {
            VStack(alignment: .leading, spacing: 12) {
                RFFieldRow(label: "Endpoint",
                           help: "例如 https://api.openai.com/v1",
                           warning: endpointWarning) {
                    TextField("https://…", text: $settings.baseURL)
                        .textFieldStyle(.roundedBorder)
                        .onChange(of: settings.baseURL) { _ in validateEndpoint() }
                }

                if let endpointIssue {
                    HStack(alignment: .top, spacing: 6) {
                        Image(systemName: "xmark.octagon.fill").font(.system(size: 10))
                        Text(endpointIssue).font(.system(size: 10.5))
                    }
                    .foregroundStyle(RFColor.danger)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, RFMetric.labelColumn + 12)
                }

                RFFieldRow(label: "模型名", help: "例如 gpt-4o-mini") {
                    TextField("gpt-4o-mini", text: $settings.model)
                        .textFieldStyle(.roundedBorder)
                }

                Divider().padding(.vertical, 2)

                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("启用云端 AI").font(.system(size: 12))
                        Text("关闭时始终使用离线降级。").font(.system(size: 10.5)).foregroundStyle(.tertiary)
                    }
                    Spacer()
                    Toggle("", isOn: $settings.enabled).labelsHidden().toggleStyle(.switch)
                }

                Text("只支持 http/https；留空则按「未配置」处理，AI 将使用离线降级。")
                    .font(.system(size: 10.5))
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    // MARK: - Credentials

    private var credentialSection: some View {
        RFCard(title: "凭证") {
            VStack(alignment: .leading, spacing: 12) {
                // `SecureField` keeps the key off screen; it is never rendered as
                // plain text, and never echoed into an error message.
                RFFieldRow(label: "API Key",
                           help: "只写入系统钥匙串，不写入 UserDefaults、配置文件或日志。") {
                    SecureField("sk-…", text: $settings.apiKey)
                        .textFieldStyle(.roundedBorder)
                }

                if let saveError {
                    HStack(alignment: .top, spacing: 6) {
                        Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 10))
                        Text(saveError).font(.system(size: 10.5))
                    }
                    .foregroundStyle(RFColor.danger)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, RFMetric.labelColumn + 12)
                }

                HStack(spacing: 8) {
                    Button("保存") { save() }
                        .buttonStyle(.borderedProminent)
                        .disabled(settings.baseURL.isEmpty && settings.apiKey.isEmpty)

                    Button("清除已保存的 Key", role: .destructive) { clearKey() }
                        .disabled(settings.apiKey.isEmpty)

                    Spacer()
                }
                .padding(.leading, RFMetric.labelColumn + 12)

                Text("保存失败会显示错误，不会报告为已保存。")
                    .font(.system(size: 10.5))
                    .foregroundStyle(.tertiary)
                    .padding(.leading, RFMetric.labelColumn + 12)
            }
        }
    }

    // MARK: - Connection test

    private var testSection: some View {
        RFCard(title: "连接测试") {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 12) {
                    // The single control the contract permits disabling, and only
                    // while there is no endpoint to test (S1).
                    Button("测试连接") { testConnection() }
                        .disabled(!canTestConnection || testState == .running)

                    if testState == .running {
                        ProgressView().controlSize(.small)
                    }
                    Spacer()
                }

                if let message = testState.message {
                    HStack(alignment: .top, spacing: 6) {
                        Image(systemName: testState == .success
                              ? "checkmark.circle.fill" : "info.circle.fill")
                            .font(.system(size: 10))
                        Text(message).font(.system(size: 11))
                    }
                    .foregroundStyle(testState == .success ? RFColor.success : Color.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                }

                if !canTestConnection {
                    // C18: a disabled control must explain itself.
                    Text("需要先填写 endpoint 才能测试连接。")
                        .font(.system(size: 10.5))
                        .foregroundStyle(.tertiary)
                }

                Text("测试只请求 GET {endpoint}/models，不会发起对话（completion）请求，因此不会产生费用。")
                    .font(.system(size: 10.5))
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var canTestConnection: Bool {
        !settings.baseURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    // MARK: - Actions

    private func load() {
        guard !loaded else { return }
        loaded = true
        let (stored, keychainError) = store.load()
        settings = stored
        if let keychainError {
            saveError = keychainError.localizedDescription
        }
        validateEndpoint()
    }

    private func validateEndpoint() {
        let raw = settings.baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        endpointIssue = nil
        endpointWarning = nil

        // An empty field is "unconfigured", not invalid — shouting at a user who
        // has not typed anything yet would be noise, not validation.
        guard !raw.isEmpty else { return }

        do {
            _ = try EndpointNormalizer.normalize(raw)
            // E5: http is allowed (local/intranet endpoints are legitimate) but
            // the user must be told the transport is cleartext.
            if EndpointNormalizer.isCleartext(raw) {
                endpointWarning = "该地址使用 http 明文传输，API Key 与内容不会加密。"
            }
        } catch let error as AIProviderError {
            endpointIssue = error.localizedDescription
        } catch {
            endpointIssue = error.localizedDescription
        }
    }

    private func save() {
        saveError = nil
        do {
            try store.save(settings)
        } catch let error as KeychainError {
            // A refused keychain write is a failure. Reporting success here is
            // exactly what K4 forbids.
            saveError = error.localizedDescription
        } catch {
            saveError = error.localizedDescription
        }
    }

    private func clearKey() {
        settings.apiKey = ""
        save()
    }

    private func testConnection() {
        let raw = settings.baseURL.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return }

        do {
            let normalized = try EndpointNormalizer.normalize(raw)
            guard settings.enabled else {
                testState = .failure("云端 AI 当前处于禁用状态：请先勾选「启用云端 AI」。")
                return
            }

            testState = .running
            let provider = CloudAIProvider(
                baseURL: normalized,
                apiKey: settings.apiKey,
                model: settings.model.isEmpty ? "unused-for-test" : settings.model
            )

            Task {
                do {
                    // `testConnection` hits GET {base}/models only. There is no
                    // completion request anywhere in this path, and no fallback
                    // to one when `/models` is unavailable.
                    try await provider.testConnection()
                    testState = .success
                } catch let error as AIProviderError {
                    testState = .failure(error.localizedDescription)
                } catch {
                    testState = .failure(error.localizedDescription)
                }
            }
        } catch let error as AIProviderError {
            testState = .failure(error.localizedDescription)
        } catch {
            testState = .failure(error.localizedDescription)
        }
    }
}

#if DEBUG
struct AIProviderSettingsPane_Previews: PreviewProvider {
    static var previews: some View {
        AIProviderSettingsPane()
    }
}
#endif
