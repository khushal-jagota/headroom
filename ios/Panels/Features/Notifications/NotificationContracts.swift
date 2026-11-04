import Foundation

/// Native push wire contract, shared by registration and owner-scoped revocation.
struct NativePushRegistration: Codable, Sendable, Equatable {
    let deviceToken: String
    let bundleId: String
    let environment: String

    enum CodingKeys: String, CodingKey {
        case deviceToken = "device_token", bundleId = "bundle_id", environment
    }
}

protocol NativePushRegistering: Sendable {
    func register(_ device: NativePushRegistration) async throws
    func unregister(_ device: NativePushRegistration) async throws
}

enum NotificationDestination: Equatable, Sendable {
    case review(ticketID: String?)
    case ticket(String)
    case orchestrator(String)
    case chief
}

struct NotificationOpen: Equatable, Sendable {
    let id: UUID
    let serverURL: URL
    let destination: NotificationDestination
}
