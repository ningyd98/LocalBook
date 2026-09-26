//
//  ConversationStorage.swift
//  ReadFlow
//
//  Created on 2024-09-12.
//

import Foundation
import GRDB

/// Manages conversation persistence in the database
class ConversationStorage {
    private let database: ReaderDatabase

    init(database: ReaderDatabase = .shared) {
        self.database = database
    }

    // MARK: - Conversation Management

    func createConversation(
        id: UUID = UUID(),
        highlightID: UUID,
        scope: AIScope
    ) throws -> Conversation {
        let conversation = Conversation(
            id: id,
            highlightID: highlightID,
            scope: scope.rawValue,
            createdAt: Date(),
            updatedAt: Date()
        )

        try database.write { db in
            try conversation.insert(db)
        }

        return conversation
    }

    func getConversation(id: UUID) throws -> Conversation? {
        return try database.read { db in
            try Conversation.fetchOne(db, key: id)
        }
    }

    /// Every conversation attached to one highlight, most recently touched
    /// first. Ported from the deleted `Storage/ConversationStorage.swift`,
    /// which was the only implementation offering this lookup.
    func getConversationsForHighlight(highlightID: UUID) throws -> [Conversation] {
        try database.read { db in
            try Conversation
                .filter(Conversation.Columns.highlightID == highlightID)
                .order(Conversation.Columns.updatedAt.desc)
                .fetchAll(db)
        }
    }

    func updateConversation(_ conversation: Conversation) throws {
        var updated = conversation
        updated.updatedAt = Date()

        try database.write { db in
            try updated.update(db)
        }
    }

    /// Switch the knowledge scope of an existing conversation in place.
    /// Ported from the deleted `Storage/ConversationStorage.swift`.
    ///
    /// Deliberately uses the typed GRDB API instead of the raw
    /// `UPDATE … WHERE id = ?` it was ported from: that version bound
    /// `id.uuidString` (TEXT) against a column GRDB writes as a 16-byte BLOB,
    /// so it matched zero rows and silently did nothing.
    func updateConversation(id: UUID, scope: AIScope) throws {
        try database.write { db in
            guard var conversation = try Conversation.fetchOne(db, key: id) else { return }
            conversation.scope = scope.rawValue
            conversation.updatedAt = Date()
            try conversation.update(db)
        }
    }

    func deleteConversation(id: UUID) throws {
        try database.write { db in
            _ = try Conversation.deleteOne(db, key: id)
        }
    }

    // MARK: - Message Management

    func addMessage(
        conversationID: UUID,
        role: ConversationMessage.MessageRole,
        content: String,
        citations: [Citation]? = nil,
        provider: String? = nil,
        isDegraded: Bool? = nil
    ) throws -> ConversationMessageRecord {
        // Both attribution columns are written in this single insert. Writing
        // one without the other is reachable by construction (each column is
        // independently nullable), and a half-attributed row loses information
        // it should have carried — see contract C29/C33.
        // `try?` is kept deliberately: an encode failure must not lose the
        // message itself, and `encodeIfPresent` means nil fields are *omitted*
        // rather than written as `null`. But it is no longer silent — a failure
        // here would store NULL, which reads back exactly like "no citations",
        // so it is reported on stderr (risk registry: docs/standalone-build.md §4.8).
        let encoded: Data?
        do {
            encoded = try JSONEncoder().encode(citations)
        } catch {
            FileHandle.standardError.write(Data(
                "[ConversationStorage] could not encode citations for a new message: \(error) — "
                .utf8
            ))
            FileHandle.standardError.write(Data("the column will be NULL, which reads as 'no citations'\n".utf8))
            encoded = nil
        }

        let message = ConversationMessageRecord(
            id: UUID(),
            conversationID: conversationID,
            role: role.rawValue,
            content: content,
            citationsJSON: encoded,
            createdAt: Date(),
            provider: provider,
            isDegraded: isDegraded
        )

        try database.write { db in
            try message.insert(db)
        }

        return message
    }

    func getMessages(conversationID: UUID) throws -> [ConversationMessageRecord] {
        return try database.read { db in
            try ConversationMessageRecord
                .filter(Column("conversationID") == conversationID)
                .order(Column("createdAt").asc)
                .fetchAll(db)
        }
    }

    /// Same rows as `getMessages(conversationID:)`, mapped to the UI-facing
    /// `ConversationMessage` model. Ported from the deleted
    /// `Storage/ConversationStorage.swift`, which returned domain messages
    /// directly; the row-based getter stays because the AIPopover flow needs
    /// the record (and its `citations` accessor).
    func conversationMessages(conversationID: UUID) throws -> [ConversationMessage] {
        try getMessages(conversationID: conversationID).map { $0.toConversationMessage() }
    }

    /// Drop every message of a conversation without deleting the conversation
    /// itself (used when a scope switch restarts the thread).
    func deleteMessages(conversationID: UUID) throws {
        try database.write { db in
            _ = try ConversationMessageRecord
                .filter(Column("conversationID") == conversationID)
                .deleteAll(db)
        }
    }
}

