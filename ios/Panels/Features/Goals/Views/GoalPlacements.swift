import SwiftUI

/// Workspace keeps the web's optional, bounded, one-line goal rows.
struct WorkspaceGoals: View {
    let holder: GoalHolder
    let goals: [GoalRecord]
    var rows: [GoalRecord]? = nil
    var window: ViewWindow? = nil
    var onPage: (String, String) -> Void = { _, _ in }
    var railNamespace: Namespace.ID? = nil
    var railExitingIDs: Set<String> = []
    var railEnteringIDs: Set<String> = []
    var railEnterVisible = true
    var previewChanged: (Bool) -> Void = { _ in }
    let open: (AppRoute) -> Void
    @Environment(AppStore.self) private var store
    private var visibleGoals: [GoalRecord] { rows ?? holder.goals(in: goals).filter(\.active) }
    private var sessions: PageSessionCollection { store.goalEdits.goals }
    var body: some View {
        VStack(spacing: 0) {
            ForEach(visibleGoals) { goal in
                if let session = sessions.session(goal.id, server: store.baseURL) {
                    GoalWorkspaceRow(goal: goal, session: session, previewChanged: previewChanged, open: open)
                        .modifier(WorkspaceRailGeometry(id: "goal:\(goal.id)", namespace: railNamespace, exiting: railExitingIDs, entering: railEnteringIDs, enterVisible: railEnterVisible))
                }
            }
            WorkspaceWindowPages(window: window, label: "Goals", onPage: onPage)
                .padding(.horizontal, PanelsTheme.Web.Rail.textInset)
        }.task(id: "\(store.baseURL):\(visibleGoals.map(\.id))") {
            sessions.update(visibleGoals, api: store.client, resources: store.resources)
        }.onChange(of: sessions.unfinishedKeys) { _, _ in sessions.prune() }
    }
}

private struct GoalWorkspaceRow: View {
    let goal: GoalRecord
    let session: PageSession
    var previewChanged: (Bool) -> Void = { _ in }
    var previewTone = false
    let open: (AppRoute) -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var typeSize
    @State private var showingPreview = false
    @State private var destination: AppRoute?
    private var accepted: GoalRecord { session.goal ?? goal }
    var body: some View {
        Button { open(.goal(goal.id)) } label: {
            HStack(spacing: 12) {
                Text(accepted.title).panelsScaledFont(size: PanelsTheme.Web.Rail.goalSize)
                    .lineLimit(typeSize.isAccessibilitySize ? nil : 1).truncationMode(.tail)
                    .frame(maxWidth: .infinity, alignment: .leading)
                WorkspaceGoalRowState(goal: accepted, readings: session.readings, preview: previewTone)
                    .fixedSize(horizontal: true, vertical: false)
            }.foregroundStyle(PanelsTheme.Web.railRow)
                .frame(maxWidth: .infinity, minHeight: PanelsTheme.Web.Rail.goalRowHeight, alignment: .leading)
                .padding(.horizontal, PanelsTheme.Web.Rail.textInset).contentShape(Rectangle())
        }.buttonStyle(WorkspaceRailButtonStyle())
            .highPriorityGesture(LongPressGesture().onEnded { _ in showingPreview = true })
            .accessibilityIdentifier("workspace-goal-\(goal.id)")
            .accessibilityAction(named: "Preview") { showingPreview = true }
            .sheet(isPresented: $showingPreview, onDismiss: {
                previewChanged(false)
                if let destination { self.destination = nil; open(destination) }
            }) {
                NavigationStack {
                    ScrollView { GoalPeek(session: session).padding(PanelsTheme.Web.Rail.previewPadding) }
                        .background(PanelsTheme.Web.rail)
                        .toolbar {
                            ToolbarItem(placement: .cancellationAction) { Button("Done") { showingPreview = false } }
                            ToolbarItem(placement: .primaryAction) { Button("Open") { destination = .goal(goal.id); showingPreview = false } }
                        }
                }.presentationDetents([.medium, .large]).presentationDragIndicator(.visible)
                    .onAppear { previewChanged(true) }
            }
            .task(id: store.baseURL) { await session.follow() }
    }
}

