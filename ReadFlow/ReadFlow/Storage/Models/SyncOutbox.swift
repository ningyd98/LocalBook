//
//  SyncOutbox.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import GRDB

struct SyncOutbox: Identifiable, Codable, FetchableRecord, PersistableRecord {
    var id: Int64?
    let entityType: String
    let entityID: UUID
    let operation: String  // create/update/delete
    let payload: Data  // JSON
    let createdAt: Date
    var retryCount: Int
    var lastAttemptAt: Date?
    var lastError: String?

    enum Operation: String, Codable {
        case create
        case update
        case delete
    }

    init(
        id: Int64? = nil,
        entityType: String,
        entityID: UUID,
        operation: String,
        payload: Data,
        createdAt: Date = Date(),
        retryCount: Int = 0,
        lastAttemptAt: Date? = nil,
        lastError: String? = nil
    ) {
        self.id = id
        self.entityType = entityType
        self.entityID = entityID
        self.operation = operation
        self.payload = payload
        self.createdAt = createdAt
        self.retryCount = retryCount
        self.lastAttemptAt = lastAttemptAt
        self.lastError = lastError
    }

    // GRDB table definition
    static let databaseTableName = "sync_outbox"

    enum Columns: String, ColumnExpression {
        case id, entityType, entityID, operation, payload
        case createdAt, retryCount, lastAttemptAt, lastError
    }

    mutating func didInsert(_ inserted: InsertionSuccess) {
        id = inserted.rowID
    }
}
