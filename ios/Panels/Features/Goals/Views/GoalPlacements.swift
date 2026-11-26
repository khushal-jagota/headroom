import SwiftUI

/// Existing Chief and Manager pages share goals, authored blocks and Tickets.
struct HolderContent: View {
    let holder: GoalHolder
    let editOwner: PageSessionRoute
    @Environment(AppStore.self) private var store
    @State private var goalsHandle: ResourceHandle<GoalsResponse>?
    @State private var projectsHandle: ResourceHandle<GoalJSON>?
    private var goals: [GoalRecord] { goalsHandle?.value?.goals ?? [] }
    private var projects: [GoalJSON] { projectsHandle?.value?["projects"].array ?? [] }
    private var sessions: PageSessionCollection { editOwner.goals }
    private var soleSession: PageSession? { sole.flatMap { sessions.session($0.id, server: store.baseURL) } }
    private var visibleGoals: [GoalRecord] { sole.map { [$0] } ?? held.filter(\.active) }
    @State private var error: String?
    @State private var pageSession: PageSession?
    private var holderStamp: String { (holder.kind == .chief ? store.chiefStamp() : store.managerStamp(holder.id ?? "")) ?? "" }
    private var held: [GoalRecord] { holder.goals(in: goals) }
    private var sole: GoalRecord? { holder.kind == .manager ? holder.soleGoal(in: goals) : nil }
    private var activeGoals: [GoalRecord] { held.filter(\.active) }
    private var documentBlocks: [GoalJSON] { (pageSession?.page?["blocks"].array ?? []).filter { $0.type != "tickets" } }
    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
            HolderTickets(holder: holder)
            if sole != nil, let soleSession {
                GoalDocumentContent(session: soleSession, showsTitle: false)
                    .accessibilityIdentifier("holder-goal-content")
            } else {
                if !activeGoals.isEmpty {
                    VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                        ForEach(activeGoals) { goal in
                            if let session = sessions.session(goal.id, server: store.baseURL) {
                                ChiefGoalCard(goal: goal, project: projects.first { $0.id == goal.projectId }?["name"].string ?? "", session: session)
                            }
                        }
                    }.accessibilityIdentifier("holder-goal-cards")
                }
                if let session = pageSession {
                    if !documentBlocks.isEmpty {
                        GoalPageBlocks(blocks: documentBlocks, session: session)
                            .accessibilityIdentifier("holder-document")
                    }
                    GoalSaveFailure(session: session)
                    if let error = session.readError { LoadErrorView(message: error) { await session.refresh() } }
                }
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
            }.buttonStyle(.plain).systemCopyText(goal.title)
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
    @Environment(\.openRoute) private var openRoute
    @Environment(\.screenTextEntry) private var textEntry
    func body(content: Content) -> some View {
        content.environment(\.openURL, OpenURLAction { url in
            if ArtifactTarget.isRetiredGoalURL(url, baseURL: store.baseURL) { return .discarded }
            if let target = ArtifactTarget.from(url, baseURL: store.baseURL) {
                let route = AppRoute.artifact(target, dock: .conversation(holder?.conversationOwner ?? target.owner.conversationOwner))
                if let textEntry { textEntry.endEditingBeforeNavigation { openRoute(route) } }
                else { openRoute(route) }
                return .handled
            }
            return .systemAction
        })
    }
}
