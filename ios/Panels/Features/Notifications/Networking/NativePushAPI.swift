import Foundation

/// Proposed endpoint, deliberately separate from Web Push subscriptions. Only the
/// explicit build-time enablement in NotificationController permits this request.
struct NativePushAPI: NativePushRegistering {
    let client: APIClient
    func register(_ device: NativePushRegistration) async throws {
        var request = URLRequest(url: try client.url("/api/notifications/apns/devices"))
        request.httpMethod = "PUT"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(device)
        request.timeoutInterval = 25
        let (data, response) = try await client.session.data(for: request)
        try APIClient.validate(response, data: data)
    }
}
