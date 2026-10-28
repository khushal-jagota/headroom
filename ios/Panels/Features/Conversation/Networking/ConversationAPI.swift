import Foundation

extension APIClient: ConversationServing {
    func markConversationRead(_ id: String, through: Int) async throws -> Int {
        struct Body: Encodable { let throughSequence: Int }
        struct Response: Decodable { let ownerReadThroughSequence: Int }
        let data = try await reviewWrite("/api/conversation/conversations/\(id)/owner-read", body: Body(throughSequence: through))
        return try Self.decoder().decode(Response.self, from: data).ownerReadThroughSequence
    }

    func changeHeldPrompt(_ id: String, heldID: String, discard: Bool) async throws -> ConversationHeldChange {
        let path = "/api/conversation/conversations/\(id)/held-prompts/\(heldID)" + (discard ? "" : "/promote")
        var request = URLRequest(url: try url(path))
        request.httpMethod = discard ? "DELETE" : "POST"
        if !discard {
            request.httpBody = Data(#"{"mode":"steer"}"#.utf8)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(ConversationHeldChange.self, from: data)
    }
    func interruptConversation(_ id: String) async throws {
        var request = URLRequest(url: try url("/api/conversation/conversations/\(id)/interrupt"))
        request.httpMethod = "POST"
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
    }
    func conversationConfiguration(_ owner: ConversationOwner, id: String?) async throws -> ConversationConfiguration {
        try await get(id.map { "/api/conversation/conversations/\($0)" } ?? owner.path + "/start-values")
    }
    func conversationSnapshot(_ id: String, lens: ConversationLens, transcript: Bool) async throws -> ConversationSnapshot {
        try await get("/api/conversation/conversations/\(id)/view", query: [.init(name: "lens", value: lens.rawValue), .init(name: "transcript", value: String(transcript))])
    }
    func conversationReceipts(_ id: String, messageIDs: [String]) async throws -> [ConversationReceipt] {
        struct Receipts: Decodable, Sendable { let receipts: [ConversationReceipt] }
        let encoded = String(decoding: try JSONEncoder().encode(messageIDs), as: UTF8.self)
        let result: Receipts = try await get("/api/conversation/conversations/\(id)/view/receipts", query: [.init(name: "message_ids", value: encoded)])
        return result.receipts
    }
    func conversationPage(_ id: String, cursor: ConversationCursor) async throws -> ConversationPage {
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        let encoded = String(decoding: try encoder.encode(cursor), as: UTF8.self)
        return try await get("/api/conversation/conversations/\(id)/view/pages", query: [.init(name: "lens", value: cursor.lens), .init(name: "cursor", value: encoded), .init(name: "direction", value: "older")])
    }
    func conversationBody(_ id: String, reference: ConversationBodyReference) async throws -> ConversationBody {
        let text = try await conversationLiveText(id, reference: reference)
        return try Self.decoder().decode(ConversationBody.self, from: Data(text.utf8))
    }
    func conversationLiveText(_ id: String, reference: ConversationBodyReference) async throws -> String {
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
        return text
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

extension APIClient {
    // The subscription declares Full-mode live-output demand to the server.
    // Frames coalesce into a fresh coherent projection read, as the app's other
    // change streams do. Cancelling the view task also cancels this socket.
    func conversationChanges(_ id: String, lens: ConversationLens) -> AsyncThrowingStream<Void, Error> {
        AsyncThrowingStream(bufferingPolicy: .bufferingNewest(1)) { continuation in
            let task = Task {
                do {
                    var request = URLRequest(url: try url("/api/conversation/conversations/\(id)/view/tail", query: [.init(name: "lens", value: lens.rawValue), .init(name: "transcript", value: "true")]))
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    request.timeoutInterval = 120
                    let (bytes, response) = try await session.bytes(for: request)
                    try Self.validate(response)
                    for try await line in bytes.lines {
                        try Task.checkCancellation()
                        if line.hasPrefix("data:") { continuation.yield(()) }
                    }
                    continuation.finish()
                } catch { continuation.finish(throwing: error) }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
