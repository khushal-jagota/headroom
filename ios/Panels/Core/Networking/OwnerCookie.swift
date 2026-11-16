import Foundation

/// The server proves the owner with an HttpOnly cookie it sets when the web app page
/// loads. The app loads that page once through the same request factory and lets
/// URLSession keep the cookie; no pairing and no secret in the build.
actor OwnerCookieBootstrap {
    static let cookieName = "panels_owner"
    static let shared = OwnerCookieBootstrap()
    private var inFlight: (origin: String, task: Task<Void, Never>)?

    /// Callers for the same server await one load. A failed load is dropped, so the
    /// next request tries again.
    func ensure(_ client: APIClient, storage: HTTPCookieStorage) async {
        guard let page = try? client.url("/"), !Self.holdsCookie(storage, for: page) else { return }
        let origin = LocalStore.server(client.baseURL)
        if let inFlight, inFlight.origin == origin { return await inFlight.task.value }
        let task = Task {
            guard let request = try? client.request(page) else { return }
            _ = try? await client.session.data(for: request)
        }
        inFlight = (origin, task)
        await task.value
        if inFlight?.origin == origin { inFlight = nil }
    }

    private static func holdsCookie(_ storage: HTTPCookieStorage, for url: URL) -> Bool {
        storage.cookies(for: url)?.contains { $0.name == cookieName } == true
    }
}

extension APIClient {
    /// The app's shared session is the one whose cookies carry the owner. Streams use
    /// the same session, so one cookie covers reads, writes and streams.
    func ensureOwnerCookie() async {
        guard session === URLSession.shared else { return }
        await OwnerCookieBootstrap.shared.ensure(self, storage: .shared)
    }
}
