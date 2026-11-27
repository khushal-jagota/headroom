import Foundation

/// The device's Review Live Activity tokens, as the server needs them. The server starts the
/// activity from the push-to-start token and updates it through the update token.
struct LiveActivityTokens: Equatable, Sendable {
    enum Update: Equatable, Sendable { case unknown, token(String), ended }
    var pushToStart: String?
    var update: Update = .unknown
}

extension LiveActivityTokens: Encodable {
    /// A field left out keeps the server's value. Only an ended activity sends null, so a
    /// token that has not arrived yet can never clear a live one.
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        if let pushToStart { try container.encode(pushToStart, forKey: .pushToStart) }
        switch update {
        case .unknown: break
        case .token(let token): try container.encode(token, forKey: .update)
        case .ended: try container.encodeNil(forKey: .update)
        }
    }
    private enum CodingKeys: String, CodingKey { case pushToStart = "push_to_start_token", update = "update_token" }
}
