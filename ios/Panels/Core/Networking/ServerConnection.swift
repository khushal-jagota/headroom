import Foundation
import Observation

/// Admission rules for a complete outage. A single slow or failed read stays quiet.
enum ServerConnectionTiming {
    static let unreachableDelay: TimeInterval = 8
    static let uncertainDelay: TimeInterval = 45
}

struct ServerConnectionEvidence {
    enum Failure { case unreachable, uncertain }
    private(set) var firstFailure: TimeInterval?
    private(set) var firstAttempt: TimeInterval?
    private(set) var failures = 0
    private(set) var unreachableFailures = 0
    private(set) var confirmed = false
    private(set) var continuedOffline = false
    var isCovered: Bool { confirmed && !continuedOffline }

    mutating func fail(_ kind: Failure, started: TimeInterval, now: TimeInterval) {
        if firstFailure == nil { firstFailure = now; firstAttempt = started }
        failures += 1
        if case .unreachable = kind { unreachableFailures += 1 }
        evaluate(now: now)
    }
    mutating func evaluate(now: TimeInterval) {
        if unreachableFailures >= 2, let firstFailure, now - firstFailure >= ServerConnectionTiming.unreachableDelay {
            confirmed = true
        } else if failures >= 3, let firstAttempt, now - firstAttempt >= ServerConnectionTiming.uncertainDelay {
            confirmed = true
        }
    }
    var deadline: TimeInterval? {
        guard !confirmed else { return nil }
        if unreachableFailures >= 2, let firstFailure { return firstFailure + ServerConnectionTiming.unreachableDelay }
        if failures >= 3, let firstAttempt { return firstAttempt + ServerConnectionTiming.uncertainDelay }
        return nil
    }
    mutating func answered() { self = Self() }
    mutating func continueOffline() { if confirmed { continuedOffline = true } }
    mutating func pause() { if !confirmed { self = Self() } }
}

/// One connection admission owner per configured server. Contacts from unrelated,
/// superseded, or background requests cannot change the current presentation.
@MainActor @Observable final class ServerConnection {
    struct Attempt: Sendable {
        let generation: Int
        let contact: Int
        let started: TimeInterval
    }
    private var server: String
    private var generation = 0
    private var contact = 0
    private var foreground = false
    private var evidence = ServerConnectionEvidence()
    @ObservationIgnored private var deadlineTask: Task<Void, Never>?
    @ObservationIgnored private let now: () -> TimeInterval
    var isCovered: Bool { evidence.isCovered }

    init(server: URL, now: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }) {
        self.server = LocalStore.server(server)
        self.now = now
    }
    func begin() {
        // SwiftUI can start the new run before the old run reaches its defer.
        // Do not join that run's unconfirmed failures across a foreground boundary.
        deadlineTask?.cancel(); deadlineTask = nil
        evidence.pause()
        generation += 1; foreground = true
    }
    func pause() {
        generation += 1; foreground = false
        deadlineTask?.cancel(); deadlineTask = nil
        evidence.pause()
    }
    func configure(server: URL) {
        pause(); self.server = LocalStore.server(server); evidence = ServerConnectionEvidence()
    }
    func attempt(server: URL) -> Attempt? {
        guard foreground, LocalStore.server(server) == self.server else { return nil }
        return Attempt(generation: generation, contact: contact, started: now())
    }
    func answered(_ attempt: Attempt?, response: URLResponse? = nil) {
        guard let attempt, foreground, attempt.generation == generation else { return }
        if let response {
            guard response is HTTPURLResponse, let url = response.url, LocalStore.server(url) == server else { return }
        }
        contact += 1
        deadlineTask?.cancel(); deadlineTask = nil
        evidence.answered()
    }
    func failed(_ error: any Error, attempt: Attempt?) {
        guard let attempt, foreground, attempt.generation == generation, attempt.contact == contact,
              let kind = Self.failure(error) else { return }
        evidence.fail(kind, started: attempt.started, now: now())
        deadlineTask?.cancel(); deadlineTask = nil
        guard let deadline = evidence.deadline, !evidence.isCovered else { return }
        let generation = generation
        let delay = max(0, deadline - now())
        deadlineTask = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(delay)) } catch { return }
            guard let self, self.foreground, self.generation == generation else { return }
            self.evidence.evaluate(now: self.now())
            self.deadlineTask = nil
        }
    }
    func continueOffline() { evidence.continueOffline() }

    nonisolated static func failure(_ error: any Error) -> ServerConnectionEvidence.Failure? {
        let error = error as NSError
        guard error.domain == NSURLErrorDomain else { return nil }
        switch URLError.Code(rawValue: error.code) {
        case .cannotFindHost, .dnsLookupFailed, .cannotConnectToHost, .notConnectedToInternet,
             .dataNotAllowed, .internationalRoamingOff: return .unreachable
        case .timedOut, .networkConnectionLost: return .uncertain
        default: return nil
        }
    }
}
