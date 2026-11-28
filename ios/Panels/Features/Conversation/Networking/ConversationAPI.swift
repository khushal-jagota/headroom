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
        var request = try self.request(try url(path))
        request.httpMethod = discard ? "DELETE" : "POST"
        if !discard {
            request.httpBody = Data(#"{"mode":"steer"}"#.utf8)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await self.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(ConversationHeldChange.self, from: data)
    }
    func interruptConversation(_ id: String) async throws {
        var request = try self.request(try url("/api/conversation/conversations/\(id)/interrupt"))
        request.httpMethod = "POST"
        let (data, response) = try await self.data(for: request)
        try Self.validate(response, data: data)
    }
    func conversationConfiguration(_ owner: ConversationOwner, id: String?) async throws -> ConversationConfiguration {
        guard let path = owner.path else { throw APIError(status: 410, message: "Goal conversations are retired. Open the goal to talk to its holder.") }
        return try await get(id.map { "/api/conversation/conversations/\($0)" } ?? path + "/start-values")
    }
    func conversationSnapshot(_ id: String, lens: ConversationLens, transcript: Bool) async throws -> ConversationSnapshot {
        let (data, _) = try await responseData("/api/conversation/conversations/\(id)/view", query: [.init(name: "lens", value: lens.rawValue), .init(name: "transcript", value: String(transcript))])
        return try Self.decoder().decode(ConversationSnapshot.self, from: data)
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
    func conversationScope(_ id: String, lens: ConversationLens, scope: ConversationCursor.Scope) async throws -> ConversationPage {
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        let encoded = String(decoding: try encoder.encode(scope), as: UTF8.self)
        return try await get("/api/conversation/conversations/\(id)/view/pages", query: [.init(name: "lens", value: lens.rawValue), .init(name: "scope", value: encoded)])
    }
    func conversationToolDetail(_ id: String, sequence: Int) async throws -> String? {
        struct Detail: Decodable { let detail: String? }
        let result: Detail = try await get("/api/conversation/conversations/\(id)/events/\(sequence)/detail")
        guard (result.detail?.utf8.count ?? 0) <= ConversationRetention.oversizedBodyLimit else {
            throw APIError(status: 0, message: "Tool output is too large. Open it on the web.")
        }
        return result.detail
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
        guard let path = owner.path else { throw APIError(status: 410, message: "Goal conversations are retired. Open the goal to talk to its holder.") }
        var request = try self.request(try url(path + "/send"))
        request.httpMethod = "POST"
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
        request.httpBody = try encoder.encode(message)
        let (data, response) = try await self.data(for: request)
        do { try Self.validate(response, data: data) }
        catch let error as APIError where error.status == 422 && message.content.contains(where: { $0.piece == "image" }) {
            // FastAPI admission errors use `detail`; other requests keep ordinary error handling.
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            let reason = detail.map { " " + $0 + "." } ?? ""
            throw APIError(status: 422, message: "The server could not accept these photos." + reason + " Your draft is kept. Remove a photo or choose another photo, then try again.")
        }
        return try Self.decoder().decode(ConversationDelivery.self, from: data)
    }
}

extension APIClient {
    // The subscription declares Full-mode live-output demand to the server. Its frames
    // carry the window and its changes, decoded off the main actor; nothing is
    // dropped, because a missing change frame is a gap. Cancelling the view task also
    // ends the subscription.
    /// The subscription's parameters. Supplying retained ranges requires an admitted cursor.
    /// Without `after`, the server intentionally starts with a new timeline-only snapshot.
    func conversationTailParams(lens: ConversationLens, transcript: Bool, ranges: [ConversationRetainedRange], after: Int?) throws -> [String: String] {
        var params = ["lens": lens.rawValue, "transcript": String(transcript)]
        if let after { params["after"] = String(after) }
        if !ranges.isEmpty {
            let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
            params["ranges"] = String(decoding: try encoder.encode(ranges), as: UTF8.self)
        }
        return params
    }
    func conversationTail(_ id: String, lens: ConversationLens, transcript: Bool = true, ranges: [ConversationRetainedRange] = [], after: Int? = nil) -> AsyncThrowingStream<ConversationTailFrame, Error> {
        let params: [String: String]
        do { params = try conversationTailParams(lens: lens, transcript: transcript, ranges: ranges, after: after) }
        catch { return AsyncThrowingStream { $0.finish(throwing: error) } }
        return liveEvents(kind: "conversation_view", conversationID: id, params: params) { try ConversationTailFrame($0) }
    }
}

extension APIClient {
    /// Sends one ask answer under its operation ID. Returns the fate: applied, saved or refused.
    func answerAsk(_ answer: AskAnswer, operationID: String) async throws -> String {
        let path: String
        var body: [String: Any] = ["operation_id": operationID]
        switch answer.request {
        case .permission(let askID, let optionID):
            path = "permission-answers"; body["ask_id"] = askID; body["option_id"] = optionID
        case .userInput(let requestID):
            path = "user-input-answers"; body["request_id"] = requestID
            // Question IDs are sent exactly as the server named them.
            body["answers"] = Dictionary(answer.questions.map { ($0.id, ["answers": $0.answers]) }, uniquingKeysWith: { _, last in last })
        }
        var request = try self.request(try url("/api/conversation/conversations/\(answer.conversationID)/\(path)"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 25
        request.httpBody = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        let (data, response) = try await self.data(for: request)
        try Self.validate(response, data: data)
        struct Response: Decodable { let fate: String }
        return try Self.decoder().decode(Response.self, from: data).fate
    }
    /// The saved fate of an answer, or nil when the server saved none.
    func askAnswerReceipt(conversationID: String, operationID: String) async throws -> String? {
        struct Receipt: Decodable { let operationId: String; let fate: String }
        struct Response: Decodable { let receipt: Receipt? }
        let (data, _) = try await responseData("/api/conversation/conversations/\(conversationID)/answer-receipts/\(operationID)")
        let value = try Self.decoder().decode(Response.self, from: data)
        guard let receipt = value.receipt else { return nil }
        guard receipt.operationId == operationID else { throw APIError(status: 0, message: "The answer receipt names another answer.") }
        return receipt.fate
    }
}
