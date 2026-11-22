import SwiftUI

struct OrchestratorView: View {
    let managerID: String
    let editOwner: PageSessionRoute
    @Environment(AppStore.self) private var store
    private var resource: ResourceHandle<ManagerDetail> { store.resources.handle(ManagerResources.detail(managerID)) }
    private var detail: ManagerDetail? { resource.value }
    private var contentResource: ResourceHandle<ManagerDocumentContent> { store.resources.handle(ManagerResources.content(managerID)) }
    private var error: String? { resource.error ?? contentResource.error }
    @State private var actions = AgentPageActions()

    private var summary: ManagerSummary? { store.managers.first { $0.id == managerID } }
    private var liveSignal: ConversationSignal? { store.presentation?.managersById[managerID]?.signal.conversationSignal }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
                    AgentPageTitle(title: detail?.title ?? summary?.title ?? "Orchestrator", signal: liveSignal ?? detail?.conversationSignal ?? summary?.conversationSignal)
                    if let detail {
                        AgentRecap(recap: contentResource.value?.recap ?? detail.recap, actions: actions)
                    }
                }
                if let detail {
                    HolderContent(holder: .manager(managerID), editOwner: editOwner)
                    VStack(spacing: 0) {
                        Divider().overlay(PanelsTheme.border)
                        ArtifactsRow(owner: ArtifactOwner(kind: .manager, id: managerID), count: detail.artifacts.count)
                    }.font(PanelsTheme.TypeScale.body)
                }
            }.padding(.horizontal, AgentPageLayout.gutter).padding(.bottom, PanelsTheme.Space.xl)
        }
        .navigationTitle("").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                AgentPageMenu(target: Principal(kind: "manager", id: managerID), settled: detail?.settled ?? false, actions: actions) { await load(); await store.refresh() }
            }
        }
        .panelsScreen()
        .conversationNavigation(owner: .manager(managerID), conversationID: detail?.manager.conversationId, refreshError: error, retry: { await load() })
        .resourceDemand(resource)
        .resourceDemand(contentResource)
        .task(id: store.baseURL.absoluteString + (store.managerStamp(managerID) ?? "")) { await resource.refresh() }
        .navigationDemand("manager:\(managerID)", selected: .manager(managerID), windows: [NavigationScope.children(of: .manager(managerID))])
        .panelsRefreshable { await load() }
        .screenLoadState("orchestrator", hasContent: detail != nil, error: error) { await load() }
        .accessibilityIdentifier("orchestrator-screen")
    }

    private func load() async {
        store.resources.invalidate([resource.key, contentResource.key])
        async let detail: Void = resource.refresh()
        async let content: Void = contentResource.refresh()
        _ = await (detail, content)
    }

}

struct AgentPageTitle: View {
    let title: String
    var signal: ConversationSignal? = nil
    var body: some View {
        HStack(alignment: .center) {
            Text(title).font(PanelsTheme.TypeScale.detailTitle).foregroundStyle(PanelsTheme.strong)
                .fixedSize(horizontal: false, vertical: true).accessibilityAddTraits(.isHeader)
            Spacer(minLength: PanelsTheme.Space.sm)
            ConversationSignalMark(signal: signal)
        }.conversationAccessibility(label: title, signal: signal)
    }
}

enum AgentPageLayout {
    static let gutter: CGFloat = 20
    static let rowInset: CGFloat = 14
}

