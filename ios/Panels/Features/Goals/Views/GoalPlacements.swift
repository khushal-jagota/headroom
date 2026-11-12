import SwiftUI

/// Workspace is the web sidebar. Each active goal belongs inside its holder card.
struct WorkspaceGoals: View {
    let holder: GoalHolder
    let goals: [GoalRecord]
    let open: (AppRoute) -> Void
    @Environment(AppStore.self) private var store
    @State private var sessions = PageSessionCollection()
    private var visibleGoals: [GoalRecord] { holder.goals(in: goals).filter(\.active) }
    var body: some View {
        VStack(spacing: 0) {
            ForEach(visibleGoals) { goal in
                if let session = sessions.session(goal.id, server: store.baseURL) { GoalWorkspaceRow(goal: goal, session: session, open: open) }
            }
        }.task(id: "\(store.baseURL):\(visibleGoals.map(\.id))") {
            sessions.update(visibleGoals, api: store.client, resources: store.resources)
        }.onChange(of: sessions.unfinishedKeys) { _, _ in sessions.prune() }
    }
}
private struct GoalWorkspaceRow: View {
    let goal: GoalRecord
    let session: PageSession
    let open: (AppRoute) -> Void
    @Environment(AppStore.self) private var store
    private var accepted: GoalRecord { session.goal ?? goal }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            link
            GoalSaveFailure(session: session)
            if let chart = GoalPresentation.primaryChart(accepted), !chart["manual"].isNull { GoalManualFailure(editor: session.manual(chart)) }
        }
    }
    private var link: some View {
        NavigationLink(value: AppRoute.goal(goal.id)) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline) {
                    Text(accepted.title).panelsScaledFont(size: 15, weight: .medium).foregroundStyle(PanelsTheme.strong)
                    Spacer(minLength: 8)
                    Text(reading).panelsScaledFont(size: 12).foregroundStyle(PanelsTheme.Goal.tone(accepted.status))
                }
                Text(accepted.deadline.map { "By " + GoalPresentation.date($0) } ?? "No deadline")
                    .panelsScaledFont(size: 11).foregroundStyle(PanelsTheme.Goal.tone(GoalPresentation.deadlineTone(accepted.deadline, completed: accepted.completed, today: accepted.statusDate)))
            }.frame(maxWidth: .infinity, minHeight: 60, alignment: .leading).contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityIdentifier("workspace-goal-\(goal.id)")
            .contextMenu {
                Button("Open") { open(.goal(goal.id)) }
                Group {
                    if accepted.kind == "binary", GoalPresentation.primaryChecklist(accepted) == nil {
                        Button(accepted.completed ? "Mark not done" : "Mark done") { Task { await session.save(["completed": .bool(!accepted.completed)]) } }
                    }
                    if let chart = GoalPresentation.primaryChart(accepted), !chart["manual"].isNull {
                        ForEach(Array(chart["manual"]["increments"].array.enumerated()), id: \.offset) { _, value in
                            if let delta = value.number {
                                Button("Add " + GoalChartPresentation.number(delta, decimals: 6, trim: true) + GoalChartPresentation.suffix(chart)) {
                                    Task { let manual = session.manual(chart); await manual.refresh(); await manual.write(delta: delta); await session.refresh() }
                                }
                            }
                        }
                    }
                }
            } preview: {
                GoalPeek(session: session).frame(width: 340).environment(store)
            }
            .task(id: store.baseURL) { await session.follow() }
    }
    private var reading: String {
        guard let chart = GoalPresentation.primaryChart(accepted), chart.kind != "binary" else { return GoalPresentation.statusText(accepted) }
        let projection = session.readings?.charts[chart.id] ?? .null
        let data = GoalChartPresentation.data(chart, values: session.readings?.values ?? [], range: "all", projection: projection)
        return GoalChartPresentation.format(chart, GoalChartPresentation.headline(chart, data: data, projection: projection).value)
    }
}

private struct GoalPeek: View {
    let session: PageSession
    @Environment(AppStore.self) private var store
    @State private var project = ""
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            if let goal = session.goal {
                HStack(spacing: 8) {
                    if !project.isEmpty { Text(project); Text("·") }
                    Text(goal.deadline.map { "By " + GoalPresentation.date($0) } ?? "No deadline")
                        .foregroundStyle(PanelsTheme.Goal.tone(GoalPresentation.deadlineTone(goal.deadline, completed: goal.completed, today: goal.statusDate)))
                }.font(.caption).foregroundStyle(PanelsTheme.Goal.faint)
                Text(goal.title).panelsScaledFont(size: 22, weight: .semibold).foregroundStyle(PanelsTheme.strong)
                if let checklist = GoalPresentation.primaryChecklist(goal) { GoalChecklist(block: checklist, session: session, preview: true) }
                else if let chart = GoalPresentation.primaryChart(goal) { GoalChart(block: chart, session: session, integrated: true, preview: true) }
            }
        }.padding(24).background(PanelsTheme.surface).allowsHitTesting(false)
            .task {
                let handle = store.resources.handle(GoalResources.projects)
                handle.acquire(); defer { handle.release() }; await handle.refresh()
                project = handle.value?["projects"].array.first { $0.id == session.goal?.projectId }?["name"].text ?? ""
                do { try await Task.sleep(for: .seconds(365 * 24 * 60 * 60)) } catch { }
            }
    }
}

/// Existing Chief and Manager pages share goals, authored blocks and Tickets.
struct HolderContent: View {
    let holder: GoalHolder
    @Environment(AppStore.self) private var store
    @State private var goalsHandle: ResourceHandle<GoalsResponse>?
    @State private var projectsHandle: ResourceHandle<GoalJSON>?
    private var goals: [GoalRecord] { goalsHandle?.value?.goals ?? [] }
    private var nextRefreshAt: Double? { goalsHandle?.value?.nextRefreshAt }
    private var projects: [GoalJSON] { projectsHandle?.value?["projects"].array ?? [] }
    @State private var sessions = PageSessionCollection()
    private var soleSession: PageSession? { sole.flatMap { sessions.session($0.id, server: store.baseURL) } }
    private var visibleGoals: [GoalRecord] { sole.map { [$0] } ?? held.filter(\.active) }
    @State private var error: String?
    @State private var pageSession: PageSession?
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
        }.task(id: store.baseURL) { await follow() }
            .task(id: "\(store.baseURL):\(visibleGoals.map(\.id))") {
                sessions.update(visibleGoals, api: store.client, resources: store.resources)
            }
            .onChange(of: sessions.unfinishedKeys) { _, _ in sessions.prune() }
            .modifier(PageArtifactNavigation(holder: holder))
    }
    private func follow() async {
        let list = store.resources.handle(GoalResources.list), projects = store.resources.handle(GoalResources.projects)
        goalsHandle = list; projectsHandle = projects
        let session = PageSession.holder(holder, api: store.client, resources: store.resources); pageSession = session
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
    func body(content: Content) -> some View {
        content.environment(\.openURL, OpenURLAction { url in
            if ArtifactTarget.isRetiredGoalURL(url, baseURL: store.baseURL) { return .discarded }
            if let target = ArtifactTarget.from(url, baseURL: store.baseURL) { selectedArtifact = target; return .handled }
            return .systemAction
        }).navigationDestination(item: $selectedArtifact) { target in
            ArtifactViewerView(owner: target.owner, path: target.path, title: target.title, conversationOwner: holder?.conversationOwner)
        }
    }
}