/// GoalRowState.svelte chooses exactly one visible state, in this order.
private struct WorkspaceGoalRowState: View {
    let goal: GoalRecord
    let readings: GoalReadings?
    var preview = false
    private var chart: GoalJSON? { GoalPresentation.primaryChart(goal) }
    private var headline: GoalChartHeadline? {
        guard let chart, goal.kind != "binary" else { return nil }
        let projection = readings?.charts[chart.id] ?? .null
        let data = GoalChartPresentation.data(chart, values: readings?.values ?? [], range: "all", projection: projection)
        return GoalChartPresentation.headline(chart, data: data, projection: projection)
    }
    private var readingTone: String {
        if goal.kind == "binary" { return goal.status }
        guard let chart, let headline else { return "neutral" }
        return GoalChartPresentation.tone(chart, headline: headline)
    }
    private var reading: String {
        if goal.kind == "binary" { return GoalPresentation.statusText(goal) }
        guard let chart, let headline, headline.value != nil else { return "No current reading" }
        if !chart["binding"].isNull && (headline.target == nil || headline.comparison == nil) {
            return readings?.charts[chart.id]?["category_summary"]["comparison"].isNull == false ? "Comparison unavailable" : "No target"
        }
        guard headline.target != nil else { return chart["expectation"].isNull ? "No target" : "Expected reading unavailable" }
        let within = (chart["direction"].string ?? (chart.kind == "time-budget" ? "atmost" : "atleast")) == "atmost"
        return readingTone == "good" ? (within ? "Within limit" : "On target") : (within ? "Above limit" : "Below target")
    }
    private var state: (String, Color) {
        if readingTone == "bad" { return (reading, PanelsTheme.Web.error) }
        if let checklist = GoalPresentation.primaryChecklist(goal), goal.status != "bad" {
            let items = checklist["items"].array
            return ("\(items.filter { $0["state"].text == "done" }.count) of \(items.count) done", PanelsTheme.Web.tertiary)
        }
        if let deadline = goal.deadline {
            return ("Due " + GoalPresentation.date(deadline), preview ? PanelsTheme.Web.tertiary : PanelsTheme.Web.goalTone(GoalPresentation.deadlineTone(deadline, completed: goal.completed, today: goal.statusDate)))
        }
        return (reading, preview || readingTone == "neutral" ? PanelsTheme.Web.tertiary : PanelsTheme.Web.goalTone(readingTone))
    }
    var body: some View {
        Text(state.0).panelsScaledFont(size: PanelsTheme.Web.smallSize, relativeTo: .caption)
            .foregroundStyle(state.1).monospacedDigit()
    }
}

/// Shared sheet content keeps real Goal controls interactive and keeps pending edits in AppStore.
private struct GoalPeek: View {
    let session: PageSession
    @Environment(AppStore.self) private var store
    @State private var project = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let goal = session.goal {
                HStack(spacing: 8) {
                    if !project.isEmpty { Text(project); Text("·") }
                    Text(goal.deadline.map { "Due " + GoalPresentation.date($0) } ?? "No deadline")
                        .foregroundStyle(PanelsTheme.Web.goalTone(GoalPresentation.deadlineTone(goal.deadline, completed: goal.completed, today: goal.statusDate)))
                }.panelsScaledFont(size: PanelsTheme.Web.smallSize, relativeTo: .caption).foregroundStyle(PanelsTheme.Web.tertiary)
                Text(goal.title).panelsScaledFont(size: PanelsTheme.Web.Rail.titleSize, relativeTo: .title3, weight: .semibold).foregroundStyle(PanelsTheme.Web.strong)
                WorkspaceGoalPrimary(session: session)
            }
        }.frame(maxWidth: .infinity, alignment: .leading)
            .task(id: store.baseURL) {
                let handle = store.resources.handle(GoalResources.projects)
                handle.acquire(); defer { handle.release() }; await handle.refresh()
                project = handle.value?["projects"].array.first { $0.id == session.goal?.projectId }?["name"].text ?? ""
                do { try await Task.sleep(for: .seconds(365 * 24 * 60 * 60)) } catch { }
            }
    }
}

private struct WorkspaceGoalPrimary: View {
    let session: PageSession
    var chartTitle: String? = nil
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let goal = session.goal {
                if let checklist = GoalPresentation.primaryChecklist(goal) {
                    GoalChecklist(block: checklist, session: session)
                } else if let chart = GoalPresentation.primaryChart(goal) {
                    GoalChart(block: chartTitle.map { chart.setting("title", .string($0)) } ?? chart, session: session, integrated: true)
                }
                GoalSaveFailure(session: session)
                if let error = session.readError { LoadErrorView(message: error) { await session.refresh() } }
            }
        }
    }
}

