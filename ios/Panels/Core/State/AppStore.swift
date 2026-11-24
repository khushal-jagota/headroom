import Foundation
import Observation
import SwiftUI

@MainActor @Observable final class AppStore {
    var baseURL: URL
    private let session: URLSession
    @ObservationIgnored lazy var resources = ResourceOwner(api: client)
    /// Unfinished Goal edits survive route pop/reentry for this app session only.
    let goalEdits = PageSessionRoute()
    let updateActions = UpdateActionsState()
    var client: APIClient { APIClient(baseURL: baseURL, session: session) }
    /// Workspace rows from the last view that carried the rail windows. A scope
    /// without them, such as Review alone, keeps the last coherent Workspace.
    var managers: [ManagerSummary] = []
    var tickets: [TicketSummary] = []
    var workspaceLoaded = false
    var workspaceSettled: [ManagerSummary] = []
    /// The coherent publication that supplies the currently retained Workspace rows.
    private(set) var workspaceView: ViewSnapshot?
    /// Per Workspace card (manager id, or "chief"), the active tickets its rail window
    /// counts but did not load: the shared row budget can cut a card short.
    var workspaceHidden: [String: Int] = [:]
    private var chiefResource: ResourceHandle<ChiefDetail> { resources.handle(ChiefResources.settings) }
    var updateResource: ResourceHandle<PanelsUpdateStatus> { resources.handle(UpdateResources.status) }
    private var deploymentResource: ResourceHandle<DeploymentStatus> { resources.handle(ChiefResources.deployment) }
    private var healthResource: ResourceHandle<ExecutorHealth> { resources.handle(ChiefResources.executorHealth) }
    private var driversResource: ResourceHandle<DriverCatalog> { resources.handle(DriverResources.catalog) }
    /// The server's driver list; nil until it arrives, or when the server has none.
    var drivers: DriverCatalog? { driversResource.value }
    /// A backend's label from the driver list, else its key capitalised.
    func backendLabel(_ key: String) -> String { DriverCatalog.label(key, in: drivers) }
    var chief: ChiefDetail? { chiefResource.value }
    var chiefSignal: ConversationSignal?
    var review: ReviewResponse?
    var counts: ViewCounts?
    /// The last admitted view; screens read window paging facts from it.
    private(set) var view: ViewSnapshot?
    var executorHealth: ExecutorHealth? { healthResource.value }
    var updateStatus: PanelsUpdateStatus? { updateResource.value }
    var updateFailure: ServerReadFailure? { updateResource.failure }
    var deployment: DeploymentStatus? { deploymentResource.value }
    var errors: [String: ServerReadFailure] = [:]
    var serverUnreachable: Bool {
        errors.values.contains { $0.isServerUnreachable } || chiefResource.failure?.isServerUnreachable == true ||
            deploymentResource.failure?.isServerUnreachable == true || updateResource.failure?.isServerUnreachable == true
    }
    var isLoading = false
    /// The view stream's health: "Following changes" once the current stream delivered
    /// its snapshot, "Reconnecting" after a failure until the next snapshot.
    var connection = "Connecting"
    /// Counts connection sessions after the first: each return to the foreground and each
    /// recovery from a dropped connection. Screens read their detail again at each, so a
    /// read that failed offline is retried, and never on an unrelated change.
    private(set) var connectionSession = 0
    /// Counts settings reads, which run at start, on each change and on refresh. Goal
    /// screens read again at each; they are outside the navigation view.
    private(set) var revision = 0
    private var hasRun = false
    /// Each view record's reload identity. A detail screen reloads when its own records'
    /// stamps change, never because another record changed.
    private(set) var recordStamps: [RecordKey: String] = [:]
    private(set) var presentation: NavigationPresentation?
    private(set) var viewRequest = ViewRequest(windows: NavigationScope.baseWindows)
    private var demands: [String: [ViewWindowRequest]] = [:]
    private var reviewHistory = ReviewHistory()
    /// Detail screens that named a selected owner, most recent last.
    private var selections: [(name: String, owner: ViewOwner)] = []
    private var running = false
    private var runGeneration = 0
    private struct ViewStream { let id: Int; let request: ViewRequest; let task: Task<ViewStreamEnd, Never> }
    /// The stream the view follows, and a replacement opened beside it. The replacement
    /// takes over when its snapshot lands, and the followed stream closes then. At most
    /// these two are open, and two only while the replacement waits for its snapshot.
    private var activeStream: ViewStream?
    private var pendingStream: ViewStream?
    private var replacementScheduled = false
    /// A pull asked for a new stream even if the followed one carries the current scope.
    private var freshStreamRequested = false
    private var streamsOpened = 0
    /// The newest stream that ended. A pull stops waiting when its stream ended without a snapshot.
    private var lastEndedStream = 0
    /// The newest stream whose snapshot landed. A pull waits for a stream opened after it began.
    private var lastSnapshotStream = 0
    /// The scope the followed stream carries, which may be wider than `viewRequest`.
    private var streamRequest: ViewRequest? { activeStream?.request }
    private var reconnectRequested = false
    private var viewReading = false
    private var viewReadsStarted = 0