// MARK: - Conversation Model

struct Conversation: Identifiable, Codable, FetchableRecord, PersistableRecord {
    let id: UUID
    let highlightID: UUID
    /// `var` (not `let`) so a scope switch can be persisted in place through the
    /// typed GRDB API; `updatedAt` was already mutable for the same reason.
    var scope: String
    let createdAt: Date
    var updatedAt: Date

    static let databaseTableName = "conversations"

    enum Columns: String, ColumnExpression {
        case id, highlightID, scope, createdAt, updatedAt
    }
}

// MARK: - Conversation Message Record

struct ConversationMessageRecord: Identifiable, Codable, FetchableRecord, PersistableRecord {
    let id: UUID
    let conversationID: UUID
    let role: String  // user, assistant, system
    let content: String
    let citationsJSON: Data?
    let createdAt: Date
    /// Which backend produced this message (`localbook` / `cloud` / `offline`).
    ///
    /// Optional on purpose: rows written before migration `v5_conversation_attribution`
    /// have no attribution, and a non-optional `String` would either fail to
    /// decode those rows or force a sentinel — inventing attribution for
    /// historical answers (contract C29).
    let provider: String?
    /// Whether the answer was degraded. Optional for the same reason: a
    /// non-optional `Bool` would need `?? false`, which silently rewrites
    /// "unknown" into "not degraded" (contract C33).
    let isDegraded: Bool?

    static let databaseTableName = "conversation_messages"

    enum Columns: String, ColumnExpression {
        case id, conversationID, role, content, citationsJSON, createdAt, provider, isDegraded
    }

    /// The three states a `citationsJSON` column can be in.
    ///
    /// Why this exists as an explicit type: the lenient accessor below answers
    /// `nil` both for "nothing was recorded" (column NULL) and for "the bytes are
    /// there but undecodable" (corrupt). Verified consequence: replacing a row's
    /// blob with `x'DEADBEEF'` makes the read-back *identical* to a row with no
    /// citations at all — so an assertion of the form "title is nil after the
    /// round trip" passes on corrupt data. That is the same "silently looks fine"
    /// shape this project keeps removing, so a self-check must be able to tell
    /// the two apart, and a corrupt blob must fail it rather than read as nil.
    enum CitationBlobState {
        /// The column is NULL: no citations were ever recorded. Not an error.
        case absent
        /// Bytes exist and do not decode. **Not** equivalent to `absent`.
        case corrupt(Error)
        /// Bytes decode to the recorded citations.
        case decoded([Citation])
    }

    /// Tells the three states apart without swallowing the decode error.
    func citationBlobState() -> CitationBlobState {
        guard let data = citationsJSON else { return .absent }
        do {
            return .decoded(try JSONDecoder().decode([Citation].self, from: data))
        } catch {
            return .corrupt(error)
        }
    }

    /// Strict accessor: `nil` means "column is NULL", and a corrupt blob
    /// **throws** instead of masquerading as nil. Use this on any path where a
    /// corrupt value must not pass as an absence (self-checks, future repair
    /// tooling).
    func decodedCitations() throws -> [Citation]? {
        switch citationBlobState() {
        case .absent: return nil
        case .corrupt(let error): throw error
        case .decoded(let citations): return citations
        }
    }

    /// Lenient accessor, kept for existing callers.
    ///
    /// Behaviour is unchanged (a corrupt blob still reads as `nil`) — but it is
    /// no longer *silent*: corruption is reported on stderr so it can be noticed
    /// without a debugger. Risk registry entry: docs/standalone-build.md §4.8.
    var citations: [Citation]? {
        switch citationBlobState() {
        case .absent:
            return nil
        case .corrupt(let error):
            FileHandle.standardError.write(Data(
                ("[ConversationMessageRecord] corrupt citationsJSON on row \(id.uuidString): "
                 + "\(error) — reading as nil, which is NOT the same as 'no citations'\n").utf8
            ))
            return nil
        case .decoded(let citations):
            return citations
        }
    }

    func toConversationMessage() -> ConversationMessage {
        let messageRole: ConversationMessage.MessageRole = {
            switch role {
            case "user": return .user
            case "assistant": return .assistant
            default: return .system
            }
        }()

        return ConversationMessage(
            id: id,
            role: messageRole,
            content: content,
            timestamp: createdAt,
            citations: citations,
            // Attribution is read back verbatim, including `nil`. Rows written
            // before `v5_conversation_attribution` have both columns NULL, and
            // that "unknown" must survive the mapping — inventing a producer here
            // is the historical-rewriting failure C29 exists to prevent.
            provider: provider,
            isDegraded: isDegraded
        )
    }
}

// MARK: - Message Role Extension

extension ConversationMessage.MessageRole {
    var rawValue: String {
        switch self {
        case .user: return "user"
        case .assistant: return "assistant"
        case .system: return "system"
        }
    }
}
