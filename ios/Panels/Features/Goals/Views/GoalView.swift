import SwiftUI

struct GoalView: View {
    let goalID: String
    var openConversationID: String? = nil
    @Environment(AppStore.self) private var store
    let editOwner: PageSessionRoute
    @State private var session: PageSession?
    var body: some View {
        Group {
            if let session {
                GoalDetailContent(session: session, openConversationID: openConversationID)
            } else { ScreenLoadingView(title: "goal") }
        }.task(id: "\(store.baseURL):\(goalID)") { session = editOwner.goal(goalID, api: store.client, resources: store.resources) }
    }
}
private struct GoalDetailContent: View {
    let session: PageSession
    var openConversationID: String?
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                AgentPageControls(beforeBack: {
                    UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
                }) {
                    Menu {
                        Button("Copy link", systemImage: "link") {
                            var link = URLComponents(url: store.baseURL, resolvingAgainstBaseURL: false)
                            link?.fragment = "/workspace/goal/\(session.id)"; UIPasteboard.general.url = link?.url
                        }
                    } label: { Image(systemName: "ellipsis").frame(width: 44, height: 44) }.accessibilityLabel("Goal actions")
                }
                GoalDocumentContent(session: session)
            }.padding(.horizontal, AgentPageLayout.gutter).padding(.bottom, 32)
        }.panelsScreen().toolbar(.hidden, for: .navigationBar)
            .modifier(GoalHolderConversation(session: session, openConversationID: openConversationID))
            .screenLoadState("goal", hasContent: session.goal != nil, error: session.readError, showsRefreshError: false) { await session.refresh() }
            .panelsRefreshable { await session.refresh() }
            .task(id: "\(store.baseURL):\(scenePhase == .active)") { if scenePhase == .active { await session.follow() } }
            .environment(\.pageTicketHolder, session.goal?.holder)
            .modifier(PageArtifactNavigation(holder: session.goal?.holder))
            .accessibilityIdentifier("goal-screen")
    }
}

/// The same goal document appears on its own page and under a sole-goal Manager.
struct GoalDocumentContent: View {
    let session: PageSession
    var showsTitle = true
    @Environment(AppStore.self) private var store
    @State private var projectsHandle: ResourceHandle<GoalJSON>?
    private var projects: [GoalJSON] { projectsHandle?.value?["projects"].array ?? [] }
    var body: some View {
        VStack(alignment: .leading, spacing: 28) {
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
                    if let legacy = goal.legacyConversationId, let holder = goal.holder {
                        NavigationLink("Previous goal conversation", value: AppRoute.conversation(owner: holder.conversationOwner, id: legacy))
                            .font(.footnote)
                    }
                }
        }.task(id: store.baseURL) { await session.follow() }
            .task(id: store.baseURL) { await loadProjects() }
    }
    private func header(_ goal: GoalRecord) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) { metadata(goal) }
                VStack(alignment: .leading, spacing: 8) { metadata(goal) }
            }.panelsScaledFont(size: 12, weight: .medium)
            if showsTitle {
                GoalEditableText(value: goal.title, label: "Goal title", session: session, path: ["title"])
                    .panelsScaledFont(size: 30, relativeTo: .title, weight: .semibold).foregroundStyle(PanelsTheme.strong)
            }
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
        let handle = store.resources.handle(GoalResources.projects); projectsHandle = handle
        handle.acquire(); defer { handle.release() }; await handle.refresh()
        do { try await Task.sleep(for: .seconds(365 * 24 * 60 * 60)) } catch { }
    }
}

private struct GoalHolderConversation: ViewModifier {
    let session: PageSession
    var openConversationID: String?
    @Environment(AppStore.self) private var store
    @State private var conversationHandle: ResourceHandle<GoalJSON>?
    private var conversationID: String? {
        guard let holder = session.goal?.holder else { return nil }
        let value = conversationHandle?.value
        return holder.kind == .chief ? value?["conversation_id"].string : value?["manager"]["conversation_id"].string
    }
    private var holderStamp: String {
        guard let holder = session.goal?.holder else { return "" }
        return (holder.kind == .chief ? store.chiefStamp() : store.managerStamp(holder.id ?? "")) ?? ""
    }
    func body(content: Content) -> some View {
        Group {
            if let holder = session.goal?.holder {
                content.conversationNavigation(owner: holder.conversationOwner, conversationID: conversationID,
                    title: session.goal?.title ?? "Goal", openConversationID: openConversationID,
                    refreshError: conversationHandle?.error ?? session.readError, retry: { await session.refresh(); await conversationHandle?.refresh() })
                    .id(holder)
            } else { content }
        }.task(id: "\(store.baseURL):\(String(describing: session.goal?.holder)):\(holderStamp)") { await load() }
    }
    private func load() async {
        guard let holder = session.goal?.holder else { return }
        let handle = store.resources.handle(GoalResources.holderConversation(holder))
        conversationHandle = handle; handle.acquire(); defer { handle.release() }
        await handle.refresh()
        do { try await Task.sleep(for: .seconds(365 * 24 * 60 * 60)) } catch { }
    }
}
