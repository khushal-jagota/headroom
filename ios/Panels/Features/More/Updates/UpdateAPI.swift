import Foundation
import Observation

enum UpdateResources {
    static let status = ResourceRegistration<PanelsUpdateStatus>(key: "/api/updates", versioned: false, signalRefreshInterval: ResourceLimits.fallbackSeconds)
}

enum PanelsUpdateAction: Sendable {
    case install
    case retryServer
    case retryMachine(PanelsUpdateStatus.Machine)
}

extension APIClient {
    func performUpdate(_ action: PanelsUpdateAction, status: PanelsUpdateStatus) async throws {
        let path: String
        var body: Data?
        let pathCharacters = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-._~"))
        let segment: (String) -> String = { $0.addingPercentEncoding(withAllowedCharacters: pathCharacters) ?? $0 }
        switch action {
        case .install:
            guard status.allowedActions.contains("update"), let offer = status.available else {
                throw APIError(status: 409, message: "This update is no longer available.")
            }
            path = "/api/updates/install"
            body = try JSONSerialization.data(withJSONObject: ["app_sha": offer.appSha])
        case .retryServer:
            guard status.allowedActions.contains("retry"), let attempt = status.attempt else {
                throw APIError(status: 409, message: "This update changed. Read the status and try again.")
            }
            path = "/api/updates/attempts/\(segment(attempt.attemptId))/retry"
        case .retryMachine(let machine):
            guard machine.canRetry, let attempt = machine.attemptId else {
                throw APIError(status: 409, message: "This machine update is no longer available to retry.")
            }
            path = "/api/updates/attempts/\(segment(attempt))/machines/\(segment(machine.machineId))/retry"
        }
        var target = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
        target.percentEncodedPath = path
        target.query = nil
        target.fragment = nil
        guard let targetURL = target.url else { throw URLError(.badURL) }
        var request = try self.request(targetURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 25
        request.setValue(status.appSha, forHTTPHeaderField: "X-Panels-App-SHA")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        let (data, response) = try await self.data(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let detail = object["detail"] as? String {
            throw APIError(status: http.statusCode, message: detail)
        }
        try Self.validate(response, data: data)
    }
}

/// Update actions never enter the message outbox and never repeat themselves.
@MainActor @Observable final class UpdateActionsState {
    private(set) var inFlight = false
    private(set) var error: String?
    private var generation = UUID()
    private var startingKey: String?
    private var startingUntil = Date.distantPast

    func reset() {
        generation = UUID()
        inFlight = false
        error = nil
        startingKey = nil
        startingUntil = .distantPast
    }
    func starting(_ status: PanelsUpdateStatus?, now: Date = .now) -> Bool {
        guard let status else { return false }
        return startingKey == status.answerKey && now < startingUntil
    }
    func busy(_ status: PanelsUpdateStatus?, now: Date = .now) -> Bool { inFlight || starting(status, now: now) }

    func perform(_ action: PanelsUpdateAction, status: PanelsUpdateStatus, api: APIClient, refresh: @MainActor () async -> Void) async {
        guard !busy(status) else { return }
        let operation = generation
        inFlight = true
        error = nil
        switch action {
        case .install, .retryServer:
            startingKey = status.answerKey
            startingUntil = .now.addingTimeInterval(30)
        case .retryMachine: break
        }
        do {
            try await api.performUpdate(action, status: status)
        } catch {
            guard generation == operation else { return }
            self.error = error.localizedDescription
            startingKey = nil
        }
        guard generation == operation else { return }
        await refresh()
        guard generation == operation else { return }
        inFlight = false
    }
}


/// Update records live outside the database, so foreground reads also use a timer.
/// The shell owns this task and cancels it when its foreground run ends.
@MainActor enum UpdateStatusRefresh {
    static func follow(current: () -> PanelsUpdateStatus?, refresh: () async -> Void,
                       wait: (TimeInterval) async throws -> Void = { try await Task.sleep(for: .seconds($0)) }) async {
        while !Task.isCancelled {
            let status = current()
            let active = status?.state == "updating" || status?.machines.contains { $0.state == "updating" } == true
            do { try await wait(active ? 3 : 30) } catch { return }
            guard !Task.isCancelled else { return }
            await refresh()
        }
    }
}
