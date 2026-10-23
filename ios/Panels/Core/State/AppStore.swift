import Foundation
import Observation

@MainActor @Observable final class AppStore {
    var baseURL: URL
    var client: APIClient { APIClient(baseURL: baseURL) }
    var managers: [ManagerSummary] = []
    var tickets: [TicketSummary] = []
    var chief: ChiefDetail?
    var review: ReviewResponse?
    var deployment: DeploymentStatus?
    var managerPage: PageFacts?
    var ticketPage: PageFacts?
    var errors: [String: String] = [:]
    var isLoading = false
    var connection = "Connecting"
    var revision = 0
    private var refreshing = false
    private var loadingMore = false

    init() {
#if DEBUG
        if let at = ProcessInfo.processInfo.arguments.firstIndex(of: "--conversation-stub"), ProcessInfo.processInfo.arguments.indices.contains(at + 1),
           let local = URL(string: ProcessInfo.processInfo.arguments[at + 1]), ["localhost", "127.0.0.1"].contains(local.host) {
            baseURL = local
            return
        }
#endif
#if DEBUG
        if ProcessInfo.processInfo.arguments.contains("--conversation-smoke") {
            baseURL = URL(string: "http://127.0.0.1:1")!
            return
        }
#endif
        let saved = UserDefaults.standard.string(forKey: "panels.baseURL")
        baseURL = saved.flatMap(URL.init(string:)) ?? APIClient.defaultBaseURL
    }

    func configure(baseURL: URL) {
        self.baseURL = baseURL
        UserDefaults.standard.set(baseURL.absoluteString, forKey: "panels.baseURL")
        managers = []; tickets = []; chief = nil; review = nil; deployment = nil
        managerPage = nil; ticketPage = nil; errors = [:]
    }

    /// Owned by the foreground shell task. SwiftUI cancels it on background or URL change.
    func run() async {
        await refresh()
        while !Task.isCancelled {
            do {
                connection = "Following changes"
                for try await _ in client.changes() {
                    try Task.checkCancellation()
                    await refresh()
                    try await Task.sleep(for: .milliseconds(300))
                }
            } catch {
                if Task.isCancelled { return }
            }
            if Task.isCancelled { return }
            connection = "Reconnecting"
            do { try await Task.sleep(for: .seconds(3)) } catch { return }
            await refresh()
        }
    }

    func refresh() async {
        // A refresh requested during pagination or another read must not be dropped.
        // Waiting remains cancellable when the shell leaves the foreground.
        while refreshing || loadingMore {
            do { try await Task.sleep(for: .milliseconds(50)) } catch { return }
        }
        guard !Task.isCancelled else { return }
        refreshing = true
        isLoading = managers.isEmpty && tickets.isEmpty && chief == nil
        defer { refreshing = false; isLoading = false }
        let api = client
        let managerOffset = managerPage?.offset ?? 0
        let ticketOffset = ticketPage?.offset ?? 0
        async let managerRead: () = read("Workspace", from: api) { try await api.managerWindow(through: managerOffset) } apply: { self.managers = $0.managers; self.managerPage = $0.page }
        async let ticketRead: () = read("Tickets", from: api) { try await api.ticketWindow(through: ticketOffset) } apply: { self.tickets = $0.tickets; self.ticketPage = $0.page }
        async let chiefRead: () = read("Chief", from: api) { try await api.chief() } apply: { self.chief = $0 }
        async let reviewRead: () = read("Review", from: api) { try await api.review() } apply: { self.review = $0 }
        async let deployRead: () = read("Deployment", from: api) { try await api.deployment() } apply: { self.deployment = $0 }
        _ = await (managerRead, ticketRead, chiefRead, reviewRead, deployRead)
        guard !Task.isCancelled, api.baseURL == baseURL else { return }
        revision += 1
    }

    private func read<T: Sendable>(_ key: String, from api: APIClient, fetch: @Sendable () async throws -> T, apply: @MainActor @Sendable (T) -> Void) async {
        do {
            let value = try await fetch()
            guard !Task.isCancelled, api.baseURL == baseURL else { return }
            apply(value); errors[key] = nil
        } catch {
            guard !Task.isCancelled, api.baseURL == baseURL else { return }
            errors[key] = error.localizedDescription
        }
    }

    func loadMore() async {
        guard !loadingMore, !refreshing else { return }
        loadingMore = true
        defer { loadingMore = false }
        let api = client
        if let offset = managerPage?.nextOffset {
            await read("Workspace", from: api) { try await api.managers(offset: offset) } apply: { page in
                let existing = Set(self.managers.map(\.id))
                self.managers += page.managers.filter { !existing.contains($0.id) }; self.managerPage = page.page
            }
        }
        if let offset = ticketPage?.nextOffset {
            await read("Tickets", from: api) { try await api.tickets(offset: offset) } apply: { page in
                let existing = Set(self.tickets.map(\.id))
                self.tickets += page.tickets.filter { !existing.contains($0.id) }; self.ticketPage = page.page
            }
        }
    }
}
