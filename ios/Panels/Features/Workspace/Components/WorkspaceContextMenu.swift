import SwiftUI

/// Long press replaces the web hover without placing controls in a snapshot preview.
struct WorkspaceContextMenu: ViewModifier {
    let route: AppRoute
    let title: String
    let parent: String?
    let ticket: TicketSummary?
    let signal: ConversationSignal?
    var goals: [GoalRecord] = []
    var machines: [WorkspaceMachine] = []
    var machineHealth: [String: WorkspaceMachineHealth] = [:]
    var resting = PanelsTheme.Web.railRow
    var header = false
    let previewChanged: (Bool) -> Void
    let open: (AppRoute) -> Void
    @Environment(AppStore.self) private var store
    @State private var error: String?
    @State private var sending = false
    @State private var showingPreview = false
    @State private var destination: AppRoute?

    func body(content: Content) -> some View {
        content
            .buttonStyle(WorkspaceRailButtonStyle(resting: resting, header: header))
            .highPriorityGesture(LongPressGesture().onEnded { _ in showingPreview = true })
            .accessibilityActions {
                Button("Preview") { showingPreview = true }
                Button("Ask for a recap") { perform(.recap) }.disabled(sending || ticket?.integrityError != nil)
                if let ticket, ticket.integrityError == nil {
                    Button(ticket.isBacklogged ? "Resume ticket" : "Backlog ticket") { perform(ticket.isBacklogged ? .resume : .backlog) }.disabled(sending)
                } else if case .orchestrator = route {
                    Button("Settle") { perform(.settle) }.disabled(sending)
                }
            }
            .sheet(isPresented: $showingPreview, onDismiss: {
                previewChanged(false)
                if let destination { self.destination = nil; open(destination) }
            }) {
                NavigationStack {
                    ScrollView {
                        VStack(alignment: .leading, spacing: 16) {
                            WorkspacePreview(route: route, title: title, parent: parent, ticket: ticket,
                                signal: signal, goals: goals, machines: machines, machineHealth: machineHealth,
                                sending: sending, requestRecap: { perform(.recap) }, open: navigate)
                            if let ticket, ticket.integrityError == nil {
                                Button(ticket.isBacklogged ? "Resume ticket" : "Backlog ticket", systemImage: "tray") {
                                    perform(ticket.isBacklogged ? .resume : .backlog)
                                }.disabled(sending).buttonStyle(.bordered)
                            } else if case .orchestrator = route {
                                Button("Settle", systemImage: "checkmark") { perform(.settle) }.disabled(sending).buttonStyle(.bordered)
                            }
                            if let error { Text(error).panelsScaledFont(size: PanelsTheme.Web.smallSize).foregroundStyle(PanelsTheme.Web.error) }
                        }.padding(PanelsTheme.Web.Rail.previewPadding)
                    }.background(PanelsTheme.Web.rail)
                        .toolbar {
                            ToolbarItem(placement: .cancellationAction) { Button("Done") { showingPreview = false } }
                            ToolbarItem(placement: .primaryAction) { Button("Open") { navigate(route) } }
                        }
                }.presentationDetents([.medium, .large]).presentationDragIndicator(.visible)
                    .onAppear { previewChanged(true) }
            }
    }

    private func navigate(_ destination: AppRoute) { self.destination = destination; showingPreview = false }
    private func perform(_ action: WorkspaceAction) {
        guard !sending else { return }
        sending = true; error = nil
        Task {
            defer { sending = false }
            do {
                try await store.client.workspaceAction(action, route: route)
                Haptics.success()
                await store.refresh()
            } catch {
                Haptics.error()
                self.error = error.localizedDescription
            }
        }
    }
}