struct WorkspaceHolderGoalPreview: View {
    let holder: GoalHolder
    let goals: [GoalRecord]
    let open: (AppRoute) -> Void
    @Environment(AppStore.self) private var store
    private var visibleGoals: [GoalRecord] { holder.goals(in: goals).filter(\.active) }
    private var sessions: PageSessionCollection { store.goalEdits.goals }
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if visibleGoals.count == 1, let goal = visibleGoals.first,
               GoalPresentation.primaryChecklist(goal) != nil || GoalPresentation.primaryChart(goal) != nil,
               let session = sessions.session(goal.id, server: store.baseURL) {
                WorkspaceGoalPrimary(session: session, chartTitle: goal.title)
                    .task(id: store.baseURL) { await session.follow() }
            } else {
                ForEach(visibleGoals) { goal in
                    if let session = sessions.session(goal.id, server: store.baseURL) {
                        Button { open(.goal(goal.id)) } label: {
                            HStack(spacing: 12) {
                                Text((session.goal ?? goal).title).panelsScaledFont(size: PanelsTheme.Web.Rail.goalSize).lineLimit(1)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                WorkspaceGoalRowState(goal: session.goal ?? goal, readings: session.readings, preview: true)
                            }.frame(minHeight: PanelsTheme.Web.Rail.goalRowHeight).contentShape(Rectangle())
                        }.buttonStyle(WorkspaceRailButtonStyle()).task(id: store.baseURL) { await session.follow() }
                    }
                }
            }
        }.task(id: "\(store.baseURL):\(visibleGoals.map(\.id))") {
            sessions.update(visibleGoals, api: store.client, resources: store.resources)
        }.onChange(of: sessions.unfinishedKeys) { _, _ in sessions.prune() }
    }
}

/// Existing Chief and Manager pages share goals, authored blocks and Tickets.
struct HolderContent: View {
    let holder: GoalHolder
    let editOwner: PageSessionRoute
    @Environment(AppStore.self) private var store
    @State private var goalsHandle: ResourceHandle<GoalsResponse>?
    @State private var projectsHandle: ResourceHandle<GoalJSON>?
    private var goals: [GoalRecord] { goalsHandle?.value?.goals ?? [] }
    private var nextRefreshAt: Double? { goalsHandle?.value?.nextRefreshAt }
    private var projects: [GoalJSON] { projectsHandle?.value?["projects"].array ?? [] }
    private var sessions: PageSessionCollection { editOwner.goals }
    private var soleSession: PageSession? { sole.flatMap { sessions.session($0.id, server: store.baseURL) } }
    private var visibleGoals: [GoalRecord] { sole.map { [$0] } ?? held.filter(\.active) }
    @State private var error: String?
    @State private var pageSession: PageSession?
    private var holderStamp: String { (holder.kind == .chief ? store.chiefStamp() : store.managerStamp(holder.id ?? "")) ?? "" }
    private var held: [GoalRecord] { holder.goals(in: goals) }
    private var sole: GoalRecord? { holder.kind == .manager ? holder.soleGoal(in: goals) : nil }
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            if let sole, let soleSession {
                GoalDocumentContent(session: soleSession, showsTitle: false)
                    .environment(\.pageTicketHolder, holder)
                if !PageDocumentPresentation.hasTickets(sole.page["blocks"].array) { HolderTickets(holder: holder) }
            } else {
                HStack {
                    Text(holder.kind == .chief ? "Active goals" : "Goals").panelsScaledFont(size: 22, weight: .semibold).foregroundStyle(PanelsTheme.muted)
                    Spacer()
                    if holder.kind == .chief { NavigationLink("All goals", value: AppRoute.goals).panelsScaledFont(size: 13) }
                }
                ForEach(held.filter(\.active)) { goal in
                    if let session = sessions.session(goal.id, server: store.baseURL) {
                        ChiefGoalCard(goal: goal, project: projects.first { $0.id == goal.projectId }?["name"].string ?? "", session: session)
                    }
                }
                if let session = pageSession {
                    GoalPageBlocks(blocks: session.page?["blocks"].array ?? [], session: session)
                        .environment(\.pageTicketHolder, holder)
                    GoalSaveFailure(session: session)
                    if let error = session.readError { LoadErrorView(message: error) { await session.refresh() } }
                }
                if !PageDocumentPresentation.hasTickets(pageSession?.page?["blocks"].array ?? []) { HolderTickets(holder: holder) }
            }
            if let error { LoadErrorView(message: error) { await load() } }
        }.task(id: "\(store.baseURL):\(holder.pagePath)") { await follow() }
            .task(id: "\(store.baseURL):\(holderStamp)") { await pageSession?.refresh() }
            .task(id: "\(store.baseURL):\(visibleGoals.map(\.id))") {
                sessions.update(visibleGoals, api: store.client, resources: store.resources)
            }
            .onChange(of: sessions.unfinishedKeys) { _, _ in sessions.prune() }
            .modifier(PageArtifactNavigation(holder: holder))
    }
    private func follow() async {
        let list = store.resources.handle(GoalResources.list), projects = store.resources.handle(GoalResources.projects)
        goalsHandle = list; projectsHandle = projects
        let session = editOwner.holder(holder, api: store.client, resources: store.resources); pageSession = session
        list.acquire(); projects.acquire(); session.acquire()
        defer { list.release(); projects.release(); session.release() }
        await load()
        do { try await Task.sleep(for: .seconds(365 * 24 * 60 * 60)) } catch { }
    }
    private func load() async {
        async let list: () = goalsHandle?.refresh() ?? ()
        async let projects: () = projectsHandle?.refresh() ?? ()
        async let page: () = pageSession?.refresh() ?? ()
        _ = await (list, projects, page)
        error = goalsHandle?.error ?? projectsHandle?.error
    }

}