    init(baseURL suppliedBaseURL: URL? = nil, session: URLSession = .shared) {
        self.session = session
        defer { restore() }
        baseURL = suppliedBaseURL ?? Self.configuredBaseURL
    }

    /// The same selected origin is available before SwiftUI creates a scene.
    nonisolated static var configuredBaseURL: URL {
#if DEBUG
        if let at = ProcessInfo.processInfo.arguments.firstIndex(of: "--conversation-stub"), ProcessInfo.processInfo.arguments.indices.contains(at + 1),
           let local = URL(string: ProcessInfo.processInfo.arguments[at + 1]), ["localhost", "127.0.0.1"].contains(local.host) {
            // A SpringBoard notification response does not retain XCTest's
            // launch arguments. Persist this explicit DEBUG fixture selection.
            UserDefaults.standard.set(local.absoluteString, forKey: "panels.baseURL")
            return local
        }
        if ProcessInfo.processInfo.arguments.contains("--conversation-smoke") {
            return URL(string: "http://127.0.0.1:1")!
        }
#endif
        let saved = UserDefaults.standard.string(forKey: "panels.baseURL")
        return saved.flatMap(URL.init(string:)) ?? APIClient.defaultBaseURL
    }

    func configure(baseURL: URL) {
        runGeneration += 1
        displayGeneration += 1
        displaySaveTask?.cancel(); displaySaveTask = nil; pendingDisplays = [:]; lastDisplaySave = .distantPast
        self.baseURL = baseURL
        updateActions.reset()
        resources.configure(api: client)
        UserDefaults.standard.set(baseURL.absoluteString, forKey: "panels.baseURL")
        managers = []; tickets = []; workspaceLoaded = false; workspaceHidden = [:]; workspaceSettled = []; workspaceView = nil; chiefSignal = nil; review = nil; counts = nil; view = nil
        presentation = nil; recordStamps = [:]
        errors = [:]
        // Review's history names one server's Tickets and conversations.
        reviewHistory = ReviewHistory()
        updateScope(opening: false)
        restore()
    }

    // MARK: Saved views

    /// The reduced current display is replaceable. It never supplies a stream base.
    private var pendingDisplays: [String: ViewSnapshot] = [:]
    private var displaySaveTask: Task<Void, Never>?
    private var displayEncodingTask: Task<Void, Never>?
    private var lastDisplaySave = Date.distantPast
    private var displayGeneration = 0
    private var displayCheckpointSequence = 0
    private func savedViewID(_ name: String) -> String { baseURL.absoluteString + "/api/navigation/view/snapshot#" + name }