private struct WorkspacePreview: View {
    let route: AppRoute
    let title: String
    let parent: String?
    let ticket: TicketSummary?
    let signal: ConversationSignal?
    let goals: [GoalRecord]
    let machines: [WorkspaceMachine]
    let machineHealth: [String: WorkspaceMachineHealth]
    let sending: Bool
    let requestRecap: () -> Void
    let open: (AppRoute) -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @State private var recap: String?
    @State private var stageLine = ""
    @State private var loadError: String?
    @State private var configurationError: String?
    @State private var conversationID: String?
    @State private var resolvedConversation = false
    @State private var configuration: ConversationConfiguration?
    @State private var startValues: ConversationConfiguration?
    @State private var backends: [TicketBackend] = []
    @State private var status: ConversationStatus?
    @State private var tailFailed = false
    @State private var refreshVersion = 0
    private var owner: ConversationOwner {
        switch route { case .ticket(let id): .ticket(id); case .orchestrator(let id): .manager(id); default: .chief }
    }
    private var holder: GoalHolder? {
        switch route { case .chief: .chief; case .orchestrator(let id): .manager(id); default: nil }
    }
    private var machine: WorkspaceMachine? { ticket.flatMap { WorkspaceMachinePresentation.machine(for: $0, in: machines) } }
    private var health: WorkspaceMachineHealth? { ticket?.machineId.flatMap { machineHealth[$0] } }
    private var unknownMac: Bool { ticket?.locale == "macos" && health?.activityKnown != true }
    private var currentStage: String {
        if unknownMac {
            return ticket.flatMap { health?.activityLabel(for: $0) } ?? macPresenceLabel
                ?? "Current activity on this machine is unknown. Last progress remains available."
        }
        return stageLine
    }
    private var macPresenceLabel: String? {
        let since = health?.sinceMs.map { milliseconds in
            let date = Date(timeIntervalSince1970: milliseconds / 1_000)
            let formatter = DateFormatter()
            formatter.locale = Locale(identifier: "en_GB")
            formatter.dateFormat = Calendar.current.isDateInToday(date) ? "HH:mm" : "d MMM, HH:mm"
            return " since " + formatter.string(from: date)
        } ?? ""
        switch health?.presence {
        case "asleep": return "Asleep" + since
        case "not_heard_from": return "Not heard from" + since
        case "never": return "The Mac has not connected yet."
        case "refused": return health?.refusal?.reason
        default: return nil
        }
    }
    private var activity: String? {
        if unknownMac { return "Activity unknown on Mac" }
        if !resolvedConversation { return nil }
        if conversationID == nil { return "Idle" }
        guard let status else { return tailFailed ? "Activity unknown" : nil }
        guard !tailFailed, status.authority == "current" else { return "Activity unknown" }
        if status.pendingPermission != nil || status.pendingUserInput != nil { return "Waiting for you" }
        return status.turnRunning == true || status.hasBackgroundTaskAfterTheTurn ? "Working" : "Idle"
    }
    private var recapStamp: String {
        switch route { case .ticket(let id): store.ticketStamp(id) ?? ""; case .orchestrator(let id): store.managerStamp(id) ?? ""; default: store.chiefStamp() ?? "" }
    }
    private var modelLabel: String {
        guard let configuration else { return "" }
        let backend = backends.first { $0.backendKey == configuration.backendKey }
        let model = configuration.model ?? startValues?.model ?? backend?.defaultModelId ?? ""
        let label = backend?.modelName(model) ?? model
        let effort = backend?.offersReasoning == false ? "" : configuration.reasoningEffort ?? startValues?.reasoningEffort ?? ""
        return [label, effort].filter { !$0.isEmpty }.joined(separator: " ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if let ticket { WorkspaceMachineIdentity(machine: machine, locale: ticket.locale, machineID: ticket.machineId, showName: true) }
                Text(modelLabel).lineLimit(1).frame(maxWidth: .infinity, alignment: .leading)
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Text(activityLabel(at: context.date)).monospacedDigit()
                }
            }.panelsScaledFont(size: PanelsTheme.Web.smallSize, relativeTo: .caption).foregroundStyle(PanelsTheme.Web.tertiary)
            Text(title).panelsScaledFont(size: PanelsTheme.Web.Rail.titleSize, relativeTo: .title3, weight: .semibold)
                .fixedSize(horizontal: false, vertical: true).foregroundStyle(PanelsTheme.Web.strong)
            if !currentStage.isEmpty { Text(currentStage).panelsScaledFont(size: PanelsTheme.Web.Rail.previewRecapSize).foregroundStyle(PanelsTheme.Web.secondary) }
            if let holder, !holder.goals(in: goals).isEmpty {
                WorkspaceHolderGoalPreview(holder: holder, goals: goals, open: open)
            }
            VStack(alignment: .leading, spacing: 8) {
                if let recap, !recap.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    Text(recap).panelsScaledFont(size: PanelsTheme.Web.Rail.previewRecapSize).lineLimit(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                } else if recap == nil && loadError == nil { RingSpinner() }
                Button("Ask for a recap", action: requestRecap).buttonStyle(.plain)
                    .panelsScaledFont(size: PanelsTheme.Web.smallSize).foregroundStyle(PanelsTheme.Web.secondary)
                    .disabled(sending || ticket?.integrityError != nil)
            }.padding(PanelsTheme.Web.Rail.previewPadding).frame(maxWidth: .infinity, alignment: .leading)
                .background(PanelsTheme.Web.surface, in: RoundedRectangle(cornerRadius: PanelsTheme.Web.cardRadius))
            if let error = loadError ?? configurationError {
                LoadErrorView(message: error) { refreshVersion += 1 }
            } else if tailFailed {
                LoadErrorView(message: "Preview could not refresh.") { refreshVersion += 1 }
            }
        }
        .foregroundStyle(PanelsTheme.Web.text)
        .task(id: "recap|\(store.baseURL)|\(String(describing: route))|\(recapStamp)|\(refreshVersion)") { await loadRecap() }
        .task(id: "model|\(store.baseURL)|\(conversationID ?? "")|\(resolvedConversation)|\(refreshVersion)") {
            guard resolvedConversation else { return }; await loadConfiguration()
        }
        .task(id: "tail|\(store.baseURL)|\(conversationID ?? "")|\(scenePhase == .active)|\(refreshVersion)") {
            guard scenePhase == .active, let conversationID else { return }; await follow(conversationID)
        }
    }

    private func activityLabel(at now: Date) -> String {
        guard activity == "Working", !unknownMac, status?.authority == "current", status?.turnRunning == true,
              let started = status?.rest.latestTurn?.startedAtUnixMilliseconds else { return activity ?? "" }
        let seconds = max(0, Int(now.timeIntervalSince1970 - Double(started) / 1000))
        let elapsed = seconds < 60 ? "\(seconds)s" : seconds < 3600 ? "\(seconds / 60)m \(seconds % 60)s" : "\(seconds / 3600)h \((seconds % 3600) / 60)m"
        return "Working · " + elapsed
    }
    private func loadRecap() async {
        let generation = store.resources.admissionGeneration
        loadError = nil
        if recap == nil { recap = ticket?.recapPreview }
        do {
            let nextRecap: String
            switch route {
            case .chief: nextRecap = store.chief?.recap ?? ""
            case .orchestrator(let id): nextRecap = try await store.resources.read(ManagerResources.content(id)).recap
            case .ticket(let id):
                let detail = try await store.resources.read(TicketResources.detail(id)).detail
                nextRecap = detail.recap
                if let ticket, let label = detail.workflow?.stages.first(where: { $0.id == ticket.stage })?.label {
                    stageLine = stageSentence(ticket, label: label)
                }
            default: return
            }
            let current: String?
            if let canonical = store.currentConversationID(owner) { current = canonical }
            else { current = try await store.client.currentConversation(owner) }
            guard !Task.isCancelled, generation == store.resources.admissionGeneration else { return }
            if conversationID != current { configuration = nil; startValues = nil; status = nil }
            recap = nextRecap; conversationID = current; resolvedConversation = true
        } catch {
            guard !Task.isCancelled, generation == store.resources.admissionGeneration else { return }
            loadError = "Preview could not refresh. \(error.localizedDescription)"
        }
    }
    private func loadConfiguration() async {
        let generation = store.resources.admissionGeneration
        configurationError = nil
        do {
            async let values = store.client.conversationConfiguration(owner, id: conversationID)
            async let catalogue = store.client.conversationBackends(owner: owner)
            let next = try await values
            let models = try await catalogue.backends
            let start = conversationID != nil && (next.model == nil || next.reasoningEffort == nil)
                ? try await store.client.conversationConfiguration(owner, id: nil) : nil
            guard !Task.isCancelled, generation == store.resources.admissionGeneration else { return }
            configuration = next; startValues = start; backends = models
        } catch {
            guard !Task.isCancelled, generation == store.resources.admissionGeneration else { return }
            configurationError = "Preview could not refresh. \(error.localizedDescription)"
        }
    }
    private func follow(_ id: String) async {
        let generation = store.resources.admissionGeneration
        tailFailed = false
        do {
            let initial = try await store.client.conversationSnapshot(id, lens: .full, transcript: false)
            guard !Task.isCancelled, generation == store.resources.admissionGeneration else { return }
            status = initial.status
            var coverage = initial.coverage?.throughSequence
            while !Task.isCancelled {
                var reset = false
                for try await frame in store.client.conversationTail(id, lens: .full, transcript: false) {
                    guard !Task.isCancelled, generation == store.resources.admissionGeneration else { return }
                    var next: ConversationStatus?
                    switch frame {
                    case .snapshot(let snapshot): next = snapshot.status; coverage = snapshot.coverage?.throughSequence
                    case .changes(let changes):
                        guard let previous = coverage, changes.fromSequence == previous, changes.lens == "full",
                              changes.status.stoppedWithoutEnding == status?.stoppedWithoutEnding,
                              changes.status.runtimeEpoch == status?.runtimeEpoch else { reset = true; break }
                        next = changes.status
                        coverage = changes.coverage.throughSequence
                    case .reset: reset = true; break
                    }
                    guard !reset, let next else { break }
                    let changedTurn = status?.rest.latestTurn?.startedAtUnixMilliseconds != next.rest.latestTurn?.startedAtUnixMilliseconds
                    status = next
                    if changedTurn { await loadConfiguration() }
                }
                if !reset { tailFailed = true; return }
            }
        } catch {
            guard !Task.isCancelled, generation == store.resources.admissionGeneration else { return }
            tailFailed = true
        }
    }
    private func stageSentence(_ ticket: TicketSummary, label: String) -> String {
        if ticket.blocked == true || ticket.ticketStatus == "blocked" { return "Blocked at \(label)." }
        if ticket.awaitingApproval { return "Awaiting your approval at \(label)." }
        if ticket.awaitingAnswer { return "Awaiting your answer at \(label)." }
        if ticket.assigned { return "Awaiting you at \(label)." }
        if ticket.agentState == "errored" { return "The last turn failed at \(label)." }
        if ticket.awaitingAgentApproval { return "Awaiting an agent's approval at \(label)." }
        if ticket.conversationSignal?.working == true { return "Currently doing \(label)." }
        return "At \(label)."
    }
}
