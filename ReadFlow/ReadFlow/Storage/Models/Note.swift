//
//  Note.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import GRDB

struct Note: Identifiable, Codable, FetchableRecord, PersistableRecord {
    let id: UUID
    let sourceID: UUID
    let highlightID: UUID?
    let sessionID: UUID?
    var content: String
    let createdAt: Date
    var updatedAt: Date
    var revision: Int
    var syncState: SyncState

    enum SyncState: String, Codable {
        case localOnly = "local_only"
        case pending = "pending"
        case synced = "synced"
    }

    init(
        id: UUID = UUID(),
        sourceID: UUID,
        highlightID: UUID? = nil,
        sessionID: UUID? = nil,
        content: String,
        createdAt: Date = Date(),
        updatedAt: Date = Date(),
        revision: Int = 1,
        syncState: SyncState = .localOnly
    ) {
        self.id = id
        self.sourceID = sourceID
        self.highlightID = highlightID
        self.sessionID = sessionID
        self.content = content
        self.createdAt = createdAt
        self.updatedAt = updatedAt
        self.revision = revision
        self.syncState = syncState
    }

    mutating func update(content: String) {
        self.content = content
        self.updatedAt = Date()
        self.revision += 1
        self.syncState = .pending
    }

    // GRDB table definition
    static let databaseTableName = "notes"

    enum Columns: String, ColumnExpression {
        case id, sourceID, sessionID, highlightID
        case content, createdAt, updatedAt, revision, syncState
    }
}
