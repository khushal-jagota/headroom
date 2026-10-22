import Foundation

extension APIClient: ConversationServing {
    func conversationSnapshot(_ id: String, lens: ConversationLens, transcript: Bool) async throws -> ConversationSnapshot {
        try await get("/api/conversation/conversations/\(id)/view", query: [.init(name: "lens", value: lens.rawValue), .init(name: "transcript", value: String(transcript))])
    }
    func conversationPage(_ id: String, cursor: ConversationCursor) async throws -> ConversationPage {
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        let encoded = String(decoding: try encoder.encode(cursor), as: UTF8.self)
        return try await get("/api/conversation/conversations/\(id)/view/pages", query: [.init(name: "lens", value: cursor.lens), .init(name: "cursor", value: encoded), .init(name: "direction", value: "older")])
    }
    func conversationBody(_ id: String, reference: ConversationBodyReference) async throws -> ConversationBody {
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        let encoded = String(decoding: try encoder.encode(reference), as: UTF8.self)
        var text = ""
        var offset = 0
        repeat {
            let chunk: ConversationBodyChunk = try await get("/api/conversation/conversations/\(id)/view/body", query: [.init(name: "reference", value: encoded), .init(name: "offset", value: String(offset))])
            text += chunk.text
            guard let next = chunk.nextOffset else { break }
            guard next > offset, text.utf8.count < 2_000_000 else { throw APIError(status: 0, message: "Message is too large. Open it on the web.") }
            offset = next
        } while true
        return try Self.decoder().decode(ConversationBody.self, from: Data(text.utf8))
    }
    func sendConversation(_ owner: ConversationOwner, message: ConversationSend) async throws -> ConversationDelivery {
        var request = URLRequest(url: try url(owner.path + "/send"))
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        request.httpBody = try encoder.encode(message)
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(ConversationDelivery.self, from: data)
    }
}
