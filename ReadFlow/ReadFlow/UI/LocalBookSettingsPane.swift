//
//  LocalBookSettingsPane.swift
//  ReadFlow
//
//  Where the LocalBook backend is configured: address, HTTP Basic username and
//  password, and a connection test that reports what actually came back.
//
//  Why this exists at all: on the same machine ReadFlow talks to
//  `http://127.0.0.1:3780` with no gate, and nothing needed configuring. Reaching
//  the backend from outside the LAN means going through
//  `https://note.ningyd.com`, where nginx challenges every request with
//  `Basic realm="LocalBook"` — so the address *and* a credential are required, and
//  a wrong password looks exactly like a wrong address unless the UI says which.
//
//  The copy follows the same rule as the AI provider panel: a disabled control
//  explains itself (C18), a secret states where it is kept, and an insecure
//  address is refused rather than warned about.
//

import SwiftUI

struct LocalBookSettingsPane: View {

    @StateObject private var store = LocalBookSettingsStore()
    @State private var saveError: String?
    @State private var savedNotice: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                statusCard
                addressCard
                pairingCard
                testCard
            }
            .padding(18)
        }
        .frame(minWidth: 560, minHeight: 460)
        .background(Color(nsColor: .windowBackgroundColor))
        .onAppear(perform: load)
    }

    // MARK: - Status

    private var statusCard: some View {
        RFCard(title: "连接状态") {
            HStack(alignment: .top, spacing: 10) {
                RFStatusDot(tone: statusTone).padding(.top, 4)
                Text(statusText).font(.system(size: 13, weight: .semibold))
                Spacer(minLength: 12)
                Text(statusExplanation)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.trailing)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var statusTone: RFTone {
        guard store.isUsable else { return .neutral }
        switch store.probe {
        case .reached(_, true): return .success
        case .reached: return .warning
        case .unreachable: return .danger
        default: return .accent
        }
    }

    private var statusText: String {
        guard store.isUsable else { return "未配置" }
        if let message = store.probe.message { return message }
        return store.settings.user.isEmpty ? "已配置（无鉴权）" : "已配置（HTTP Basic）"
    }

    private var statusExplanation: String {
        guard store.isUsable else {
            return "默认使用本机服务；在外网请填写 https 地址与访问凭据"
        }
        if store.settings.user.isEmpty {
            return "直连同一台机器或局域网内的 LocalBook 服务"
        }
        return "经公网域名访问，凭据以 HTTP Basic 发送（仅 https）"
    }

    // MARK: - Address

    private var addressCard: some View {
        RFCard(title: "服务地址") {
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    Button("本机服务") {
                        store.settings.baseURL = "http://127.0.0.1:3780"
                    }
                    .buttonStyle(.link)
                    Button("公网地址") {
                        store.settings.baseURL = "https://note.ningyd.com"
                    }
                    .buttonStyle(.link)
                    Spacer()
                }

                RFFieldRow(label: "地址",
                           help: "本机或局域网可用 http；公网必须 https",
                           warning: store.endpointIssue?.message) {
                    TextField("https://note.ningyd.com", text: $store.settings.baseURL)
                        .textFieldStyle(.roundedBorder)
                }

                RFFieldRow(label: "用户名",
                           help: "公网部署由 nginx 鉴权（HTTP Basic）；本机服务留空即可") {
                    TextField("留空表示无需鉴权", text: $store.settings.user)
                        .textFieldStyle(.roundedBorder)
                }

                RFFieldRow(label: "密码",
                           help: "只写入系统钥匙串，不写入 UserDefaults、配置文件或日志。") {
                    SecureField("输入访问密码", text: $store.settings.password)
                        .textFieldStyle(.roundedBorder)
                }

                if let saveError {
                    notice(saveError, tone: .danger, systemImage: "exclamationmark.triangle.fill")
                }
                if let savedNotice {
                    notice(savedNotice, tone: .success, systemImage: "checkmark.circle.fill")
                }

                HStack(spacing: 8) {
                    Button("保存") { save() }
                        .buttonStyle(.borderedProminent)
                        .disabled(!store.isUsable)
                    Button("清除", role: .destructive) { clear() }
                        .disabled(store.settings.isBlank && store.settings.user.isEmpty)
                    Spacer()
                }
                .padding(.leading, RFMetric.labelColumn + 12)
            }
        }
    }

    // MARK: - Reader pairing

    private var pairingCard: some View {
        RFCard(title: "Reader 配对") {
            VStack(alignment: .leading, spacing: 10) {
                Text("先生成一次性配对码，在 LocalBook 网页设置中批准，再返回此处完成配对。访问令牌只保存在系统钥匙串。")
                    .font(.system(size: 10.5))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: 10) {
                    Button(store.isPairing ? "配对中…" : "生成配对码") {
                        Task { await store.beginPairing() }
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(!store.isUsable || store.isPairing)

                    if store.pairingCode != nil {
                        Button("完成配对") {
                            Task { await store.finishPairing() }
                        }
                        .disabled(store.isPairing)
                    }

                    if store.isPairing { ProgressView().controlSize(.small) }
                    Spacer()
                }

                if let code = store.pairingCode {
                    HStack(spacing: 8) {
                        Text("本次配对码")
                        Text(code)
                            .font(.system(size: 18, weight: .bold, design: .monospaced))
                            .textSelection(.enabled)
                    }
                }
                if let status = store.pairingStatus {
                    Text(status)
                        .font(.system(size: 10.5))
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.leading, RFMetric.labelColumn + 12)
        }
    }

    // MARK: - Test

    private var testCard: some View {
        RFCard(title: "连接测试") {
            VStack(alignment: .leading, spacing: 10) {
                HStack(spacing: 12) {
                    Button("测试连接") {
                        savedNotice = nil
                        Task { await store.testConnection() }
                    }
                    .disabled(!store.isUsable || store.probe == .running)

                    if store.probe == .running { ProgressView().controlSize(.small) }
                    Spacer()
                }

                if let message = store.probe.message {
                    notice(message,
                          tone: store.probe.isSuccess ? .success : .warning,
                          systemImage: store.probe.isSuccess ? "checkmark.circle.fill" : "info.circle.fill")
                }

                if !store.isUsable {
                    // C18: a disabled control explains itself.
                    Text("需要先填写有效的地址才能测试连接。")
                        .font(.system(size: 10.5))
                        .foregroundStyle(.tertiary)
                }

                Text("测试只向该地址发起一次 GET 根路径请求，不会发送任何数据。"
                     + "公网部署会返回 401 —— 那说明域名、TLS 与鉴权链路都是通的。")
                    .font(.system(size: 10.5))
                    .foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func notice(_ text: String, tone: RFTone, systemImage: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: systemImage).font(.system(size: 10))
            Text(text).font(.system(size: 10.5)).fixedSize(horizontal: false, vertical: true)
        }
        .foregroundStyle(tone.foreground)
        .padding(.leading, RFMetric.labelColumn + 12)
    }

    // MARK: - Actions

    private func load() {
        saveError = store.load()
        savedNotice = nil
    }

    private func save() {
        saveError = nil
        savedNotice = nil
        do {
            _ = try store.save()
            savedNotice = "已保存。地址与用户名存在 UserDefaults，密码存在系统钥匙串。"
        } catch {
            // The panel must not print "saved" over a keychain that refused (K5).
            saveError = error.localizedDescription
        }
    }

    private func clear() {
        store.clear()
        saveError = nil
        savedNotice = "已清除已保存的地址与凭据。"
    }
}
