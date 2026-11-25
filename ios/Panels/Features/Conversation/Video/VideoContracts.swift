import Foundation

/// Bytes stay in the phone store. Drafts and outgoing work contain only this reference.
struct SavedVideo: Codable, Equatable, Sendable {
    let id: String
    let fileName: String
    /// Relative to LocalStore.directory, whose container prefix can change on update.
    let filePath: String
    let byteCount: Int64
    var preparationError: String? = nil
    var fileURL: URL { fileURL(in: LocalStore.directory) }
    func fileURL(in directory: URL) -> URL { directory.appendingPathComponent(filePath) }
}

struct VideoUploadDestination: Codable, Equatable, Sendable {
    let kind: String
    let id: String
    let expectedConversationId: String?

    enum CodingKeys: String, CodingKey { case kind, id, expectedConversationId }
    func encode(to encoder: any Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(kind, forKey: .kind)
        try values.encode(id, forKey: .id)
        try values.encode(expectedConversationId, forKey: .expectedConversationId)
    }
}

struct VideoUploadStatus: Codable, Sendable {
    let uploadId: String
    let destination: VideoUploadDestination
    let filename: String
    let byteCount: Int64
    let receivedBytes: Int64
    let state: String
    let mediaType: String?
    let expiresAt: Double
    let error: String?
}

enum VideoAttachmentPolicy {
    static let maximumBytes: Int64 = 250 * 1024 * 1024
}
