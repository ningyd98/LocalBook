//
//  ReadFlowApp.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//  ReadFlow - LocalBook 的 macOS 原生阅读采集客户端
//

import SwiftUI

@main
struct ReadFlowApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @Environment(\.openWindow) private var openWindow
    @StateObject private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(appState)
                .onAppear {
                    appDelegate.openSettingsWindow = { openWindow(id: "settings") }
                }
        }
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Highlight") {
                    appState.captureSelection()
                }
                .keyboardShortcut("h", modifiers: [.command, .shift])
            }
        }

        // Menu Bar Item
        MenuBarExtra("ReadFlow", systemImage: "book.fill") {
            MenuBarView()
                .environmentObject(appState)
        }

        // Settings window. `UI/MenuBarView.swift` opens it with
        // `openWindow(id: "settings")`, so the identifier has to match.
        Window("ReadFlow 设置", id: "settings") {
            SettingsView()
                .environmentObject(appState)
        }

        // Local search window. Opened from the menu bar with
        // `openWindow(id: "search")`; the identifier has to match. This is the
        // standalone search/browse entry: it reads the local FTS5 index only and
        // issues no network request (contract C8), which is what keeps it usable
        // with no server and no network.
        Window("ReadFlow 搜索", id: "search") {
            SearchWindowView()
                .environmentObject(appState)
        }
    }
}

struct ContentView: View {
    @EnvironmentObject var appState: AppState

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "book.fill")
                .font(.system(size: 60))
                .foregroundColor(.accentColor)

            Text("ReadFlow")
                .font(.largeTitle)
                .fontWeight(.bold)

            Text("LocalBook 阅读采集客户端")
                .font(.headline)
                .foregroundColor(.secondary)

            Divider()
                .padding(.vertical)

            VStack(alignment: .leading, spacing: 12) {
                StatusRow(
                    icon: "network",
                    title: "LocalBook 连接",
                    value: appState.connectionStatus
                )

                StatusRow(
                    icon: "clock",
                    title: "最后同步",
                    value: appState.lastSyncTime
                )

                StatusRow(
                    icon: "star.fill",
                    title: "待同步项",
                    value: "\(appState.pendingSyncCount)"
                )
            }
            .padding()
            .background(Color.gray.opacity(0.1))
            .cornerRadius(12)

            Spacer()

            HStack(spacing: 16) {
                Button(action: {
                    appState.discoverLocalBook()
                }) {
                    Label("发现服务", systemImage: "magnifyingglass")
                }

                Button(action: {
                    appState.triggerSync()
                }) {
                    Label("立即同步", systemImage: "arrow.triangle.2.circlepath")
                }
                .disabled(!appState.isConnected)
            }
        }
        .padding(40)
        .frame(minWidth: 400, minHeight: 500)
    }
}

struct StatusRow: View {
    let icon: String
    let title: String
    let value: String

    var body: some View {
        HStack {
            Image(systemName: icon)
                .foregroundColor(.accentColor)
                .frame(width: 24)

            Text(title)
                .fontWeight(.medium)

            Spacer()

            Text(value)
                .foregroundColor(.secondary)
        }
    }
}

// `#Preview` needs the `PreviewsMacros` plugin, which ships with Xcode only.
// A Command Line Tools build has to use the macro-free `PreviewProvider` form.
struct ContentView_Previews: PreviewProvider {
    static var previews: some View {
        ContentView()
            .environmentObject(AppState())
    }
}
