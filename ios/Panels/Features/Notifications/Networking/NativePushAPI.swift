import Foundation

/// Native device endpoint, deliberately separate from Web Push subscriptions. Only the
/// explicit build-time enablement in NotificationController permits this request.
struct NativePushAPI: NativePushRegistering {
    let client: APIClient
    func register(_ device: NativePushRegistration) async throws {
        try await write(device, method: "PUT")
    }
    func unregister(_ device: NativePushRegistration) async throws {
        try await write(device, method: "DELETE")
    }
    private func write(_ device: NativePushRegistration, method: String) async throws {
        var request = URLRequest(url: try client.url("/api/notifications/apns/devices"))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(device)
        request.timeoutInterval = 25
        let (data, response) = try await client.session.data(for: request)
        try APIClient.validate(response, data: data)
    }
}
