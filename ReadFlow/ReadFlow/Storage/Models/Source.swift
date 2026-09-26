//
//  Source.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import GRDB

struct Source: Identifiable, Codable, FetchableRecord, PersistableRecord {
    let id: UUID
    let type: SourceType
    let title: String
    let url: String?
    let canonicalURL: String?
    let filePath: String?
    let fileHash: String?
    let createdAt: Date
    var lastReadAt: Date?
    let metadata: SourceMetadata?

    enum SourceType: String, Codable {
        case web
        case pdf
        case document
        case markdown
        case app
    }

    init(
        id: UUID = UUID(),
        type: SourceType,
        title: String,
        url: String? = nil,
        canonicalURL: String? = nil,
        filePath: String? = nil,
        fileHash: String? = nil,
        createdAt: Date = Date(),
        lastReadAt: Date? = nil,
        metadata: SourceMetadata? = nil
    ) {
        self.id = id
        self.type = type
        self.title = title
        self.url = url
        self.canonicalURL = canonicalURL
        self.filePath = filePath
        self.fileHash = fileHash
        self.createdAt = createdAt
        self.lastReadAt = lastReadAt
        self.metadata = metadata
    }

    // GRDB table definition
    static let databaseTableName = "sources"

    enum Columns: String, ColumnExpression {
        case id, type, title, url, canonicalURL
        case filePath, fileHash, createdAt, lastReadAt, metadata
    }
}

struct SourceMetadata: Codable {
    let author: String?
    let publishedDate: Date?
    let domain: String?
    let favicon: String?
    let pageCount: Int?
    let fileSize: Int64?
    /// Bundle id of the app a capture came from (`CapturedSource.appBundleID`).
    /// Optional and additive, so metadata written by an earlier build still decodes.
    let appBundleID: String?
    /// Localised name of the app a capture came from (`CapturedSource.appName`).
    let appName: String?

    init(
        author: String? = nil,
        publishedDate: Date? = nil,
        domain: String? = nil,
        favicon: String? = nil,
        pageCount: Int? = nil,
        fileSize: Int64? = nil,
        appBundleID: String? = nil,
        appName: String? = nil
    ) {
        self.author = author
        self.publishedDate = publishedDate
        self.domain = domain
        self.favicon = favicon
        self.pageCount = pageCount
        self.fileSize = fileSize
        self.appBundleID = appBundleID
        self.appName = appName
    }
}
