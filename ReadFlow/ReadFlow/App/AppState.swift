//
//  AppState.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import SwiftUI
import Combine

@MainActor
class AppState: ObservableObject {
    // Connection State
    @Published var isConnected: Bool = false
    @Published var connectionStatus: String = "未连接"
    @Published var localBookURL: String?

    // Sync State
    @Published var lastSyncTime: String = "从未"
    @Published var pendingSyncCount: Int = 0
    @Published var isSyncing: Bool = false

    // UI State
    @Published var showFloatingToolbar: Bool = false
    @Published var showAIPopover: Bool = false
    /// Renamed from `showSearchPanel`: a stored property and the
    /// `showSearchPanel()` action below cannot share one base name
    /// ("invalid redeclaration").
    @Published var isSearchPanelVisible: Bool = false

    // Local storage state
    /// Where the shared local store ended up after the first launch opened and
    /// migrated it. `nil` until `setupServices()` has run.
    @Published private(set) var storageMode: ReaderDatabaseStorageMode?
    /// Human-readable storage status for the UI ("本地库就绪" / "内存模式").
    @Published private(set) var storageStatus: String = "未初始化"

    // Services (to be injected)
    private var syncEngine: SyncEngine?
    private var bonjourDiscovery: BonjourDiscovery?
    private var captureService: SelectionService?
    private var cancellables = Set<AnyCancellable>()

    init() {
        // The self-check uses its own throwaway store; do not also create (and
        // migrate) the real user store as a side effect of running it.
        if CommandLine.arguments.contains(StorageSelfCheck.flag) {
            storageStatus = "自检模式（不打开用户数据库）"
            return
        }

        setupServices()
        startDiscovery()
    }

    // MARK: - Setup

    private func setupServices() {
        // Open the local SQLite store during launch. Acceptance requires the
        // app to create the database and run schema migrations on first start,
        // with no server and no manual configuration involved.
        //
        // `ReaderDatabase.shared` is a lazy static, so this is what forces the
        // open + `DatabaseMigrator` run. It never throws: a location that cannot
        // be opened is skipped, and the last resort is an in-memory store, which
        // `storageMode` makes visible instead of hiding.
        let database = ReaderDatabase.shared
        storageMode = database.storageMode

        switch database.storageMode {
        case .onDisk(let url):
            storageStatus = "本地库就绪"
            AppDelegate.log("local store ready: \(url.path) (schema migrated)")
        case .inMemory(let reason):
            storageStatus = "本地库不可用（内存模式，重启即失）"
            AppDelegate.log("local store degraded to in-memory: \(reason)")
        }

        // Keep app-level state driven by the real services instead of simulating
        // connection/sync completion in the menu bar.
        let engine = SyncEngine.shared
        syncEngine = engine
        bonjourDiscovery = BonjourDiscovery()
        captureService = SelectionService.shared

        engine.$pendingCount
            .receive(on: RunLoop.main)
            .sink { [weak self] count in self?.pendingSyncCount = count }
            .store(in: &cancellables)
        engine.$lastSyncTime
            .receive(on: RunLoop.main)
            .sink { [weak self] date in
                guard let self else { return }
                lastSyncTime = date.map(formatDate) ?? "从未"
            }
            .store(in: &cancellables)
        engine.$syncState
            .receive(on: RunLoop.main)
            .sink { [weak self] state in self?.applySyncState(state) }
            .store(in: &cancellables)
        bonjourDiscovery?.$discoveredServices
            .receive(on: RunLoop.main)
            .sink { [weak self] services in
                guard let self else { return }
                localBookURL = services.first?.resolvedURL?.absoluteString
                if let first = services.first {
                    connectionStatus = "已发现 \(first.displayName)"
                } else if !isConnected {
                    connectionStatus = "未发现 LocalBook 服务"
                }
            }
            .store(in: &cancellables)
    }

    private func startDiscovery() {
        bonjourDiscovery?.startDiscovery()
        if !isConnected {
            connectionStatus = "正在搜索 LocalBook…"
        }
    }

    private func applySyncState(_ state: SyncEngine.SyncState) {
        switch state {
        case .offline:
            isConnected = false
            connectionStatus = "未连接"
        case .syncing:
            connectionStatus = "同步中…"
        case .synced:
            isConnected = true
            connectionStatus = "已连接"
        case .error(let message):
            isConnected = false
            connectionStatus = "同步失败：\(message)"
        }
    }

    // MARK: - Actions

    func captureSelection() {
        guard let selection = captureService?.captureSelection() else {
            connectionStatus = "未捕获到选中文本"
            return
        }
        showFloatingToolbar = true
        connectionStatus = "已捕获：\(selection.text.prefix(24))"
    }

    func discoverLocalBook() {
        startDiscovery()
        if let service = bonjourDiscovery?.discoveredServices.first {
            localBookURL = service.resolvedURL?.absoluteString
            connectionStatus = "已发现 \(service.displayName)"
        }
    }

    func triggerSync() {
        guard !isSyncing else { return }
        guard let engine = syncEngine else {
            connectionStatus = "同步服务未初始化"
            return
        }

        isSyncing = true
        Task { [weak self] in
            await engine.immediateSync()
            guard let self else { return }
            isSyncing = false
            applySyncState(engine.syncState)
            pendingSyncCount = engine.pendingCount
            if let date = engine.lastSyncTime {
                lastSyncTime = formatDate(date)
            }
        }
    }

    func showSearchPanel() {
        isSearchPanelVisible = true
    }

    // MARK: - Helpers

    private func formatDate(_ date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.locale = Locale(identifier: "zh_CN")
        return formatter.localizedString(for: date, relativeTo: Date())
    }
}