    private func restore() {
        let server = LocalStore.server(baseURL)
        for name in ["workspace", "latest"] {
            guard let record = try? LocalStore.shared.record(server: server, kind: "cache", id: savedViewID(name)),
                  record.updated > Date.now.addingTimeInterval(-DisplayCacheConfiguration.maximumAge),
                  let snapshot = try? APIClient.decoder().decode(ViewSnapshot.self, from: record.payload),
                  snapshot.wireVersion == NavigationViewVersion.wire,
                  snapshot.projectionVersion == NavigationViewVersion.projection else { continue }
            admit(snapshot.savedDisplay, persist: false)
        }
    }

    private func checkpointNavigation(_ snapshot: ViewSnapshot) {
        pendingDisplays["latest"] = snapshot
        if snapshot.window("rail_parents") != nil { pendingDisplays["workspace"] = snapshot }
        let delay = max(0, DisplayCacheConfiguration.checkpointInterval - Date().timeIntervalSince(lastDisplaySave))
        guard displaySaveTask == nil else { return }
        let generation = displayGeneration
        displaySaveTask = Task { [weak self] in
            do { try await Task.sleep(for: .seconds(delay)) } catch { return }
            guard let self, generation == self.displayGeneration else { return }
            self.displaySaveTask = nil
            await self.saveNavigationDisplay()
        }
    }
    func saveNavigationDisplay() async {
        displaySaveTask?.cancel(); displaySaveTask = nil
        // An explicit checkpoint also waits for the automatic checkpoint already
        // in progress, so a following disk barrier includes its admitted bytes.
        await displayEncodingTask?.value
        let displays = pendingDisplays
        guard !displays.isEmpty else { return }
        pendingDisplays = [:]; lastDisplaySave = .now
        displayCheckpointSequence += 1
        let sequence = displayCheckpointSequence, generation = displayGeneration
        let server = LocalStore.server(baseURL)
        let ids = displays.keys.reduce(into: [String: String]()) { $0[$1] = savedViewID($1) }
        let task = Task { [weak self] in
            let encoded = await Task.detached(priority: .utility) {
                let encoder = JSONEncoder(); encoder.keyEncodingStrategy = .convertToSnakeCase
                return displays.compactMap { name, snapshot -> (String, Data)? in
                    guard let data = try? encoder.encode(snapshot.savedDisplay) else { return nil }
                    return (name, data)
                }
            }.value
            guard let self, generation == self.displayGeneration, sequence == self.displayCheckpointSequence else { return }
            for (name, data) in encoded {
                guard let id = ids[name] else { continue }
                LocalStore.shared.enqueueCacheResponse(server: server, id: id, data: data)
            }
        }
        displayEncodingTask = task
        await task.value
        if sequence == displayCheckpointSequence { displayEncodingTask = nil }
    }

    // MARK: Scope

    /// A mounted screen states the windows it shows; nil withdraws them. A new or
    /// changed demand opens a stream for the new scope at once, and the current stream
    /// keeps the view live until the new snapshot arrives. A withdrawal only narrows the
    /// next stream, so leaving a screen puts no view read in front of the next screen's own.
    func setNavigationDemand(_ name: String, _ windows: [ViewWindowRequest]?) {
        guard demands[name] != windows else { return }
        demands[name] = windows
        updateScope(opening: windows != nil)
    }

    /// Review's skipped and returned entries. Only a new return order changes what the
    /// window holds, so only it opens a stream; skipped keys ride the next one.
    func setReviewHistory(_ history: ReviewHistory) {
        guard history != reviewHistory else { return }
        let reordered = history.priority != reviewHistory.priority
        reviewHistory = history
        updateScope(opening: reordered)
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
        let next = NavigationScope.request(demands: demands, selected: selections.last?.owner,
            reviewHistory: reviewHistory, previous: viewRequest)
        guard next != viewRequest else { return }
        viewRequest = next
        guard running, opening else { return }
        replaceStream()
    }

