import SwiftUI

struct GoalView: View {
    let goalID: String
    var openConversationID: String? = nil
    @Environment(AppStore.self) private var store
    @State private var session: GoalSession?
    var body: some View {
        Group {
            if let session {
                GoalDetailContent(session: session, openConversationID: openConversationID)
            } else { ScreenLoadingView(title: "goal") }
        }.task(id: store.baseURL) { session = GoalSession.session(id: goalID, api: store.client) }
    }
}
private struct GoalDetailContent: View {
    let session: GoalSession
    var openConversationID: String?
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.openURL) private var openURL
    @State private var projects: [GoalJSON] = []
    @State private var files = false
    @State private var selectedArtifact: ArtifactTarget?
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                AgentPageControls(beforeBack: {
                    UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
                }) {
                    Menu {
                        Button("Files", systemImage: "folder") { files = true }
                        Button("Copy link", systemImage: "link") {
                            var link = URLComponents(url: store.baseURL, resolvingAgainstBaseURL: false)
                            link?.fragment = "/workspace/goal/\(session.id)"; UIPasteboard.general.url = link?.url
                        }
                    } label: { Image(systemName: "ellipsis").frame(width: 44, height: 44) }.accessibilityLabel("Goal actions")
                }
                if let goal = session.goal {
                    header(goal)
                    GoalSaveFailure(session: session)
                    if let checklist = GoalPresentation.primaryChecklist(goal) { GoalChecklist(block: checklist, session: session) }
                    else if let chart = GoalPresentation.primaryChart(goal) {
                        GoalChart(block: chart, session: session, integrated: true).modifier(GoalPanel(padding: 20, radius: PanelsTheme.Goal.panelRadius))
                    }
                    ForEach(Array((session.readings?.sources.filter { $0["health"].text != "healthy" } ?? []).enumerated()), id: \.offset) { _, source in
                        Text(source["source_name"].text + ": " + source["health"].text).font(.caption).foregroundStyle(PanelsTheme.Goal.attention)
                    }
                    GoalPageBlocks(blocks: GoalPresentation.withoutChecklist(goal.page["blocks"].array, id: GoalPresentation.primaryChecklist(goal)?.id), session: session).padding(.top, 12)
                }
            }.padding(.horizontal, AgentPageLayout.gutter).padding(.bottom, 32)
        }.panelsScreen().toolbar(.hidden, for: .navigationBar)
            .conversationNavigation(owner: .goal(session.id), conversationID: session.goal?.conversationId, title: session.goal?.title ?? "Goal", openConversationID: openConversationID, refreshError: session.readError, retry: { await session.refresh() })
            .screenLoadState("goal", hasContent: session.goal != nil, error: session.readError, showsRefreshError: false) { await session.refresh() }
            .panelsRefreshable { await session.refresh() }
            .task(id: "\(store.revision):\(scenePhase == .active)") { if scenePhase == .active { await session.refresh(); await loadProjects() } }
            .modifier(GoalBoundaryRefresh(at: session.nextRefreshAt) { await session.refresh() })
            .environment(\.openURL, OpenURLAction { url in
                if let target = ArtifactTarget.from(url, baseURL: store.baseURL) { selectedArtifact = target; return .handled }
                return .systemAction
            })
            .navigationDestination(item: $selectedArtifact) { target in ArtifactViewerView(owner: target.owner, path: target.path, title: target.title, conversationOwner: .goal(session.id)) }
            .navigationDestination(isPresented: $files) { ScrollView { ArtifactListView(owner: .init(kind: .goal, id: session.id)).padding() }.panelsScreen().navigationTitle("Files") }
            .accessibilityIdentifier("goal-screen")
    }
    private func header(_ goal: GoalRecord) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { metadata(goal) }
                VStack(alignment: .leading, spacing: 8) { metadata(goal) }
            }.panelsScaledFont(size: 12, weight: .medium)
            GoalEditableText(value: goal.title, label: "Goal title", session: session, path: ["title"])
                .panelsScaledFont(size: 30, relativeTo: .title, weight: .semibold).foregroundStyle(PanelsTheme.strong)
            GoalEditableText(value: goal.why, label: "Why this goal matters", session: session, path: ["why"], multiline: true)
                .panelsScaledFont(size: 14, weight: .medium).foregroundStyle(PanelsTheme.Goal.faint)
        }
    }
    @ViewBuilder private func metadata(_ goal: GoalRecord) -> some View {
        Menu {
            ForEach(projects, id: \.id) { project in
                Button(project["name"].text) { Task { await session.save(["project_id": .string(project.id)], revision: goal.revision) } }
            }
        } label: { Text(projects.first { $0.id == goal.projectId }?["name"].string ?? (goal.projectId == "project_personal" ? "Personal" : "…")) }.accessibilityLabel("Project")
        GoalDateControl(value: goal.deadline, label: "Goal deadline", tone: GoalPresentation.deadlineTone(goal.deadline, completed: goal.completed, today: goal.statusDate), revision: goal.revision) { value, revision in
            await session.save(["deadline": value.map(GoalJSON.string) ?? .null], revision: revision)
        }
        Menu {
            Button("Active") { Task { await session.save(["active": .bool(true)], revision: goal.revision) } }
            Button("Inactive") { Task { await session.save(["active": .bool(false)], revision: goal.revision) } }
        } label: { Text(goal.active ? "Active" : "Inactive") }.accessibilityLabel("State")
    }
    private func loadProjects() async {
        if let response: GoalJSON = try? await store.client.get("/api/projects") { projects = response["projects"].array }
    }
}
