//
//  SyncEngine.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import Combine

class SyncEngine: ObservableObject {
    static let shared = SyncEngine()

    @Published var syncState: SyncState = .offline
    @Published var lastSyncTime: Date?
    @Published var pendingCount: Int = 0

    private let database: ReaderDatabase
    private let client: LocalBookClient
    private var batchWindow: DispatchWorkItem?
    private var lastSyncCursor: String?
    private let userDefaults = UserDefaults.standard

    enum SyncState: Equatable {
        case offline
        case syncing
        case synced
        case error(String)

        static func == (lhs: SyncState, rhs: SyncState) -> Bool {
            switch (lhs, rhs) {
            case (.offline, .offline), (.syncing, .syncing), (.synced, .synced):
                return true
            case (.error(let lhsMsg), .error(let rhsMsg)):
                return lhsMsg == rhsMsg
            default:
                return false
            }
        }
    }

    private init() {
        self.database = ReaderDatabase.shared
        self.client = LocalBookClient.shared

        // Load last sync cursor
        self.lastSyncCursor = userDefaults.string(forKey: "last_sync_cursor")

        // Update pending count
        updatePendingCount()
    }

    // MARK: - Public API

    /// Throws when the access token cannot be persisted — the keychain refused
    /// the write (contract K5). Callers must handle it instead of assuming the
    /// client is durably configured.
    func configure(baseURL: URL, accessToken: String) throws {
        try client.configure(baseURL: baseURL, accessToken: accessToken)

        // Check if we're online
        if client.isConfigured() {
            Task {
                await checkConnectionAndSync()
            }
        }
    }

    /// Re-checks the configured Reader endpoint after settings/pairing changes.
    func refreshConnection() {
        Task { await checkConnectionAndSync() }
    }

    func triggerSync() {
        // Aggregate operations within 2-5 seconds (using 3 seconds)
        batchWindow?.cancel()
        batchWindow = DispatchWorkItem { [weak self] in
            Task {
                await self?.performSync()
            }
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3, execute: batchWindow!)
    }

    func immediateSync() async {
        await performSync()
    }

    // MARK: - Private Methods

    private func updatePendingCount() {
        do {
            let items = try database.getPendingSyncItems(limit: 1000)
            DispatchQueue.main.async {
                self.pendingCount = items.count
            }
        } catch {
            print("[SyncEngine] Failed to get pending count: \(error)")
        }
    }

    private func checkConnectionAndSync() async {
        guard client.isConfigured() else {
            await MainActor.run {
                syncState = .offline
            }
            return
        }

        // Try to get device status to verify connection
        do {
            _ = try await client.getStatus()
            await MainActor.run {
                syncState = .synced
            }

            // Trigger initial sync
            await performSync()
        } catch {
            await MainActor.run {
                syncState = .offline
            }
        }
    }

    private func performSync() async {
        guard client.isConfigured() else {
            await MainActor.run {
                syncState = .offline
            }
            return
        }

        await MainActor.run {
            syncState = .syncing
        }

        do {
            // Step 1: Push local changes
            try await pushChanges()

            // Step 2: Pull server changes
            try await pullChanges()

            // Step 3: Update state
            await MainActor.run {
                syncState = .synced
                lastSyncTime = Date()
            }

            updatePendingCount()

            print("[SyncEngine] Sync completed successfully")
        } catch {
            await MainActor.run {
                syncState = .error(error.localizedDescription)
            }
            print("[SyncEngine] Sync failed: \(error)")
        }
    }

