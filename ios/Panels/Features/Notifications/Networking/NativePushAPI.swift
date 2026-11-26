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
    /// Tell the server this device's Live Activity tokens. The device must be registered first.
    func reportLiveActivity(_ device: NativePushRegistration, _ tokens: LiveActivityTokens) async throws {
        var request = try client.request(try client.url("/api/notifications/apns/live-activity"))
        request.httpMethod = "PUT"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body = try JSONEncoder().encode(device)
        var fields = try JSONSerialization.jsonObject(with: body) as? [String: Any] ?? [:]
        let extra = try JSONSerialization.jsonObject(with: JSONEncoder().encode(tokens)) as? [String: Any] ?? [:]
        fields.merge(extra) { _, new in new }
        request.httpBody = try JSONSerialization.data(withJSONObject: fields)
        request.timeoutInterval = 25
        let (data, response) = try await client.data(for: request)
        try APIClient.validate(response, data: data)
    }

    private func write(_ device: NativePushRegistration, method: String) async throws {
        var request = try client.request(try client.url("/api/notifications/apns/devices"))
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(device)
        request.timeoutInterval = 25
        let (data, response) = try await client.data(for: request)
        try APIClient.validate(response, data: data)
    }
}
