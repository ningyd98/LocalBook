//
//  MenuBarView.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import SwiftUI

struct MenuBarView: View {
    @EnvironmentObject var appState: AppState
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(spacing: 0) {
            // Header
            headerSection

            Divider()

            // Connection Status
            statusSection

            Divider()

            // Quick Actions
            actionsSection

            Divider()

            // Footer
            footerSection
        }
        .frame(width: 280)
    }

    // MARK: - Sections

    private var headerSection: some View {
        HStack {
            Image(systemName: "book.circle.fill")
                .font(.system(size: 24))
                .foregroundColor(.blue)

            VStack(alignment: .leading, spacing: 2) {
                Text("ReadFlow")
                    .font(.headline)
                Text("阅读采集助手")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Spacer()
        }
        .padding()
    }

    private var statusSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Circle()
                    .fill(appState.isConnected ? Color.green : Color.gray)
                    .frame(width: 8, height: 8)

                Text(appState.connectionStatus)
                    .font(.subheadline)

                Spacer()
            }

            HStack {
                Text("上次同步:")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Text(appState.lastSyncTime)
                    .font(.caption)

                Spacer()

                if appState.pendingSyncCount > 0 {
                    Text("\(appState.pendingSyncCount) 待同步")
                        .font(.caption)
                        .foregroundColor(.orange)
                }
            }
        }
        .padding()
    }

    private var actionsSection: some View {
        VStack(spacing: 4) {
            MenuButton(
                icon: "plus.circle.fill",
                title: "捕获划选内容",
                subtitle: "⌘⇧C",
                action: { appState.captureSelection() }
            )

            MenuButton(
                icon: "arrow.triangle.2.circlepath",
                title: "立即同步",
                subtitle: appState.isSyncing ? "同步中..." : nil,
                action: { appState.triggerSync() },
                disabled: appState.isSyncing
            )

            MenuButton(
                icon: "magnifyingglass",
                title: "搜索笔记",
                subtitle: "⌘⇧F",
                // Opens the local search window. This used to call
                // `appState.showSearchPanel()`, which only printed a line — so
                // the menu advertised a feature that did not exist. The window is
                // local-only (C8): no server, no network.
                action: { openWindow(id: "search") }
            )

            MenuButton(
                icon: "wifi.circle",
                title: "发现 LocalBook",
                action: { appState.discoverLocalBook() }
            )
        }
        .padding(.vertical, 8)
    }

    private var footerSection: some View {
        HStack {
            Button(action: {
                openWindow(id: "settings")
            }) {
                Image(systemName: "gearshape")
                Text("设置")
            }
            .buttonStyle(.plain)

            Spacer()

            Button(action: {
                NSApplication.shared.terminate(nil)
            }) {
                Text("退出")
            }
            .buttonStyle(.plain)
        }
        .padding()
        .font(.caption)
    }
}

struct MenuButton: View {
    let icon: String
    let title: String
    var subtitle: String?
    let action: () -> Void
    var disabled: Bool = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: icon)
                    .font(.system(size: 16))
                    .frame(width: 20)
                    .foregroundColor(disabled ? .gray : .blue)

                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.subheadline)

                    if let subtitle = subtitle {
                        Text(subtitle)
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                }

                Spacer()
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .disabled(disabled)
        .opacity(disabled ? 0.6 : 1.0)
    }
}

// `#Preview` needs the `PreviewsMacros` plugin, which ships with Xcode only.
// A Command Line Tools build has to use the macro-free `PreviewProvider` form.
struct MenuBarView_Previews: PreviewProvider {
    static var previews: some View {
        MenuBarView()
            .environmentObject(AppState())
    }
}