    /// Opens a stream for the current scope. A current stream stays followed until the
    /// new one's snapshot lands, as the web does; several requests in one turn share
    /// one new stream. Without a current stream there is nothing live to keep, so the
    /// follow loop opens the new stream at once.
    private func replaceStream(fresh: Bool = false) {
        guard running else { return }
        if fresh { freshStreamRequested = true }
        guard activeStream != nil, connection == "Following changes" else {
            pendingStream?.task.cancel(); pendingStream = nil
            if let activeStream { activeStream.task.cancel() } else { reconnectRequested = true }
            return
        }
        guard !replacementScheduled else { return }
        replacementScheduled = true
        Task {
            replacementScheduled = false
            guard running else { return }
            // The followed stream ended meanwhile: the follow loop opens the current scope now.
            guard let active = activeStream else { reconnectRequested = true; return }
            let fresh = freshStreamRequested
            freshStreamRequested = false
            // A replacement that took over meanwhile may already carry this scope.
            guard fresh || active.request != viewRequest else { return }
            pendingStream?.task.cancel()
            pendingStream = openStream(viewRequest)
        }
    }

    private func openStream(_ request: ViewRequest) -> ViewStream {
        streamsOpened += 1
        let id = streamsOpened, api = client
        return ViewStream(id: id, request: request, task: Task { await self.consume(request, api: api, stream: id) })
    }

    private func closeStreams() {
        activeStream?.task.cancel(); pendingStream?.task.cancel()
        activeStream = nil; pendingStream = nil
    }

    private func admit(_ snapshot: ViewSnapshot, persist: Bool = true) {
        let presentation = NavigationPresentation(snapshot.content.records)
        if let managers = presentation.workspaceManagers(in: snapshot), let tickets = presentation.workspaceTickets(in: snapshot) {
            self.managers = managers; self.tickets = tickets; workspaceLoaded = true; workspaceView = snapshot
            workspaceHidden = Dictionary(snapshot.content.windows.filter { $0.request.section == "rail_tickets" && $0.request.group == "all" }
                .map { ($0.request.owner?.id ?? "chief", max(0, $0.total - $0.members.count)) }, uniquingKeysWith: { first, _ in first })
        }
        if let settled = presentation.workspaceSettledManagers(in: snapshot) { workspaceSettled = settled }
        chiefSignal = presentation.chief?.signal.conversationSignal
        review = presentation.reviewQueue(in: snapshot)
        counts = snapshot.content.counts
        let stamps = Dictionary(snapshot.content.records.compactMap { record in record.stamp.map { (record.key, $0) } }, uniquingKeysWith: { _, latest in latest })
        if stamps != recordStamps { recordStamps = stamps }
        self.presentation = presentation
        view = snapshot
        if persist { checkpointNavigation(snapshot) }
    }

