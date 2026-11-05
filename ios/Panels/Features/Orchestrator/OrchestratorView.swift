import SwiftUI

struct OrchestratorView: View {
    let managerID: String
    var openConversationID: String?
    @Environment(AppStore.self) private var store
    @State private var detail: ManagerDetail?
    @State private var error: String?
    @State private var actions = AgentPageActions()

    private var summary: ManagerSummary? { store.managers.first { $0.id == managerID } }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                AgentPageControls {
                    AgentPageMenu(target: Principal(kind: "manager", id: managerID), settled: detail?.settled ?? false, actions: actions) { await store.refresh() }
                }
                VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
                    AgentPageTitle(title: detail?.title ?? summary?.title ?? "Orchestrator", signal: detail?.conversationSignal ?? summary?.conversationSignal)
                    if let detail {
                        AgentRecap(recap: detail.recap, target: Principal(kind: "manager", id: managerID), refresh: { await store.refresh() }, actions: actions)
                    }
                }
                if let detail {
                    HolderContent(holder: .manager(managerID))
                    VStack(spacing: 0) {
                        Divider().overlay(PanelsTheme.border)
                        ArtifactsRow(owner: ArtifactOwner(kind: .manager, id: managerID), count: detail.artifacts.count)
                    }.font(PanelsTheme.TypeScale.body)
                }
            }.padding(.horizontal, AgentPageLayout.gutter).padding(.bottom, PanelsTheme.Space.xl)
        }
        .toolbar(.hidden, for: .navigationBar)
        .panelsScreen()
        .conversationNavigation(owner: .manager(managerID), conversationID: detail?.manager.conversationId, title: detail?.title ?? summary?.title ?? "Orchestrator", openConversationID: openConversationID, refreshError: error, retry: { await load() })
        .task(id: store.revision) { await load() }
        .panelsRefreshable { await load() }
        .screenLoadState("orchestrator", hasContent: detail != nil, error: error, showsRefreshError: false) { await load() }
        .accessibilityIdentifier("orchestrator-screen")
    }

    private func load() async {
        let api = store.client
        if detail == nil { detail = api.cached("/api/managers/\(managerID)/workspace") }
        do {
            let result = try await api.manager(managerID)
            try Task.checkCancellation()
            guard api.baseURL == store.baseURL else { return }
            detail = result; error = nil
        } catch { if !Task.isCancelled, api.baseURL == store.baseURL { self.error = error.localizedDescription } }
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

/// Page controls belong to the scrolling content, with no bar or duplicate title.
struct AgentPageControls<Actions: View>: View {
    @Environment(\.dismiss) private var dismiss
    var beforeBack: (() -> Void)? = nil
    @ViewBuilder let actions: () -> Actions

    var body: some View {
        HStack {
            Button { beforeBack?(); dismiss() } label: {
                Image(systemName: "chevron.left")
                    .frame(width: 44, height: 44, alignment: .leading)
                    .contentShape(Rectangle())
            }.accessibilityLabel("Back").accessibilityIdentifier("detail-back")
            Spacer(minLength: 0)
            actions()
        }
        .font(.system(size: 16))
        .foregroundStyle(PanelsTheme.faint)
        .tint(PanelsTheme.faint)
        .buttonStyle(.plain)
        .background(AgentPageBackGesture())
    }
}