    private func pushChanges() async throws {
        // Fetch pending outbox items (max 100)
        let items = try database.getPendingSyncItems(limit: 100)

        guard !items.isEmpty else {
            print("[SyncEngine] No pending items to push")
            return
        }

        print("[SyncEngine] Pushing \(items.count) items")

        // Convert to API format
        let outboxItems = items.map { item in
            SyncOutboxItem(
                entityType: item.entityType,
                entityID: item.entityID,
                operation: item.operation,
                payload: item.payload,
                createdAt: item.createdAt
            )
        }

        // Push to server
        let response = try await client.pushSync(items: outboxItems)

        print("[SyncEngine] Server accepted \(response.accepted) items, rejected \(response.rejected)")

        // The server returns IDs, not just counts, so a rejected operation cannot
        // block the rest of the outbox by being deleted accidentally.
        for acceptedID in response.acceptedIDs {
            if let item = items.first(where: { $0.entityID.uuidString.caseInsensitiveCompare(acceptedID) == .orderedSame }),
               let itemID = item.id {
                try database.markSyncItemSuccess(id: itemID)
            }
        }

        for rejectedID in response.rejectedIDs {
            if let item = items.first(where: { $0.entityID.uuidString.caseInsensitiveCompare(rejectedID) == .orderedSame }),
               let itemID = item.id {
                let reason = response.conflicts.first(where: { $0.entityID.caseInsensitiveCompare(rejectedID) == .orderedSame })?.reason
                    ?? "服务器拒绝了同步操作"
                try database.markSyncItemFailed(id: itemID, error: reason)
            }
        }

        // Handle conflicts (server wins strategy). Older servers may omit the
        // rejected_ids extension, so still mark conflict rows explicitly.
        if !response.conflicts.isEmpty {
            print("[SyncEngine] \(response.conflicts.count) conflicts detected (server wins)")
            for conflict in response.conflicts {
                if let item = items.first(where: { $0.entityID.uuidString.caseInsensitiveCompare(conflict.entityID) == .orderedSame }),
                   let itemID = item.id {
                    try database.markSyncItemFailed(id: itemID, error: conflict.reason)
                }
            }
        }
    }

    private func pullChanges() async throws {
        var cursor = lastSyncCursor
        var hasMore = true
        var totalPulled = 0

        while hasMore {
            let response = try await client.pullSync(cursor: cursor)

            if !response.entities.isEmpty {
                print("[SyncEngine] Pulled \(response.entities.count) entities")

                // Apply changes
                try applyServerOperations(response.entities)

                totalPulled += response.entities.count
            }

            // Update cursor and honor the server's pagination flag.
            if let nextCursor = response.nextCursor {
                cursor = nextCursor
                lastSyncCursor = nextCursor
                userDefaults.set(nextCursor, forKey: "last_sync_cursor")
            }
            hasMore = response.hasMore && response.nextCursor != nil

            // Safety limit: max 1000 operations per sync
            if totalPulled >= 1000 {
                print("[SyncEngine] Reached pull limit (1000 operations)")
                break
            }
        }

        if totalPulled > 0 {
            print("[SyncEngine] Applied \(totalPulled) server operations")
        }
    }

    private func applyServerOperations(_ operations: [SyncOperation]) throws {
        for operation in operations {
            do {
                // The Reader API carries the entity as a JSON object in `data`.
                let payloadData = try operation.encodedData()

                // Apply operation based on entity type
                switch operation.entityType {
                case "source":
                    try applySourceOperation(operation.operation, entityID: operation.entityID, payload: payloadData)

                case "highlight":
                    try applyHighlightOperation(operation.operation, entityID: operation.entityID, payload: payloadData)

                case "note":
                    try applyNoteOperation(operation.operation, entityID: operation.entityID, payload: payloadData)

                case "reader_item":
                    try applyReaderItemOperation(operation.operation, entityID: operation.entityID, payload: payloadData)

                default:
                    throw NetworkError.invalidPayload
                }
            } catch {
                print("[SyncEngine] Failed to apply operation \(operation.operation) for \(operation.entityType):\(operation.entityID): \(error)")
                throw error
            }
        }
    }

    private func applySourceOperation(_ operation: String, entityID: UUID, payload: Data) throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601

        switch operation {
        case "create", "update":
            let source = try decoder.decode(Source.self, from: payload)
            try database.upsertSource(source)

        case "delete":
            try database.deleteSource(id: entityID)

        default:
            throw NetworkError.invalidPayload
        }
    }

    private func applyHighlightOperation(_ operation: String, entityID: UUID, payload: Data) throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601

        switch operation {
        case "create", "update":
            let highlight = try decoder.decode(Highlight.self, from: payload)
            try database.upsertHighlight(highlight)

        case "delete":
            try database.deleteHighlight(id: entityID)

        default:
            throw NetworkError.invalidPayload
        }
    }

    private func applyNoteOperation(_ operation: String, entityID: UUID, payload: Data) throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601

        switch operation {
        case "create", "update":
            let note = try decoder.decode(Note.self, from: payload)
            try database.upsertNote(note)

        case "delete":
            try database.deleteNote(id: entityID)

        default:
            throw NetworkError.invalidPayload
        }
    }

    private func applyReaderItemOperation(_ operation: String, entityID: UUID, payload: Data) throws {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .iso8601

        switch operation {
        case "create", "update":
            let item = try decoder.decode(ReaderItem.self, from: payload)
            try database.upsertReaderItem(item)

        case "delete":
            try database.deleteReaderItem(id: entityID)

        default:
            throw NetworkError.invalidPayload
        }
    }
}
