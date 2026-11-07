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
    /// The view stream's health: "Following changes" once the current stream delivered
    /// its snapshot, "Reconnecting" after a failure until the next snapshot.
    var connection = "Connecting"
    /// Counts connection sessions after the first: each return to the foreground and each
    /// recovery from a dropped connection. Screens read their detail again at each, so a
    /// read that failed offline is retried, and never on an unrelated change.
    private(set) var connectionSession = 0
    private var hasRun = false
    /// Each view record's reload identity. A detail screen reloads when its own records'
    /// stamps change, never because another record changed.
    private(set) var recordStamps: [RecordKey: String] = [:]
    private(set) var presentation: NavigationPresentation?
    private(set) var viewRequest = ViewRequest(windows: NavigationScope.baseWindows)
    private var demands: [String: [ViewWindowRequest]] = [:]
    /// Detail screens that named a selected owner, most recent last.
    private var selections: [(name: String, owner: ViewOwner)] = []
    private var running = false
    private var viewConsumer: Task<ViewStreamEnd, Never>?
    /// The scope the running stream follows, which may be wider than `viewRequest`.
    private var streamRequest: ViewRequest?
    private var reconnectRequested = false
    private var viewReading = false
    private var viewReadsStarted = 0
    private var resourceVersions = ResourceVersionState()

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
        presentation = nil; recordStamps = [:]
        deployment = nil; errors = [:]
        resourceVersions = ResourceVersionState()
        restore()
    }

    // MARK: Saved views

    /// The latest view, and separately the latest one that carried Workspace, so a
    /// relaunch restores both even when Review was the last screen. Each stream's
    /// snapshot is saved as read; patches between them are not.
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
    /// changed demand opens a stream for the new scope at once, and the last coherent
    /// view stays on screen until its snapshot arrives. A withdrawal only narrows the
    /// next stream, so leaving a screen puts no view read in front of the next screen's own.
    func setNavigationDemand(_ name: String, _ windows: [ViewWindowRequest]?) {
        guard demands[name] != windows else { return }
        demands[name] = windows
        updateScope(opening: windows != nil)
    }

    /// A detail screen names the one record it keys its reloads on, so the view carries
    /// it wherever the screen was opened from. The most recent screen's owner wins. When
    /// the current stream already carries that record and those windows, as for a Ticket
    /// opened from Workspace or Review, the new scope waits for the next stream, so no
    /// view read goes in front of the page's own first read.
    func setNavigationDemand(_ name: String, selected owner: ViewOwner, windows: [ViewWindowRequest] = []) {
        let unchanged = demands[name] == windows && selections.last.map { $0.name == name && $0.owner == owner } == true
        guard !unchanged else { return }
        let covered = connection == "Following changes" && recordStamps[RecordKey(kind: owner.kind, id: owner.id)] != nil
            && windows.allSatisfy { streamRequest?.windows.contains($0) == true }
        demands[name] = windows
        selections.removeAll { $0.name == name }
        selections.append((name, owner))
        updateScope(opening: !covered)
    }

    func withdrawNavigationDemand(_ name: String) {
        guard demands[name] != nil || selections.contains(where: { $0.name == name }) else { return }
        demands[name] = nil
        selections.removeAll { $0.name == name }
        updateScope(opening: false)
    }

    private func updateScope(opening: Bool) {
        let next = NavigationScope.request(demands: demands, selected: selections.last?.owner, previous: viewRequest)
        guard next != viewRequest else { return }
        viewRequest = next
        guard running, opening else { return }
        // The follow loop opens the new scope's stream; several changes in one turn share it.
        if let viewConsumer { viewConsumer.cancel() } else { reconnectRequested = true }
    }

    private func admit(_ snapshot: ViewSnapshot) {
        let presentation = NavigationPresentation(snapshot.content.records)
        if let managers = presentation.workspaceManagers(in: snapshot), let tickets = presentation.workspaceTickets(in: snapshot) {
            self.managers = managers; self.tickets = tickets; workspaceLoaded = true
        }
        chiefSignal = presentation.chief?.signal.conversationSignal
        review = presentation.reviewQueue(in: snapshot)
        counts = snapshot.content.counts
        let stamps = Dictionary(snapshot.content.records.compactMap { record in record.stamp.map { (record.key, $0) } }, uniquingKeysWith: { _, latest in latest })
        if stamps != recordStamps { recordStamps = stamps }
        self.presentation = presentation
        view = snapshot
    }

    // MARK: Reload identities

    func ticketStamp(_ id: String) -> String? { recordStamps[RecordKey(kind: "ticket", id: id)] }

    /// An orchestrator page: its record, and its children window's members and total.
    func managerStamp(_ id: String) -> String? { ownerStamp(RecordKey(kind: "manager", id: id), children: .manager(id)) }

    /// The Chief page: its record and children window, and its settings body.
    func chiefStamp() -> String? {
        ownerStamp(RecordKey(kind: "chief", id: "chief"), children: .chief).map { $0 + "\n" + (chief.map { $0.label + "\u{1f}" + $0.recap } ?? "") }
    }

    private func ownerStamp(_ key: RecordKey, children owner: ViewOwner) -> String? {
        let identity = NavigationScope.children(of: owner).identity
        guard let own = recordStamps[key], let window = view?.content.windows.first(where: { $0.request.identity == identity }) else { return nil }
        return ([own, String(window.total)] + window.members.map { $0.id + "=" + (recordStamps[$0] ?? "") }).joined(separator: "\n")
    }

    /// A detail read's rows with the view's current working and unread marks, so a
    /// mark change needs no detail read. Rows the view does not carry keep their own.
    func withLiveMarks<Row: LiveMarkedWorker>(_ rows: [Row]) -> [Row] {
        guard let presentation else { return rows }
        return rows.map { row in
            guard let live = presentation.ticketsById[row.id], let signal = live.signal.conversationSignal else { return row }
            var row = row
            row.conversationSignal = signal
            if let state = live.agentState { row.agentState = state }
            return row
        }
    }

    // MARK: Following the view

    /// Owned by the foreground shell task. SwiftUI cancels it on background or URL change,
    /// which closes both streams; the next foreground entry starts with fresh snapshots.
    func run() async {
        running = true
        if hasRun { connectionSession += 1 }
        hasRun = true
        defer { running = false; viewConsumer = nil; streamRequest = nil }
        async let settings: () = followSettings()
        await followView()
        await settings
    }

    fileprivate enum ViewStreamEnd { case cancelled, ended(admitted: Bool), refused(admitted: Bool), failed }

    /// Mirrors web/src/lib/navigation/views/owner.ts: one stream per scope generation.
    /// A gap, an obsolete frame, a reset or a closed stream needs a fresh snapshot, which
    /// a new stream sends first. Failures retry with backoff and show Reconnecting.
    private func followView() async {
        var failures = 0
        while !Task.isCancelled {
            let api = client
            let request = viewRequest
            reconnectRequested = false
            streamRequest = request
            let consumer = Task { await self.consume(request, api: api) }
            viewConsumer = consumer
            let end = await withTaskCancellationHandler { await consumer.value } onCancel: { consumer.cancel() }
            viewConsumer = nil
            if Task.isCancelled { return }
            switch end {
            case .cancelled: continue // A new or widened scope: open its stream at once.
            case .ended(let admitted), .refused(let admitted):
                // A stream that was current reconnects at once for its fresh snapshot.
                if admitted { failures = 0; continue }
                failures += 1
            case .failed: failures += 1
            }
            connection = "Reconnecting"
            // 1, 2, 4, then 8 s: a phone in the foreground wants to be current again soon.
            let delay = Duration.seconds(1 << min(failures - 1, 3))
            let deadline = ContinuousClock.now + delay
            while ContinuousClock.now < deadline, !reconnectRequested {
                do { try await Task.sleep(for: .milliseconds(100)) } catch { return }
            }
        }
    }

    private func consume(_ request: ViewRequest, api: APIClient) async -> ViewStreamEnd {
        // Frames apply to this stream's own snapshot, never to a restored or older view.
        var current: ViewSnapshot?
        var admitted = false
        do {
            for try await (frame, data) in api.navigationViewEvents(request) {
                guard !Task.isCancelled, api.baseURL == baseURL else { return .cancelled }
                if let generation = frame.scopeGeneration, generation != request.scopeGeneration { return .refused(admitted: admitted) }
                guard case .success(let next) = NavigationViewFrames.apply(current, frame) else { return .refused(admitted: admitted) }
                current = next
                switch frame {
                case .checkpoint: continue // Coverage only; the content is unchanged.
                case .snapshot:
                    admit(next)
                    admitted = true
                    if connection == "Reconnecting" { connectionSession += 1 }
                    connection = "Following changes"
                    errors["Navigation"] = nil
                    Task { await self.save(next, data: data, api: api) }
                    Task { await Outbox.shared.drain(api: api) }
                default: admit(next)
                }
                // A page that waited for the next stream was carried by another screen's
                // window. Once its record leaves this stream, open one with it selected.
                if let owner = selections.last?.owner, request.selected != owner,
                   recordStamps[RecordKey(kind: owner.kind, id: owner.id)] == nil { return .cancelled }
            }
            return Task.isCancelled ? .cancelled : .ended(admitted: admitted)
        } catch {
            if Task.isCancelled || api.baseURL != baseURL { return .cancelled }
            errors["Navigation"] = error.localizedDescription
            return .failed
        }
    }

    /// Chief settings and deployment status are outside the view, and deployment status
    /// is not a navigation change at all, so these two small versioned reads keep the
    /// payload-free change stream. Wakes are coalesced to one probe a second.
    private func followSettings() async {
        await readSettings(probing: false)
        while !Task.isCancelled {
            do {
                for try await _ in client.changes() {
                    try Task.checkCancellation()
                    await readSettings(probing: true)
                    try await Task.sleep(for: .seconds(1))
                }
            } catch {
                if Task.isCancelled { return }
            }
            do { try await Task.sleep(for: .seconds(3)) } catch { return }
        }
    }

    /// Manual refresh and confirmed writes. Chief settings and deployment read again. The
    /// view follows its stream; a stream that is not current reconnects now. Without a
    /// foreground stream, one snapshot is read.
    func refresh() async {
        guard !Task.isCancelled else { return }
        isLoading = view == nil && chief == nil
        defer { isLoading = false }
        if connection != "Following changes" { reconnectRequested = true }
        let api = client
        async let settingsRead: () = readSettings(probing: false)
        if !running { await readView(from: api) }
        await settingsRead
        guard !Task.isCancelled, api.baseURL == baseURL else { return }
        if errors.isEmpty { Task { await Outbox.shared.drain(api: api) } }
    }

    /// Probing reads a body only when its version moved; otherwise both bodies read.
    func readSettings(probing: Bool) async {
        let api = client
        let keys = ["/api/workers/chief-of-staff/settings", "/api/deployment-status"]
        let probe = probing ? try? await api.resourceVersions(keys) : nil
        guard !Task.isCancelled, api.baseURL == baseURL else { return }
        async let chiefRead: () = readVersioned("Chief", path: keys[0], api: api, probe: probe, force: !probing) { self.chief = $0 }
        async let deployRead: () = readVersioned("Deployment", path: keys[1], api: api, probe: probe, force: !probing) { self.deployment = $0 }
        _ = await (chiefRead, deployRead)
    }

    /// One bounded snapshot for the current scope, when no stream runs. A read that starts
    /// after this call covers it; concurrent callers wait for that read instead of repeating it.
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
