//
//  Highlight.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import GRDB

struct Highlight: Identifiable, Codable, FetchableRecord, PersistableRecord {
    let id: UUID
    let sourceID: UUID
    let sessionID: UUID?
    let selectedText: String
    let contextBefore: String
    let contextAfter: String
    let page: Int?
    let location: String?
    let createdAt: Date
    var syncState: SyncState

    enum SyncState: String, Codable {
        case localOnly = "local_only"
        case pending = "pending"
        case synced = "synced"
    }

    init(
        id: UUID = UUID(),
        sourceID: UUID,
        sessionID: UUID? = nil,
        selectedText: String,
        contextBefore: String = "",
        contextAfter: String = "",
        page: Int? = nil,
        location: String? = nil,
        createdAt: Date = Date(),
        syncState: SyncState = .localOnly
    ) {
        self.id = id
        self.sourceID = sourceID
        self.sessionID = sessionID
        self.selectedText = selectedText
        self.contextBefore = contextBefore
        self.contextAfter = contextAfter
        self.page = page
        self.location = location
        self.createdAt = createdAt
        self.syncState = syncState
    }

    // GRDB table definition
    static let databaseTableName = "highlights"

    enum Columns: String, ColumnExpression {
        case id, sourceID, sessionID, selectedText
        case contextBefore, contextAfter, page, location
        case createdAt, syncState
    }
}
