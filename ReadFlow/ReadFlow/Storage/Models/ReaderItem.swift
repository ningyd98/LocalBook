//
//  ReaderItem.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import GRDB

// Base item type for all reader entities
struct ReaderItem: Identifiable, Codable, FetchableRecord, PersistableRecord {
    let id: UUID
    let type: ItemType
    let sourceID: UUID?
    let sessionID: UUID?
    let parentID: UUID?
    let content: String
    let metadata: Data?
    let createdAt: Date
    let updatedAt: Date
    var syncState: SyncState

    enum ItemType: String, Codable {
        case source
        case highlight
        case note
        case question
        case answer
    }

    enum SyncState: String, Codable {
        case localOnly = "local_only"
        case pending = "pending"
        case syncing = "syncing"
        case synced = "synced"
        case conflict = "conflict"
        case failed = "failed"
    }

    init(
        id: UUID = UUID(),
        type: ItemType,
        sourceID: UUID? = nil,
        sessionID: UUID? = nil,
        parentID: UUID? = nil,
        content: String,
        metadata: Data? = nil,
        createdAt: Date = Date(),
        updatedAt: Date = Date(),
        syncState: SyncState = .localOnly
    ) {
        self.id = id
        self.type = type
        self.sourceID = sourceID
        self.sessionID = sessionID
        self.parentID = parentID
        self.content = content
        self.metadata = metadata
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.syncState = syncState
    }

    // GRDB table definition
    static let databaseTableName = "reader_items"

    enum Columns: String, ColumnExpression {
        case id, type, sourceID, sessionID, parentID
        case content, metadata, createdAt, updatedAt, syncState
    }
}
