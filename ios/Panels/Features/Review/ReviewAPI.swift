import Foundation

struct ReviewApproval: Codable, Sendable {
    let expectedProposalRevision: Int
    let editedBody: String?
    let nextCeiling: String
    let nextHolder: Principal
}

extension APIClient {
    func approveReview(_ ticketID: String, field: String, approval: ReviewApproval) async throws {
        _ = try await reviewWrite("/api/tickets/\(ticketID)/accept/\(field)", body: approval)
    }

    func returnReview(_ ticketID: String, message: String, expectedProposalRevision: Int) async throws {
        struct Body: Encodable { let message: String; let expectedProposalRevision: Int }
        _ = try await reviewWrite("/api/tickets/\(ticketID)/reject", body: Body(message: message, expectedProposalRevision: expectedProposalRevision))
    }

    func transcribeReviewAudio(_ audio: Data) async throws -> String {
        struct Body: Encodable { let audio: String; let mediaType: String }
        struct Response: Decodable { let transcript: String }
        guard audio.count <= 25 * 1024 * 1024 else { throw APIError(status: 0, message: "The recording is too large.") }
        let data = try await reviewWrite("/api/conversation/voice-transcriptions", body: Body(audio: audio.base64EncodedString(), mediaType: "audio/mp4"))
        return try Self.decoder().decode(Response.self, from: data).transcript
    }

    func renameReviewTicket(_ id: String, title: String) async throws {
        struct Body: Encodable { let title: String }
        _ = try await reviewWrite("/api/tickets/\(id)", method: "PATCH", body: Body(title: title))
    }

    func reviewWrite<T: Encodable>(_ path: String, method: String = "POST", body: T) async throws -> Data {
        var request = URLRequest(url: try url(path))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 25
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        request.httpBody = try encoder.encode(body)
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return data
    }
}
