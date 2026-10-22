import Foundation

extension APIClient: FeedbackServing {
    func feedback() async throws -> FeedbackResponse { try await get("/api/feedback") }

    func updateFeedback(_ id: String, action: FeedbackAction) async throws -> FeedbackNote {
        // Identifiers are a single path component, never a route supplied by a note.
        let endpoint = try url("/api/feedback").appending(component: id).appending(component: action.rawValue)
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.timeoutInterval = 25
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(FeedbackNote.self, from: data)
    }
}
