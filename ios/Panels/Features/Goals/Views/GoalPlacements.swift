import SwiftUI

/// Workspace is the web sidebar. Its active Goals belong inside the Chief card.
struct WorkspaceGoals: View {
    let open: (AppRoute) -> Void
    @Environment(AppStore.self) private var store
    @State private var goals: [GoalRecord] = []
    @State private var nextRefreshAt: Double?
    @State private var error: String?
    var body: some View {
        VStack(spacing: 0) {
            ForEach(goals.filter(\.active)) { goal in GoalWorkspaceRow(goal: goal, open: open) }
            if let error { LoadErrorView(message: error) { await load() } }
        }.task(id: "\(store.baseURL):\(store.revision)") { await load() }
            .modifier(GoalBoundaryRefresh(at: nextRefreshAt, refresh: load))
    }
    private func load() async {
        let api = store.client
        do {
            let response = try await api.goals(); try Task.checkCancellation()
            guard api.baseURL == store.baseURL else { return }; goals = response.goals; nextRefreshAt = response.nextRefreshAt; error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}
private struct GoalWorkspaceRow: View {
    let goal: GoalRecord
    let open: (AppRoute) -> Void
    @Environment(AppStore.self) private var store
    @State private var session: GoalSession?
    private var accepted: GoalRecord { session?.goal ?? goal }
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            link
            if let session {
                GoalSaveFailure(session: session)
                if let chart = GoalPresentation.primaryChart(accepted), !chart["manual"].isNull { GoalManualFailure(editor: session.manual(chart)) }
            }
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
            .modifier(GoalBoundaryRefresh(at: session?.nextRefreshAt) { if let session { await session.refresh() } })
            .contextMenu {
                Button("Open") { open(.goal(goal.id)) }
                if let session {
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
                if let session { GoalPeek(session: session).frame(width: 340).environment(store) }
            }
            .task(id: "\(store.baseURL):\(store.revision)") {
                let selected = GoalSession.session(id: goal.id, api: store.client, initial: goal)
                session = selected; await selected.refresh()
            }
    }
    private var reading: String {
        guard let session, let chart = GoalPresentation.primaryChart(accepted), chart.kind != "binary" else { return GoalPresentation.statusText(accepted) }
        let projection = session.readings?.charts[chart.id] ?? .null
        let data = GoalChartPresentation.data(chart, values: session.readings?.values ?? [], range: "all", projection: projection)
        return GoalChartPresentation.format(chart, GoalChartPresentation.headline(chart, data: data, projection: projection).value)
    }
}

private struct GoalPeek: View {
    let session: GoalSession
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
                if let response: GoalJSON = try? await store.client.get("/api/projects") { project = response["projects"].array.first { $0.id == session.goal?.projectId }?["name"].text ?? "" }
            }
    }
}

struct ChiefGoals: View {
    @Environment(AppStore.self) private var store
    @State private var goals: [GoalRecord] = []
    @State private var nextRefreshAt: Double?
    @State private var projects: [GoalJSON] = []
    @State private var error: String?
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                Text("Active goals").panelsScaledFont(size: 22, weight: .semibold).foregroundStyle(PanelsTheme.muted)
                Spacer()
                NavigationLink("All goals", value: AppRoute.goals).panelsScaledFont(size: 13)
            }
            ForEach(goals.filter(\.active)) { goal in ChiefGoalCard(goal: goal, project: projects.first { $0.id == goal.projectId }?["name"].string ?? "") }
            if goals.filter(\.active).isEmpty, error == nil { Text("No active goals").font(.footnote).foregroundStyle(PanelsTheme.Goal.faint) }
            if let error { LoadErrorView(message: error) { await load() } }
        }.task(id: "\(store.baseURL):\(store.revision)") { await load() }
            .modifier(GoalBoundaryRefresh(at: nextRefreshAt, refresh: load))
    }
    private func load() async {
        let api = store.client
        do {
            async let response = api.goals()
            async let projectResponse: GoalJSON = api.get("/api/projects")
            let (list, projects) = try await (response, projectResponse); try Task.checkCancellation()
            guard api.baseURL == store.baseURL else { return }; goals = list.goals; nextRefreshAt = list.nextRefreshAt; self.projects = projects["projects"].array; error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}
private struct ChiefGoalCard: View {
    let goal: GoalRecord
    let project: String
    @Environment(AppStore.self) private var store
    @State private var session: GoalSession?
    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            NavigationLink(value: AppRoute.goal(goal.id)) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack { Text(project); Text("·"); Text(goal.deadline.map { "By " + GoalPresentation.date($0) } ?? "No deadline").foregroundStyle(PanelsTheme.Goal.tone(GoalPresentation.deadlineTone(goal.deadline, completed: goal.completed, today: goal.statusDate))) }.font(.system(size: 12)).foregroundStyle(PanelsTheme.Goal.faint)
                    Text(goal.title).panelsScaledFont(size: 22, weight: .semibold).foregroundStyle(PanelsTheme.strong)
                }.frame(maxWidth: .infinity, alignment: .leading).padding(18).background(PanelsTheme.raised)
            }.buttonStyle(.plain)
            if let session, let accepted = session.goal {
                VStack(alignment: .leading, spacing: 16) {
                    if let checklist = GoalPresentation.primaryChecklist(accepted) { GoalChecklist(block: checklist, session: session) }
                    else if let chart = GoalPresentation.primaryChart(accepted) { GoalChart(block: chart, session: session, integrated: true) }
                    GoalSaveFailure(session: session)
                }.padding(.horizontal, 20).padding(.vertical, 24)
            }
        }.modifier(GoalBoundaryRefresh(at: session?.nextRefreshAt) { if let session { await session.refresh() } })
            .background(PanelsTheme.surface).clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(PanelsTheme.border))
            .task(id: "\(store.baseURL):\(store.revision)") { let selected = GoalSession.session(id: goal.id, api: store.client, initial: goal); session = selected; await selected.refresh() }
    }
}
