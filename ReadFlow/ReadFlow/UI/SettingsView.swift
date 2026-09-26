//
//  SettingsView.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var appState: AppState

    @State private var serverURL: String = ""
    @State private var autoSync: Bool = true
    @State private var syncInterval: Int = 5
    @State private var captureShortcut: String = "⌘⇧C"
    @State private var searchShortcut: String = "⌘⇧F"

    var body: some View {
        TabView {
            GeneralSettings()
                .tabItem {
                    Label("通用", systemImage: "gearshape")
                }

            SyncSettings(
                serverURL: $serverURL,
                autoSync: $autoSync,
                syncInterval: $syncInterval
            )
            .tabItem {
                Label("同步", systemImage: "arrow.triangle.2.circlepath")
            }

            LocalBookSettingsPane()
                .tabItem {
                    Label("LocalBook", systemImage: "network")
                }

            ShortcutSettings(
                captureShortcut: $captureShortcut,
                searchShortcut: $searchShortcut
            )
            .tabItem {
                Label("快捷键", systemImage: "keyboard")
            }

            // The AI provider panel is its own tab so the settings window hosts
            // exactly one place where the cloud endpoint, model and key are
            // configured (contract §6.4). Its content is unchanged by being
            // embedded here.
            AIProviderSettingsPane()
                .tabItem {
                    Label("AI 供应商", systemImage: "brain")
                }

            AboutSettings()
                .tabItem {
                    Label("关于", systemImage: "info.circle")
                }
        }
        .frame(width: 560, height: 460)
    }
}

struct GeneralSettings: View {
    @State private var launchAtLogin: Bool = false
    @State private var showMenuBarIcon: Bool = true
    @State private var showNotifications: Bool = true

    var body: some View {
        Form {
            Section {
                Toggle("开机自动启动", isOn: $launchAtLogin)
                Toggle("显示菜单栏图标", isOn: $showMenuBarIcon)
                Toggle("显示通知", isOn: $showNotifications)
            } header: {
                Text("启动设置")
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}

struct SyncSettings: View {
    @Binding var serverURL: String
    @Binding var autoSync: Bool
    @Binding var syncInterval: Int

    var body: some View {
        Form {
            Section {
                TextField("服务器地址", text: $serverURL)
                    .textFieldStyle(.roundedBorder)

                HStack {
                    Text("状态:")
                    Spacer()
                    Text("未连接")
                        .foregroundColor(.secondary)
                }

                Button("发现 LocalBook 服务") {
                    // Trigger Bonjour discovery
                }
            } header: {
                Text("服务器")
            }

            Section {
                Toggle("自动同步", isOn: $autoSync)

                if autoSync {
                    Picker("同步间隔", selection: $syncInterval) {
                        Text("1 分钟").tag(1)
                        Text("5 分钟").tag(5)
                        Text("10 分钟").tag(10)
                        Text("30 分钟").tag(30)
                    }
                }
            } header: {
                Text("同步设置")
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}

struct ShortcutSettings: View {
    @Binding var captureShortcut: String
    @Binding var searchShortcut: String

    var body: some View {
        Form {
            Section {
                HStack {
                    Text("捕获划选")
                    Spacer()
                    Text(captureShortcut)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.secondary.opacity(0.2))
                        .cornerRadius(4)
                }

                HStack {
                    Text("搜索笔记")
                    Spacer()
                    Text(searchShortcut)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.secondary.opacity(0.2))
                        .cornerRadius(4)
                }
            } header: {
                Text("全局快捷键")
            } footer: {
                Text("点击快捷键文本以重新设置")
                    .font(.caption)
            }
        }
        .formStyle(.grouped)
        .padding()
    }
}

struct AboutSettings: View {
    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "book.circle.fill")
                .font(.system(size: 64))
                .foregroundColor(.blue)

            Text("ReadFlow")
                .font(.title)

            Text("版本 1.3.0")
                .font(.subheadline)
                .foregroundColor(.secondary)

            Text("LocalBook 阅读采集客户端")
                .font(.caption)

            Spacer()

            VStack(spacing: 8) {
                Link("项目主页", destination: URL(string: "https://github.com/localbook/readflow")!)
                Link("使用文档", destination: URL(string: "https://docs.localbook.app")!)
                Link("报告问题", destination: URL(string: "https://github.com/localbook/readflow/issues")!)
            }
            .font(.caption)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }
}

// `#Preview` needs the `PreviewsMacros` plugin, which ships with Xcode only.
// A Command Line Tools build has to use the macro-free `PreviewProvider` form.
struct SettingsView_Previews: PreviewProvider {
    static var previews: some View {
        SettingsView()
            .environmentObject(AppState())
    }
}
