import Foundation

/// A validated download, retained by both its thumbnail and full-screen viewer.
/// Its temporary file disappears when neither presentation needs it.
final class ConversationMediaFile: Sendable {
    let url: URL
    init(url: URL) { self.url = url }
    deinit { try? FileManager.default.removeItem(at: url) }
}

extension APIClient {
    func conversationFileURL(_ conversationID: String, storedFileID: String) throws -> URL {
        guard var components = URLComponents(url: try url("/"), resolvingAgainstBaseURL: false) else { throw URLError(.badURL) }
        let segment = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        guard let conversation = conversationID.addingPercentEncoding(withAllowedCharacters: segment),
              let file = storedFileID.addingPercentEncoding(withAllowedCharacters: segment) else { throw URLError(.badURL) }
        components.percentEncodedPath = "/api/conversation/conversations/\(conversation)/files/\(file)"
        guard let result = components.url else { throw URLError(.badURL) }
        return result
    }

    func downloadConversationFile(_ conversationID: String, storedFileID: String, fileName: String?) async throws -> ConversationMediaFile {
        var request = try self.request(try conversationFileURL(conversationID, storedFileID: storedFileID))
        request.timeoutInterval = 60
        let (download, response) = try await self.download(for: request)
        do {
            // Error bodies contain server validation notes, never a media payload.
            let errorData = (response as? HTTPURLResponse).map { (200..<300).contains($0.statusCode) } == true ? Data() : (try Data(contentsOf: download))
            try Self.validate(response, data: errorData)
            try Task.checkCancellation()
            let ext = fileName.map { ($0 as NSString).pathExtension }.flatMap { $0.isEmpty ? nil : $0 } ?? "data"
            let destination = FileManager.default.temporaryDirectory.appendingPathComponent("panels-conversation-\(UUID().uuidString)").appendingPathExtension(ext)
            try FileManager.default.moveItem(at: download, to: destination)
            return ConversationMediaFile(url: destination)
        } catch {
            try? FileManager.default.removeItem(at: download)
            throw error
        }
    }
}
