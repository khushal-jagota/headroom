import SwiftUI

struct WorkspaceView: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @AppStorage("panels.goals-sidebar-rows") private var showGoalRows = false
    @Namespace private var railNamespace
    @State private var visible = false
    @State private var groups: [String: [WorkspaceTicketGroup]] = [:]
    @State private var orderedGroups: [String: [WorkspaceTicketGroup]] = [:]
    @State private var managers: [ManagerSummary] = []
    @State private var settled: [ManagerSummary] = []
    @State private var windows: [ViewWindow] = []
    @State private var hasPublished = false
    @State private var retainedGoalRows: [String: [GoalRecord]] = [:]
    @State private var goalRowsEnabled = false
    @State private var pages: [String: (anchor: String, direction: String)] = [:]
    @State private var settledOpen = false
    @State private var settledBusy: String?
    @State private var settledError: String?
    @State private var viewportHeight: CGFloat = 0
    @State private var settledContentHeight: CGFloat = 0
    @State private var goalsHandle: ResourceHandle<GoalsResponse>?
    @State private var machinesHandle: ResourceHandle<WorkspaceMachinesResponse>?
    @State private var healthHandles: [String: ResourceHandle<WorkspaceMachineHealth>] = [:]
    @State private var selectedRoute: AppRoute?
    @State private var scrolling = false
    @State private var previewOpen = false
    @State private var motionActive = false
    @State private var motionTask: Task<Void, Never>?
    @State private var frames: [String: CGRect] = [:]
    @State private var exitingIDs: Set<String> = []
    @State private var enteringIDs: Set<String> = []
    @State private var enterVisible = true
    @GestureState private var fingerDown = false

    private var goals: [GoalRecord] { goalsHandle?.value?.goals ?? [] }
    private var machines: [WorkspaceMachine] { machinesHandle?.value?.machines ?? [] }
    private var machineHealth: [String: WorkspaceMachineHealth] { healthHandles.compactMapValues { $0.error == nil ? $0.value : nil } }
    private var machineIDs: [String] {
        let retained = groups.values.flatMap { $0.flatMap(\.tickets) }
        return Array(Set((store.tickets + retained).filter { $0.locale == "macos" }.compactMap(\.machineId))).sorted()
    }
    private var hasContent: Bool { store.chief != nil || store.workspaceLoaded }
    private var refreshError: String? { store.errors["Navigation"]?.message ?? store.errors["Chief"]?.message ?? goalsHandle?.error }
    private var held: Bool { fingerDown || scrolling || previewOpen || motionActive }

    private func request(_ section: String, owner: ViewOwner? = nil, limit: Int = 60) -> ViewWindowRequest {
        var request = ViewWindowRequest(section: section, owner: owner, limit: limit)
        if let page = pages[request.identity] { request.anchor = page.anchor; request.direction = page.direction }
        return request
    }
    private func window(_ section: String, owner: ViewOwner? = nil) -> ViewWindow? {
        let identity = request(section, owner: owner).identity
        return windows.first { $0.request.identity == identity }
    }
    private var workspaceWindows: [ViewWindowRequest] {
        var result = [request("rail_parents"), request("rail_tickets", owner: .chief, limit: 30)]
            + store.managers.map { request("rail_tickets", owner: .manager($0.id)) }
        if settledOpen { result.append(request("settled")) }
        if showGoalRows {
            let holders: [ViewOwner] = [.chief] + store.managers.map { .manager($0.id) }
            for holder in holders {
                let count = goals.filter { $0.holderKind.rawValue == holder.kind && ($0.holderId ?? "chief") == holder.id }.count
                let total = max(count, window("goals", owner: holder)?.total ?? 0)
                if total > 0 { result.append(request("goals", owner: holder, limit: min(total, 60))) }
            }
        }
        return result
    }

    var body: some View {
        VStack(spacing: PanelsTheme.Web.Rail.gap) {
            activeWork
            if hasContent { settledDock }
        }
        .padding(.bottom, PanelsTheme.Web.Rail.gap)
        .coordinateSpace(name: "workspace-rail")
        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { viewportHeight = $0 }
        .onPreferenceChange(WorkspaceRailFramesPreferenceKey.self) { frames = $0 }
        .screenLoadState("workspace", hasContent: hasContent, error: refreshError) { await refresh() }
        .navigationTitle("")
        .navigationBarTitleDisplayMode(.inline)
        .onChange(of: selectedRoute) { old, new in if old != new { Haptics.light() } }
        .navigationDestination(item: $selectedRoute) { RouteDestination(route: $0) }
        .background(PanelsTheme.Web.surface).foregroundStyle(PanelsTheme.Web.text)
        .onAppear {
            visible = true
            store.setNavigationDemand("workspace", workspaceWindows)
            updateGroups()
        }
        .onDisappear {
            visible = false
            cancelMotion()
            store.setNavigationDemand("workspace", nil)
        }
        .onChange(of: workspaceWindows) { _, demand in if visible { store.setNavigationDemand("workspace", demand) } }
        .onChange(of: store.view?.contentVersion) { _, _ in if visible { updateGroups() } }
        .onChange(of: goals) { _, _ in if visible { updateGroups() } }
        .onChange(of: showGoalRows) { _, _ in if visible { updateGroups() } }
        .onChange(of: held) { _, hold in if visible, !hold { updateGroups() } }
        .onChange(of: reduceMotion) { _, reduced in
            if reduced { cancelMotion(); updateGroups() }
        }
        .onChange(of: store.baseURL) { _, _ in
            cancelMotion()
            groups = [:]; orderedGroups = [:]; managers = []; settled = []; windows = []; pages = [:]; hasPublished = false; retainedGoalRows = [:]; goalRowsEnabled = false
            goalsHandle = nil; machinesHandle = nil; healthHandles = [:]
            settledError = nil; settledOpen = false
        }
        .task(id: "\(store.baseURL):\(visible):\(scenePhase == .active)") { await followBodies() }
        .task(id: "\(store.baseURL):\(visible):\(scenePhase == .active):\(machineIDs)") { await followMachineHealth() }
    }

    private var activeWork: some View {
        ScrollView {
            LazyVStack(spacing: PanelsTheme.Web.Rail.gap) {
                if hasContent {
                    card(title: store.chief?.label ?? "Chief of Staff", chief: true, owner: .chief,
                         route: .chief, signal: store.chiefSignal)
                    ForEach(managers) { manager in
                        card(title: manager.title, project: manager.project, owner: .manager(manager.id),
                             route: .orchestrator(manager.id), signal: manager.conversationSignal)
                    }
                    pagesControl(window("rail_parents")) { anchor, direction in page("rail_parents", anchor: anchor, direction: direction) }
                }
            }
            .padding(.horizontal, PanelsTheme.Web.Rail.inset).padding(.top, PanelsTheme.Web.Rail.gap)
            .padding(.bottom, PanelsTheme.Web.Rail.gap)
        }
        .simultaneousGesture(LongPressGesture(minimumDuration: 60, maximumDistance: 10_000)
            .updating($fingerDown) { _, down, _ in down = true })
        .onScrollPhaseChange { _, phase in scrolling = phase != .idle }
        .panelsRefreshable { await refresh() }
        .scrollContentBackground(.hidden).textCase(nil)
    }

    private func card(title: String, project: String? = nil, chief: Bool = false, owner: ViewOwner,
                      route: AppRoute, signal: ConversationSignal?) -> some View {
        WorkspaceCard(title: title, project: project, chief: chief, route: route, signal: signal,
                      groups: groups[owner.id] ?? [], goals: goals,
                      ticketWindow: window("rail_tickets", owner: owner),
                      onTicketPage: { anchor, direction in page("rail_tickets", owner: owner, anchor: anchor, direction: direction) },
                      goalWindow: window("goals", owner: owner),
                      onGoalPage: { anchor, direction in page("goals", owner: owner, anchor: anchor, direction: direction) },
                      goalRows: retainedGoalRows[owner.id] ?? [], goalRowsEnabled: goalRowsEnabled,
                      machines: machines, machineHealth: machineHealth, railNamespace: railNamespace,
                      railExitingIDs: exitingIDs, railEnteringIDs: enteringIDs, railEnterVisible: enterVisible,
                      previewChanged: { previewOpen = $0 }, open: { selectedRoute = $0 })
    }

    private func goalRows(_ owner: ViewOwner, in snapshot: ViewSnapshot) -> [GoalRecord] {
        guard showGoalRows else { return [] }
        let holder = owner.kind == "chief" ? GoalHolder.chief : GoalHolder.manager(owner.id)
        let held = holder.goals(in: goals)
        if owner.kind == "manager", held.count == 1 { return held }
        let byID = Dictionary(goals.map { ($0.id, $0) }, uniquingKeysWith: { _, latest in latest })
        return snapshot.content.windows.first { $0.request.section == "goals" && $0.request.owner == owner }?.members.compactMap { byID[$0.id] } ?? []
    }

    private var settledDock: some View {
        VStack(spacing: 0) {
            Button {
                Haptics.selection()
                withAnimation(reduceMotion ? nil : .timingCurve(0.4, 0.02, 0.2, 1, duration: PanelsTheme.Web.Rail.Motion.space)) {
                    settledOpen.toggle()
                }
            } label: {
                HStack(spacing: 8) {
                    Text("Settled")
                    if let count = store.counts?.settled { Text(String(count)).panelsScaledFont(size: PanelsTheme.Web.smallSize).foregroundStyle(PanelsTheme.Web.tertiary) }
                    Spacer()
                    Image(systemName: "chevron.right").font(.system(size: 14))
                        .foregroundStyle(PanelsTheme.Web.tertiary).rotationEffect(.degrees(settledOpen ? 90 : 0))
                }.panelsScaledFont(size: PanelsTheme.Web.rowSize).padding(.horizontal, PanelsTheme.Web.Rail.textInset)
                    .frame(minHeight: PanelsTheme.Web.Rail.settledHeight).contentShape(Rectangle())
            }.buttonStyle(WorkspaceRailButtonStyle(header: true))
                .accessibilityIdentifier("workspace-settled")
                .accessibilityValue(settledOpen ? "Expanded" : "Collapsed")
            if settledOpen {
                ScrollView {
                    VStack(spacing: 0) {
                        ForEach(settled) { manager in
                            settledRow(manager)
                                .opacity(exitingIDs.contains("settled:\(manager.id)") || (enteringIDs.contains("settled:\(manager.id)") && !enterVisible) ? 0 : 1)
                                .offset(y: enteringIDs.contains("settled:\(manager.id)") && !enterVisible ? -4 : 0)
                                .animation(reduceMotion ? nil : PanelsTheme.Web.Rail.Motion.leave, value: exitingIDs)
                                .animation(reduceMotion ? nil : PanelsTheme.Web.Rail.Motion.enter.delay(PanelsTheme.Web.Rail.Motion.fadeDelay), value: enterVisible)
                        }
                        if window("settled") == nil { RingSpinner(size: .compact).frame(maxWidth: .infinity) }
                        pagesControl(window("settled")) { anchor, direction in page("settled", anchor: anchor, direction: direction) }
                        if let settledError { Text(settledError).panelsScaledFont(size: PanelsTheme.Web.smallSize).foregroundStyle(PanelsTheme.Web.tertiary) }
                    }
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { settledContentHeight = $0 }
                }.frame(height: min(settledContentHeight, max(0, viewportHeight * 0.5 - PanelsTheme.Web.Rail.settledHeight - 2 * PanelsTheme.Web.Rail.padding)))
                    .onScrollPhaseChange { _, phase in scrolling = phase != .idle }
            }
        }
        .padding(PanelsTheme.Web.Rail.padding)
        .background(PanelsTheme.Web.rail, in: RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius))
        .padding(.horizontal, PanelsTheme.Web.Rail.inset)
        .fixedSize(horizontal: false, vertical: !settledOpen)
    }

    private func settledRow(_ manager: ManagerSummary) -> some View {
        HStack(spacing: 8) {
            NavigationLink(value: AppRoute.orchestrator(manager.id)) {
                HStack {
                    Text(manager.title).lineLimit(typeSize.isAccessibilitySize ? nil : 1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    ConversationSignalMark(signal: manager.conversationSignal)
                }.contentShape(Rectangle())
            }.buttonStyle(WorkspaceRailButtonStyle(resting: manager.conversationSignal?.messageForOwner == true ? PanelsTheme.Web.strong : PanelsTheme.Web.railRow))
                .conversationAccessibility(label: manager.title, signal: manager.conversationSignal)
            Button("Reopen") { reopen(manager.id) }
                .panelsScaledFont(size: PanelsTheme.Web.smallSize).foregroundStyle(PanelsTheme.Web.tertiary)
                .disabled(settledBusy != nil).accessibilityLabel("Reopen \(manager.title)")
        }.panelsScaledFont(size: PanelsTheme.Web.rowSize)
            .padding(.horizontal, PanelsTheme.Web.Rail.textInset)
            .frame(minHeight: PanelsTheme.Web.Rail.settledRowHeight)
    }

    @ViewBuilder private func pagesControl(_ window: ViewWindow?, onPage: @escaping (String, String) -> Void) -> some View {
        if let window, window.previous != nil || window.next != nil {
            HStack {
                Button("Previous") { if let anchor = window.previous { onPage(anchor, "before") } }.disabled(window.previous == nil)
                Spacer()
                Button("Next") { if let anchor = window.next { onPage(anchor, "after") } }.disabled(window.next == nil)
            }.panelsScaledFont(size: PanelsTheme.Web.smallSize)
                .frame(minHeight: 44).padding(.horizontal, PanelsTheme.Web.Rail.textInset)
        }
    }

    private func page(_ section: String, owner: ViewOwner? = nil, anchor: String, direction: String) {
        pages[request(section, owner: owner).identity] = (anchor, direction)
    }

    private struct Publication {
        let ordered: [String: [WorkspaceTicketGroup]]
        let groups: [String: [WorkspaceTicketGroup]]
        let managers: [ManagerSummary]
        let settled: [ManagerSummary]
        let windows: [ViewWindow]
        let goalRows: [String: [GoalRecord]]
        let goalRowsEnabled: Bool
    }

    private func nextPublication() -> Publication? {
        guard let snapshot = store.workspaceView else { return nil }
        let changedOwners = Set(snapshot.content.windows.filter { next in
            guard next.request.section == "rail_tickets" else { return false }
            let previous = windows.first { $0.request.identity == next.request.identity }?.request
            return previous?.anchor != next.request.anchor || previous?.direction != next.request.direction
        }.compactMap { $0.request.owner?.id })
        let byOwner = Dictionary(grouping: store.tickets) { $0.manager?.kind == "chief" ? "chief" : $0.managerId ?? "chief" }
        var ordered: [String: [WorkspaceTicketGroup]] = [:]
        for (owner, tickets) in byOwner {
            ordered[owner] = WorkspaceGrouping.groups(tickets, previous: changedOwners.contains(owner) ? [] : orderedGroups[owner] ?? [])
        }
        let managers = store.managers.sorted { $0.createdAt == $1.createdAt ? $0.id < $1.id : $0.createdAt < $1.createdAt }
        let owners: [ViewOwner] = [.chief] + managers.map { .manager($0.id) }
        let rows = Dictionary(uniqueKeysWithValues: owners.map { ($0.id, goalRows($0, in: snapshot)) })
        return Publication(ordered: ordered, groups: ordered.mapValues(WorkspaceGrouping.focused), managers: managers,
                           settled: snapshot.window("settled") == nil ? settled : store.workspaceSettled, windows: snapshot.content.windows,
                           goalRows: rows, goalRowsEnabled: showGoalRows)
    }

    private var currentIDs: [String] { structuralIDs(groups, managers, goalRows: retainedGoalRows) + (settledOpen ? settled.map { "settled:\($0.id)" } : []) }
    private func ids(_ next: Publication) -> [String] {
        structuralIDs(next.groups, next.managers, goalRows: next.goalRows) + (settledOpen ? next.settled.map { "settled:\($0.id)" } : [])
    }
    private func publish(_ next: Publication) {
        orderedGroups = next.ordered; groups = next.groups; managers = next.managers
        settled = next.settled; windows = next.windows; hasPublished = true
        retainedGoalRows = next.goalRows; goalRowsEnabled = next.goalRowsEnabled
    }
    private func cancelMotion() {
        motionTask?.cancel(); motionTask = nil
        exitingIDs = []; enteringIDs = []; enterVisible = true; motionActive = false
    }

    private func updateGroups() {
        refreshSignals()
        guard !held, let next = nextPublication() else { return }
        let oldIDs = currentIDs, newIDs = ids(next)
        guard oldIDs != newIDs, hasPublished, !reduceMotion else { publish(next); return }
        motionActive = true
        exitingIDs = Set(oldIDs).subtracting(newIDs)
        motionTask = Task { @MainActor in
            do {
                if !exitingIDs.isEmpty { try await Task.sleep(for: .seconds(PanelsTheme.Web.Rail.Motion.exit)) }
                guard !Task.isCancelled else { return }
                // Preserve old space throughout the exit; a gesture gets the whole publication back.
                if fingerDown || scrolling || previewOpen { cancelMotion(); return }
                guard let latest = nextPublication() else { cancelMotion(); return }
                let destinationIDs = ids(latest)
                let duration = motionDuration(oldIDs: oldIDs, newIDs: destinationIDs)
                enteringIDs = Set(destinationIDs).subtracting(oldIDs); enterVisible = false
                exitingIDs = []
                withAnimation(PanelsTheme.Web.Rail.Motion.glide(duration: duration)) { publish(latest) }
                await Task.yield()
                guard !Task.isCancelled else { return }
                enterVisible = true
                let completion = enteringIDs.isEmpty ? duration : max(duration, PanelsTheme.Web.Rail.Motion.fadeDelay + PanelsTheme.Web.Rail.Motion.fadeIn)
                try await Task.sleep(for: .seconds(completion))
                enteringIDs = []; motionActive = false; motionTask = nil
            } catch { }
        }
    }

    private func refreshSignals() {
        let freshTickets = Dictionary(store.tickets.map { ($0.id, $0) }, uniquingKeysWith: { _, latest in latest })
        groups = groups.mapValues { groups in
            groups.map { group in
                WorkspaceTicketGroup(section: group.section, tickets: group.tickets.map { old in
                    var row = old
                    row.conversationSignal = freshTickets[row.id]?.conversationSignal
                    return row
                })
            }
        }
        let freshManagers = Dictionary(store.managers.map { ($0.id, $0) }, uniquingKeysWith: { _, latest in latest })
        managers = managers.map { old in refreshed(old, signal: freshManagers[old.id]?.conversationSignal) }
        let freshSettled = Dictionary(store.workspaceSettled.map { ($0.id, $0) }, uniquingKeysWith: { _, latest in latest })
        settled = settled.map { old in refreshed(old, signal: freshSettled[old.id]?.conversationSignal) }
    }

    private func refreshed(_ manager: ManagerSummary, signal: ConversationSignal?) -> ManagerSummary {
        ManagerSummary(id: manager.id, title: manager.title, projectId: manager.projectId, project: manager.project,
                       settled: manager.settled, createdAt: manager.createdAt, updatedAt: manager.updatedAt,
                       awaitingReply: manager.awaitingReply, awaitingAnswer: manager.awaitingAnswer,
                       awaitingApproval: manager.awaitingApproval, awaitingAgentApproval: manager.awaitingAgentApproval,
                       agentState: manager.agentState, conversationSignal: signal)
    }

    private func structuralIDs(_ groups: [String: [WorkspaceTicketGroup]], _ managers: [ManagerSummary], goalRows: [String: [GoalRecord]]) -> [String] {
        (["chief"] + managers.map(\.id)).flatMap { owner in
            let goalIDs = (goalRows[owner] ?? []).map { "goal:\($0.id)" }
            let ticketIDs = (groups[owner] ?? []).flatMap { group in
                ["heading:\(owner):\(group.section.rawValue)"] + group.tickets.map { "ticket:\($0.id)" }
            }
            return ["card:\(owner)"] + goalIDs + ticketIDs
        }
    }

    private func motionDuration(oldIDs: [String], newIDs: [String]) -> Double {
        guard Set(oldIDs) == Set(newIDs) else { return PanelsTheme.Web.Rail.Motion.space }
        var distance: CGFloat = 0
        for (index, id) in newIDs.enumerated() where oldIDs.indices.contains(index) {
            if let from = frames[id], let target = frames[oldIDs[index]] { distance = max(distance, abs(from.minY - target.minY)) }
        }
        return PanelsTheme.Web.Rail.Motion.moveDuration(distance: distance)
    }

    private func reopen(_ id: String) {
        guard settledBusy == nil else { return }
        settledBusy = id; settledError = nil
        Task {
            defer { settledBusy = nil }
            do {
                try await store.client.workspaceAction(.reopen, route: .orchestrator(id))
                Haptics.success(); await store.refresh()
            } catch { Haptics.error(); settledError = error.localizedDescription }
        }
    }

    private func refresh() async {
        await store.refresh()
        await goalsHandle?.refresh()
        await machinesHandle?.refresh()
        for handle in healthHandles.values { await handle.refresh() }
    }

    private func followBodies() async {
        guard visible, scenePhase == .active else { return }
        let goals = store.resources.handle(GoalResources.list)
        let machines = store.resources.handle(WorkspaceResources.machines)
        goalsHandle = goals; machinesHandle = machines
        goals.acquire(); machines.acquire()
        defer { goals.release(); machines.release() }
        await goals.refresh(); await machines.refresh()
        do {
            while !Task.isCancelled {
                try await Task.sleep(for: .seconds(PanelsTheme.Web.Rail.machineRefreshInterval))
                await machines.refresh()
            }
        } catch { }
    }

    private func followMachineHealth() async {
        guard visible, scenePhase == .active else { return }
        let handles = Dictionary(uniqueKeysWithValues: machineIDs.map { ($0, store.resources.handle(WorkspaceResources.machineHealth($0))) })
        healthHandles = handles
        for handle in handles.values { handle.acquire() }
        defer { for handle in handles.values { handle.release() } }
        do {
            while !Task.isCancelled {
                for handle in handles.values { await handle.refresh() }
                try await Task.sleep(for: .seconds(PanelsTheme.Web.Rail.machineRefreshInterval))
            }
        } catch { }
    }
}
