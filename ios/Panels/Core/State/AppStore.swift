import Foundation
import Observation

@MainActor @Observable final class AppStore {
    var baseURL: URL
    private let session: URLSession
    var client: APIClient { APIClient(baseURL: baseURL, session: session) }
    /// Workspace rows from the last view that carried the rail windows. A scope
    /// without them, such as Review alone, keeps the last coherent Workspace.
    var managers: [ManagerSummary] = []
    var tickets: [TicketSummary] = []
    var workspaceLoaded = false
    var chief: ChiefDetail?
    var chiefSignal: ConversationSignal?
    var review: ReviewResponse?
    var counts: ViewCounts?
    /// The last admitted view; screens read window paging facts from it.
    private(set) var view: ViewSnapshot?
    var deployment: DeploymentStatus?
    var errors: [String: String] = [:]
    var isLoading = false
    var connection = "Connecting"
    var revision = 0
    private(set) var viewRequest = ViewRequest(windows: NavigationScope.baseWindows)
    private var demands: [String: [ViewWindowRequest]] = [:]
    private var running = false
    private var viewReading = false
    private var viewReadsStarted = 0
    private var resourceVersions = ResourceVersionState()
    private var refreshing = false

    init(baseURL suppliedBaseURL: URL? = nil, session: URLSession = .shared) {
        self.session = session
        defer { restore() }
        if let suppliedBaseURL { baseURL = suppliedBaseURL; return }
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
        managers = []; tickets = []; workspaceLoaded = false; chief = nil; chiefSignal = nil; review = nil; counts = nil; view = nil
        deployment = nil; errors = [:]
        resourceVersions = ResourceVersionState()
        restore()
    }

    // MARK: Saved views

    /// The latest view, and separately the latest one that carried Workspace, so a
    /// relaunch restores both even when Review was the last screen.
    private func savedViewID(_ name: String) -> String { baseURL.absoluteString + "/api/navigation/view/snapshot#" + name }

    private func restore() {
        let server = LocalStore.server(baseURL)
        for name in ["workspace", "latest"] {
            guard let data = try? LocalStore.shared.record(server: server, kind: "cache", id: savedViewID(name))?.payload,
                  case .snapshot(let snapshot)? = try? APIClient.decoder().decode(ViewFrame.self, from: data) else { continue }
            admit(snapshot)
        }
        chief = client.cached("/api/workers/chief-of-staff/settings")
    }

    private func save(_ snapshot: ViewSnapshot, data: Data, api: APIClient) async {
        let server = LocalStore.server(api.baseURL)
        try? await LocalStore.shared.cacheResponse(server: server, id: savedViewID("latest"), data: data)
        if snapshot.window("rail_parents") != nil {
            try? await LocalStore.shared.cacheResponse(server: server, id: savedViewID("workspace"), data: data)
        }
    }

    // MARK: Scope

    /// A mounted screen states the windows it shows; nil withdraws them. A new or
    /// changed demand reads at once. A withdrawal only narrows the next read, so
    /// leaving a screen adds no read in front of the next screen's own.
    func setNavigationDemand(_ name: String, _ windows: [ViewWindowRequest]?) {
        guard demands[name] != windows else { return }
        demands[name] = windows
        let next = NavigationScope.request(demands: demands, previous: viewRequest)
        guard next != viewRequest else { return }
        viewRequest = next
        if running, windows != nil { Task { await readView(from: client) } }
    }

    private func admit(_ snapshot: ViewSnapshot) {
        let presentation = NavigationPresentation(snapshot.content.records)
        if let managers = presentation.workspaceManagers(in: snapshot), let tickets = presentation.workspaceTickets(in: snapshot) {
            self.managers = managers; self.tickets = tickets; workspaceLoaded = true
        }
        chiefSignal = presentation.chief?.signal.conversationSignal
        review = presentation.reviewQueue(in: snapshot)
        counts = snapshot.content.counts
        view = snapshot
    }

    // MARK: Refresh

    /// Owned by the foreground shell task. SwiftUI cancels it on background or URL change.
    func run() async {
        running = true
        defer { running = false }
        await refresh()
        while !Task.isCancelled {
            do {
                connection = "Following changes"
                for try await _ in client.changes() {
                    try Task.checkCancellation()
                    await refresh(changesOnly: true)
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

    func refresh(changesOnly: Bool = false) async {
        // A refresh requested during another read must not be dropped.
        // Waiting remains cancellable when the shell leaves the foreground.
        while refreshing {
            do { try await Task.sleep(for: .milliseconds(50)) } catch { return }
        }
        guard !Task.isCancelled else { return }
        refreshing = true
        isLoading = view == nil && chief == nil
        defer { refreshing = false; isLoading = false }
        let api = client
        let keys = ["/api/workers/chief-of-staff/settings", "/api/deployment-status"]
        let probe = changesOnly ? try? await api.resourceVersions(keys) : nil
        guard !Task.isCancelled, api.baseURL == baseURL else { return }
        async let viewRead: () = readView(from: api)
        async let chiefRead: () = readVersioned("Chief", path: keys[0], api: api, probe: probe, force: !changesOnly) { self.chief = $0 }
        async let deployRead: () = readVersioned("Deployment", path: keys[1], api: api, probe: probe, force: !changesOnly) { self.deployment = $0 }
        _ = await (viewRead, chiefRead, deployRead)
        guard !Task.isCancelled, api.baseURL == baseURL else { return }
        revision += 1
        if errors.isEmpty { Task { await Outbox.shared.drain(api: api) } }
    }

    /// One bounded snapshot for the current scope. A read that starts after this call
    /// covers it; concurrent callers wait for that read instead of repeating it.
    private func readView(from api: APIClient) async {
        let wanted = viewReadsStarted
        while viewReading {
            do { try await Task.sleep(for: .milliseconds(50)) } catch { return }
        }
        guard viewReadsStarted == wanted, !Task.isCancelled, api.baseURL == baseURL else { return }
        viewReading = true
        viewReadsStarted += 1
        defer { viewReading = false }
        let request = viewRequest
        do {
            let (snapshot, data) = try await api.navigationViewSnapshot(request)
            guard !Task.isCancelled, api.baseURL == baseURL else { return }
            guard snapshot.scopeGeneration == request.scopeGeneration else { throw APIError(status: 0, message: "The navigation view answered another scope.") }
            admit(snapshot)
            errors["Navigation"] = nil
            await save(snapshot, data: data, api: api)
        } catch {
            guard !Task.isCancelled, api.baseURL == baseURL else { return }
            errors["Navigation"] = error.localizedDescription
        }
    }

    private func readVersioned<T: Decodable & Sendable>(_ label: String, path: String, api: APIClient, probe: ResourceVersions?, force: Bool, apply: @MainActor @Sendable (T) -> Void) async {
        if !force, errors[label] == nil, resourceVersions.isCurrent(path, probe: probe) { return }
        do {
            let result: ResourceBody<T> = try await api.resourceBody(path)
            guard !Task.isCancelled, api.baseURL == baseURL else { return }
            apply(result.body)
            resourceVersions.accept(result)
            errors[label] = nil
        } catch {
            guard !Task.isCancelled, api.baseURL == baseURL else { return }
            // Older servers and unregistered resources retain ordinary refresh.
            resourceVersions.invalidate(path)
            await read(label, from: api, fetch: { try await api.get(path) }, apply: apply)
        }
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
}
