import SwiftUI

struct OrchestratorView: View {
    let managerID: String
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var detail: ManagerDetail?
    @State private var error: String?
    @State private var headerCollapsed = false
    @State private var actions = AgentPageActions()

    private var summary: ManagerSummary? { store.managers.first { $0.id == managerID } }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
                    AgentPageTitle(title: detail?.title ?? summary?.title ?? "Orchestrator")
                    if let detail {
                        AgentRecap(recap: detail.recap, target: Principal(kind: "manager", id: managerID), refresh: { await store.refresh() }, actions: actions)
                    }
                }
                if let detail {
                    if detail.tickets.isEmpty {
                        Text("No workers yet").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    }
                    WorkerSections(tickets: detail.tickets)
                    VStack(spacing: 0) {
                        Divider().overlay(PanelsTheme.border)
                        NavigationLink {
                            AgentReadingPage(title: "Goal", originalText: detail.goal) { text in
                                try await store.client.saveManagerGoal(managerID, goal: text)
                                await store.refresh()
                            }
                        } label: {
                            Text("Goal").frame(maxWidth: .infinity, minHeight: 50, alignment: .leading).contentShape(Rectangle())
                        }.buttonStyle(.plain).accessibilityIdentifier("manager-goal")
                        ArtifactsRow(owner: ArtifactOwner(kind: .manager, id: managerID), count: detail.artifacts.count)
                    }.font(PanelsTheme.TypeScale.body)
                }
            }.padding(.horizontal, AgentPageLayout.gutter).padding(.bottom, PanelsTheme.Space.xl)
        }
        .onScrollGeometryChange(for: Bool.self) { $0.contentOffset.y + $0.contentInsets.top > 100 } action: { _, collapsed in
            withAnimation(reduceMotion ? nil : PanelsTheme.Motion.fade) { headerCollapsed = collapsed }
        }
        .navigationTitle(headerCollapsed ? detail?.title ?? "Orchestrator" : "")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(.bar, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .panelsScreen()
        .conversationOverlay(owner: .manager(managerID), conversationID: detail?.manager.conversationId, title: detail?.title ?? summary?.title ?? "Orchestrator")
        .toolbar {
            ToolbarItem(placement: .principal) {
                AgentNavigationHeader(title: detail?.title ?? summary?.title ?? "Orchestrator",
                                      eyebrow: (detail?.project ?? summary?.project ?? "") + ((detail?.settled ?? summary?.settled) == true ? " · Settled" : ""),
                                      collapsed: headerCollapsed, signal: detail?.conversationSignal ?? summary?.conversationSignal)
            }
            ToolbarItem(placement: .topBarTrailing) {
                AgentPageMenu(target: Principal(kind: "manager", id: managerID), settled: detail?.settled ?? false, actions: actions) { await store.refresh() }
            }
        }
        .task(id: store.revision) { await load() }
        .refreshable { await load() }
        .screenLoadState("orchestrator", hasContent: detail != nil, error: error) { await load() }
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
            Text(title).font(.title.weight(.semibold)).foregroundStyle(PanelsTheme.strong)
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

/// Keeps the page context beside native Back and actions, above the full scrolling title.
struct AgentNavigationHeader: View {
    let title: String
    var eyebrow: String = ""
    var collapsed = false
    var signal: ConversationSignal? = nil

    var body: some View {
        HStack(spacing: PanelsTheme.Space.sm) {
            Text(collapsed || eyebrow.isEmpty ? title : eyebrow)
                .font(collapsed || eyebrow.isEmpty ? .headline : PanelsTheme.TypeScale.small)
                .foregroundStyle(collapsed || eyebrow.isEmpty ? PanelsTheme.strong : PanelsTheme.faint)
                .lineLimit(1)
            ConversationSignalMark(signal: signal)
        }.conversationAccessibility(label: collapsed || eyebrow.isEmpty ? title : eyebrow, signal: signal)
    }
}
