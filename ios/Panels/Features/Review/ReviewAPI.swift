import Foundation

/// The served lifecycle determines approval gates; Review never assumes coding stages.
struct ReviewWorkerManifest: Decodable, Sendable {
    struct Stage: Decodable, Identifiable, Sendable { let id: String; let label: String }
    struct Field: Decodable, Sendable { let id: String; let label: String }
    let workerType: String?
    let stages: [Stage]
    let advance: [String: String]
    let fields: [Field]
    let ceilingRange: [String]

    init(workflow: TicketWorkerType) {
        workerType = workflow.workerType
        stages = workflow.stages.map { Stage(id: $0.id, label: $0.label) }
        advance = workflow.advance ?? [:]
        fields = workflow.fields.map { Field(id: $0.id, label: $0.label) }
        ceilingRange = workflow.ceilingRange ?? []
    }

    func approvalStages(after stage: String) -> [Stage] {
        guard let next = advance[stage], let start = stages.firstIndex(where: { $0.id == next }) else { return [] }
        return Array(stages[start...]).filter { ceilingRange.contains($0.id) }
    }
}

struct ReviewApproval: Codable, Sendable {
    let expectedProposalRevision: Int
    let editedBody: String?
    let nextCeiling: String
    let nextHolder: Principal
}

enum DecisionResponseError: Error, Equatable {
    case unsupported, invalid
}

extension APIClient {
    func reviewManifest(_ workerType: String?) async throws -> ReviewWorkerManifest {
        guard let workerType else { throw APIError(status: 0, message: "This ticket has no saved workflow.") }
        struct Response: Decodable, Sendable { let workerTypes: [ReviewWorkerManifest] }
        let response: Response = try await get("/api/worker-types/\(workerType)/manifest")
        guard let manifest = response.workerTypes.first(where: { $0.workerType == workerType }) else {
            throw APIError(status: 0, message: "This worker type is unavailable.")
        }
        return manifest
    }

    func approveReview(_ ticketID: String, field: String, approval: ReviewApproval) async throws {
        _ = try await reviewWrite("/api/tickets/\(ticketID)/accept/\(field)", body: approval)
    }

    func returnReview(_ ticketID: String, message: String, expectedProposalRevision: Int) async throws {
        struct Body: Encodable { let message: String; let expectedProposalRevision: Int }
        _ = try await reviewWrite("/api/tickets/\(ticketID)/reject", body: Body(message: message, expectedProposalRevision: expectedProposalRevision))
    }

    /// Encode once when the decision is saved. Recovery sends these exact bytes.
    static func encodeReviewDecision(_ command: DecisionCommand) throws -> Data {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(command)
    }

    func submitReviewDecision(ticketID: String, operationID: String, command: DecisionCommand,
                              frozenBody: Data) async throws -> DecisionReceipt {
        // Validate the full frozen intent, then send its original bytes unchanged.
        let frozenCommand = try Self.decoder().decode(DecisionCommand.self, from: frozenBody)
        guard frozenCommand == command else {
            throw APIError(status: 0, message: "The saved decision does not match its frozen request.")
        }
        var request = try self.request(try url(decisionOperationPath(ticketID: ticketID, operationID: operationID)))
        request.httpMethod = "POST"
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.timeoutInterval = 25
        request.httpBody = frozenBody
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        let value = try Self.decoder().decode(DecisionOperationResponse.self, from: data)
        // A successful POST always confirms a terminal outcome. Null remains unknown.
        guard let receipt = try Self.validateReviewDecisionResponse(value, ticketID: ticketID,
            operationID: operationID, command: command, allowMissingReceipt: false) else {
            throw DecisionResponseError.invalid
        }
        return receipt
    }

    func lookupReviewDecision(ticketID: String, operationID: String,
                              command: DecisionCommand) async throws -> DecisionReceipt? {
        // Receipt absence is never inferred from an ordinary response cache.
        let (data, _) = try await responseData(decisionOperationPath(ticketID: ticketID, operationID: operationID))
        let value = try Self.decoder().decode(DecisionOperationResponse.self, from: data)
        return try Self.validateReviewDecisionResponse(value, ticketID: ticketID,
            operationID: operationID, command: command, allowMissingReceipt: true)
    }

    private func decisionOperationPath(ticketID: String, operationID: String) -> String {
        "/api/tickets/\(ticketID)/decision-operations/\(operationID)"
    }

    static func validateReviewDecisionResponse(_ response: DecisionOperationResponse, ticketID: String,
        operationID: String, command: DecisionCommand, allowMissingReceipt: Bool) throws -> DecisionReceipt? {
        guard response.receiptContract == 1 else {
            throw DecisionResponseError.unsupported
        }
        guard let receipt = response.receipt else {
            if allowMissingReceipt { return nil }
            throw DecisionResponseError.invalid
        }
        guard receipt.operationId == operationID, receipt.ticketId == ticketID,
              receipt.decision == command.decision, receipt.field == command.field,
              receipt.expectedProposalRevision == command.expectedProposalRevision else {
            throw DecisionResponseError.invalid
        }
        switch receipt.outcome {
        case "applied" where receipt.transition != nil && receipt.error == nil:
            return receipt
        case "refused" where receipt.transition == nil && receipt.error != nil:
            return receipt
        default:
            throw DecisionResponseError.invalid
        }
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
        var request = try self.request(try url(path))
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