    /// Outer nil means that this view does not carry the owner. Inner nil is a
    /// known cleared link, and must not fall back to a saved conversation.
    func currentConversationID(_ owner: ConversationOwner) -> String?? {
        switch owner {
        case .chief:
            guard let chief = presentation?.chief else { return nil }
            return .some(chief.conversationId)
        case .manager(let id):
            guard let manager = presentation?.managersById[id] else { return nil }
            return .some(manager.conversationId)
        case .ticket(let id):
            guard let ticket = presentation?.ticketsById[id] else { return nil }
            return .some(ticket.conversationId)
        case .goal: return nil
        }
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

    /// The shell keys `run()` on this. A brief inactive moment, such as Control Center or a
    /// notification pull-down, keeps the streams open; only the background ends them.
    nonisolated static func follows(_ phase: ScenePhase) -> Bool { phase != .background }

    /// Owned by the foreground shell task. SwiftUI cancels it on background or URL change,
    /// which closes both streams; the next foreground entry starts with fresh snapshots.
    /// SwiftUI starts the next run without waiting for the cancelled one to unwind, so
    /// a run clears the shared stream state only while it is still the newest run.
    func run() async {
        runGeneration += 1
        let generation = runGeneration
        running = true
        let chief = chiefResource, deployment = deploymentResource, updates = updateResource, health = healthResource, drivers = driversResource
        chief.acquire(); deployment.acquire(); updates.acquire(); health.acquire(); drivers.acquire()
        resources.start()
        // An earlier run's streams close now, so a new run never follows beside them.
        closeStreams()
        connection = "Connecting"
        let updateReads = Task {
            await UpdateStatusRefresh.follow(current: { updates.value }, refresh: { await updates.refresh() })
        }
        if hasRun { connectionSession += 1 }
        hasRun = true
        defer {
            updateReads.cancel()
            chief.release(); deployment.release(); updates.release(); health.release(); drivers.release()
            if runGeneration == generation {
                Task { await saveNavigationDisplay() }
                resources.stop(); running = false; closeStreams(); connection = "Connecting"
            }
        }
        await followView()
    }

    fileprivate enum ViewStreamEnd { case cancelled, ended(admitted: Bool), refused(admitted: Bool), failed }

    /// Mirrors web/src/lib/navigation/views/owner.ts: one stream per scope generation.
    /// A gap, an obsolete frame, a reset or a closed stream needs a fresh snapshot, which
    /// a new stream sends first. Failures retry with backoff and show Reconnecting.
    private func followView() async {
        let generation = runGeneration
        var failures = 0
        while !Task.isCancelled, generation == runGeneration {
            reconnectRequested = false
            let stream = activeStream ?? openStream(viewRequest)
            activeStream = stream
            let end = await withTaskCancellationHandler { await stream.task.value } onCancel: { stream.task.cancel() }
            if Task.isCancelled || generation != runGeneration { return }
            // A replacement's snapshot landed and it took over: follow it.
            if activeStream?.id != stream.id { failures = 0; continue }
            activeStream = nil
            if let pending = pendingStream {
                // The followed stream ended while its replacement waited: the replacement is followed
                // now. Its scope is the newest asked for, or wider after a narrowing.
                pendingStream = nil
                activeStream = pending
            }
            switch end {
            case .cancelled: break // A new or widened scope: open its stream at once.
            case .ended(let admitted), .refused(let admitted):
                // A stream that was current reconnects after a second for its fresh
                // snapshot, as the web does: the server closes on change-log gaps, and a
                // repeating gap must not become a loop of full snapshots.
                if admitted { failures = 0 } else { failures += 1 }
            case .failed: failures += 1
            }
            // The stream that delivered changes is gone: the view is not following until the next snapshot.
            if failures > 0 { connection = "Reconnecting" } else if connection == "Following changes" { connection = "Connecting" }
            if case .cancelled = end { continue }
            if activeStream != nil { continue }
            // 1, 2, 4, then 8 s: a phone in the foreground wants to be current again soon.
            let delay = Duration.seconds(1 << min(max(failures, 1) - 1, 3))
            let deadline = ContinuousClock.now + delay
            while ContinuousClock.now < deadline, !reconnectRequested {
                do { try await Task.sleep(for: .milliseconds(100)) } catch { return }
            }
        }
    }

    private func consume(_ request: ViewRequest, api: APIClient, stream: Int) async -> ViewStreamEnd {
        let generation = runGeneration
        defer {
            if pendingStream?.id == stream { pendingStream = nil }
            lastEndedStream = max(lastEndedStream, stream)
        }
        // Frames apply to this stream's own snapshot, never to a restored or older view.
        var current: ViewSnapshot?
        var admitted = false
        do {
            for try await (frame, _) in api.navigationViewEvents(request) {
                guard !Task.isCancelled, generation == runGeneration, api.baseURL == baseURL else { return .cancelled }
                if let generation = frame.scopeGeneration, generation != request.scopeGeneration { return .refused(admitted: admitted) }
                guard case .success(let next) = NavigationViewFrames.apply(current, frame) else { return .refused(admitted: admitted) }
                current = next
                if case .checkpoint = frame { continue } // Coverage only; the content is unchanged.
                if case .snapshot = frame, let pending = pendingStream, pending.id == stream {
                    // The replacement's snapshot landed: it takes over, and the stream it replaces closes now.
                    let replaced = activeStream
                    activeStream = pending; pendingStream = nil
                    replaced?.task.cancel()
                }
                guard activeStream?.id == stream else { continue }
                if case .snapshot = frame {
                    admit(next)
                    admitted = true
                    lastSnapshotStream = max(lastSnapshotStream, stream)
                    if connection == "Reconnecting" { connectionSession += 1 }
                    connection = "Following changes"
                    errors["Navigation"] = nil
                    Task { await Outbox.shared.drain(api: api) }
                } else { admit(next) }
                // A page that waited for the next stream was carried by another screen's
                // window. Once its record leaves this stream, open one with it selected.
                if let owner = selections.last?.owner, request.selected != owner,
                   recordStamps[RecordKey(kind: owner.kind, id: owner.id)] == nil { return .cancelled }
            }
            return Task.isCancelled || generation != runGeneration ? .cancelled : .ended(admitted: admitted)
        } catch {
            if Task.isCancelled || generation != runGeneration || api.baseURL != baseURL { return .cancelled }
            // A replacement that fails leaves the followed stream as it was.
            if activeStream?.id == stream { errors["Navigation"] = ServerReadFailure(error) }
            return .failed
        }
    }

    /// Pull to refresh. Chief settings and deployment read again, and the view opens a
    /// new stream, whose first frame is a fresh snapshot. The pull ends when that
    /// snapshot lands, or when the new stream ends without one. Without a foreground
    /// stream, one snapshot is read.
    func pull() async {
        guard !Task.isCancelled else { return }
        isLoading = view == nil && chief == nil
        defer { isLoading = false }
        let api = client
        async let settingsRead: () = readSettings(probing: false)
        if running {
            // A replacement for the current scope is already on its way, and its snapshot is
            // the fresh one: a pull in quick succession shares it, so no third stream opens.
            if let pending = pendingStream, pending.request == viewRequest {
                await snapshotLanded(fromStreamAfter: pending.id - 1)
            } else {
                let opened = streamsOpened
                replaceStream(fresh: true)
                await snapshotLanded(fromStreamAfter: opened)
            }
        } else { await readView(from: api) }
        await settingsRead
        guard !Task.isCancelled, api.baseURL == baseURL else { return }
        if errors.isEmpty { Task { await Outbox.shared.drain(api: api) } }
    }

    private func snapshotLanded(fromStreamAfter opened: Int) async {
        let deadline = ContinuousClock.now + .seconds(25)
        while lastSnapshotStream <= opened, running, ContinuousClock.now < deadline, !Task.isCancelled {
            let open = [activeStream, pendingStream].contains { ($0?.id ?? 0) > opened }
            // The new stream ended without a snapshot, as when the phone is offline.
            if !open, lastEndedStream > opened { return }
            do { try await Task.sleep(for: .milliseconds(50)) } catch { return }
        }
    }

    /// Refresh after confirmed writes. Chief settings and deployment read again. The
    /// view follows its stream, which carries the write; a stream that is not current reconnects now. Without a
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

    /// Shell documents use the same owner as every mounted detail.
    func readSettings(probing: Bool) async {
        if probing { await resources.reconcile() }
        else {
            async let chief: Void = chiefResource.refresh()
            async let deployment: Void = deploymentResource.refresh()
            async let health: Void = healthResource.refresh()
            async let updates: Void = updateResource.refresh()
            _ = await (chief, deployment, health, updates)
        }
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
        let generation = runGeneration
        do {
            let (snapshot, _) = try await api.navigationViewSnapshot(request)
            guard !Task.isCancelled, generation == runGeneration, api.baseURL == baseURL else { return }
            guard snapshot.scopeGeneration == request.scopeGeneration else { throw APIError(status: 0, message: "The navigation view answered another scope.") }
            admit(snapshot)
            errors["Navigation"] = nil
        } catch {
            guard !Task.isCancelled, generation == runGeneration, api.baseURL == baseURL else { return }
            errors["Navigation"] = ServerReadFailure(error)
        }
    }

}
