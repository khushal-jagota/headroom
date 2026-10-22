import Foundation

struct RecapDelivery: Decodable, Sendable {
    let fate: String
    let refusalReason: String?
    var accepted: Bool { ["started", "queued", "injected"].contains(fate) }
    var note: String? {
        switch fate {
        case "started", "queued", "injected": nil
        case "refused": "Not delivered · \(refusalReason?.replacingOccurrences(of: "_", with: " ") ?? "request refused")"
        default: "Delivery uncertain · do not resend"
        }
    }
}

extension APIClient {
    func requestRecap(target: Principal) async throws -> RecapDelivery {
        struct Body: Encodable { let target: Principal }
        var request = URLRequest(url: try url("/api/recap-requests"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(Body(target: target))
        request.timeoutInterval = 25
        let (data, response) = try await session.data(for: request)
        try Self.validate(response, data: data)
        return try Self.decoder().decode(RecapDelivery.self, from: data)
    }
}
