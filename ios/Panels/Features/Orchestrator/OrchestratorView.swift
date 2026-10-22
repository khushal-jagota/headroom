import SwiftUI

struct OrchestratorView: View {
    let managerID: String
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var detail: ManagerDetail?
    @State private var error: String?
    @State private var headerCollapsed = false
    @State private var actions = AgentPageActions()
    @State private var scrolling = false
    @State private var refreshAfterScroll = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                if let error { LoadErrorView(message: error) { await load() } }
                if let detail {
                    VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
                        AgentEyebrow(title: detail.project + (detail.settled == true ? " · Settled" : ""), signal: detail.conversationSignal)
                        AgentPageTitle(title: detail.title)
                        AgentRecap(recap: detail.recap, target: Principal(kind: "manager", id: managerID), refresh: { await store.refresh() }, actions: actions)
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
                        Divider().overlay(PanelsTheme.border)
                        NavigationLink(value: AppRoute.artifacts(owner: ArtifactOwner(kind: .manager, id: managerID), directory: "")) {
                            HStack {
                                Text("Artifacts")
                                Spacer()
                                Text(detail.artifacts.count.formatted()).foregroundStyle(PanelsTheme.faint)
                            }.frame(minHeight: 50).contentShape(Rectangle())
                        }.buttonStyle(.plain)
                    }.font(PanelsTheme.TypeScale.body)
                    ConversationPlaceholder()
                } else if error == nil {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, PanelsTheme.Space.xxl)
                }
            }.padding(.horizontal, AgentPageLayout.gutter).padding(.bottom, PanelsTheme.Space.xl)
        }
        .onScrollPhaseChange { _, phase in
            scrolling = phase != .idle
            if !scrolling && refreshAfterScroll {
                refreshAfterScroll = false
                Task { await load() }
            }
        }
        .onScrollGeometryChange(for: Bool.self) { $0.contentOffset.y + $0.contentInsets.top > 100 } action: { _, collapsed in
            withAnimation(reduceMotion ? nil : .easeOut(duration: PanelsTheme.Motion.base)) { headerCollapsed = collapsed }
        }
        .navigationTitle(headerCollapsed ? detail?.title ?? "Orchestrator" : "")
        .navigationBarTitleDisplayMode(.inline).panelsScreen()
        .toolbar(.hidden, for: .tabBar)
        .toolbar { ToolbarItem(placement: .topBarTrailing) {
            AgentPageMenu(target: Principal(kind: "manager", id: managerID), settled: detail?.settled ?? false, actions: actions) { await store.refresh() }
        } }
        .task(id: store.revision) { await load() }
        .refreshable { Haptics.light(); await load() }
        .accessibilityIdentifier("orchestrator-screen")
    }

    private func load() async {
        let api = store.client
        do {
            let result = try await api.manager(managerID)
            try Task.checkCancellation()
            guard api.baseURL == store.baseURL else { return }
            if scrolling { refreshAfterScroll = true; return }
            detail = result; error = nil
        } catch { if !Task.isCancelled, api.baseURL == store.baseURL { self.error = error.localizedDescription } }
    }
}

struct AgentPageTitle: View {
    let title: String
    var body: some View {
        Text(title).font(.title.weight(.semibold)).foregroundStyle(PanelsTheme.strong)
            .fixedSize(horizontal: false, vertical: true).accessibilityAddTraits(.isHeader)
    }
}

enum AgentPageLayout {
    static let gutter: CGFloat = 20
    static let rowInset: CGFloat = 14
}

struct AgentEyebrow: View {
    let title: String
    let signal: ConversationSignal?
    var body: some View {
        HStack(spacing: PanelsTheme.Space.sm) {
            if !title.isEmpty { Text(title).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint) }
            Spacer()
            WorkerSignalMark(signal: signal)
        }.frame(minHeight: 18).padding(.top, PanelsTheme.Space.sm)
    }
}