private struct PageTicketHolderKey: EnvironmentKey { static let defaultValue: GoalHolder? = nil }
extension EnvironmentValues {
    var pageTicketHolder: GoalHolder? {
        get { self[PageTicketHolderKey.self] }
        set { self[PageTicketHolderKey.self] = newValue }
    }
}
private struct ChiefGoalCard: View {
    let goal: GoalRecord
    let project: String
    let session: PageSession
    @Environment(AppStore.self) private var store
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            NavigationLink(value: AppRoute.goal(goal.id)) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack { Text(project); Text("·"); Text(goal.deadline.map { "By " + GoalPresentation.date($0) } ?? "No deadline").foregroundStyle(PanelsTheme.Goal.tone(GoalPresentation.deadlineTone(goal.deadline, completed: goal.completed, today: goal.statusDate))) }.font(.system(size: 12)).foregroundStyle(PanelsTheme.Goal.faint)
                    Text(goal.title).panelsScaledFont(size: 22, weight: .semibold).foregroundStyle(PanelsTheme.strong)
                }.frame(maxWidth: .infinity, alignment: .leading).padding(18).background(PanelsTheme.raised)
            }.buttonStyle(.plain)
            if let accepted = session.goal {
                VStack(alignment: .leading, spacing: 16) {
                    if let checklist = GoalPresentation.primaryChecklist(accepted) { GoalChecklist(block: checklist, session: session) }
                    else if let chart = GoalPresentation.primaryChart(accepted) { GoalChart(block: chart, session: session, integrated: true) }
                    GoalSaveFailure(session: session)
                }.padding(.horizontal, 20).padding(.vertical, 24)
            }
        }
            .background(PanelsTheme.surface).clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(PanelsTheme.border))
            .task(id: store.baseURL) { await session.follow() }
    }
}

/// Keep ordinary managed links in the native reader, with the document holder's conversation.
struct PageArtifactNavigation: ViewModifier {
    let holder: GoalHolder?
    @Environment(AppStore.self) private var store
    @State private var selectedArtifact: ArtifactTarget?
    @Environment(\.screenTextEntry) private var textEntry
    func body(content: Content) -> some View {
        content.environment(\.openURL, OpenURLAction { url in
            if ArtifactTarget.isRetiredGoalURL(url, baseURL: store.baseURL) { return .discarded }
            if let target = ArtifactTarget.from(url, baseURL: store.baseURL) {
                if let textEntry { textEntry.endEditingBeforeNavigation { selectedArtifact = target } }
                else { selectedArtifact = target }
                return .handled
            }
            return .systemAction
        }).navigationDestination(item: $selectedArtifact) { target in
            ArtifactViewerView(owner: target.owner, path: target.path, title: target.title, conversationOwner: holder?.conversationOwner)
        }
    }
}
