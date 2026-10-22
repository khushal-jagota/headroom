import SwiftUI

struct OrchestratorView: View {
    let managerID: String
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var detail: ManagerDetail?
    @State private var error: String?
    @State private var headerCollapsed = false
    @State private var goalExpanded = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                if let error { LoadErrorView(message: error) { await load() } }
                if let detail {
                    VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
                        AgentBreadcrumb(title: detail.project, signal: detail.conversationSignal)
                        AgentPageTitle(title: detail.title)
                        AgentRecap(recap: detail.recap, target: Principal(kind: "manager", id: managerID)) { await store.refresh() }
                    }
                    WorkerSections(tickets: detail.tickets)
                    VStack(spacing: PanelsTheme.Space.sm) {
                        if !detail.goal.isEmpty {
                            DisclosureGroup("Goal", isExpanded: $goalExpanded) {
                                Text(detail.goal).font(PanelsTheme.TypeScale.body).foregroundStyle(PanelsTheme.muted)
                                    .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, PanelsTheme.Space.md)
                            }.font(PanelsTheme.TypeScale.small).tint(PanelsTheme.faint)
                                .padding(PanelsTheme.Space.md)
                                .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
                                .animation(reduceMotion ? nil : .easeInOut(duration: PanelsTheme.Motion.slow), value: goalExpanded)
                                .onChange(of: goalExpanded) { _, _ in Haptics.selection() }
                        }
                        NavigationLink(value: AppRoute.artifacts(owner: ArtifactOwner(kind: .manager, id: managerID), directory: "")) {
                            HStack {
                                Label("Artifacts", systemImage: "folder")
                                Spacer()
                                Image(systemName: "chevron.right")
                            }.font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
                                .padding(PanelsTheme.Space.md).frame(minHeight: 44)
                                .background(PanelsTheme.raised, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
                        }.buttonStyle(.plain)
                    }
                    ConversationPlaceholder()
                } else if error == nil {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, PanelsTheme.Space.xxl)
                }
            }.padding(.horizontal, PanelsTheme.Space.xl).padding(.bottom, PanelsTheme.Space.xl)
        }
        .onScrollGeometryChange(for: Bool.self) { $0.contentOffset.y + $0.contentInsets.top > 100 } action: { _, collapsed in
            withAnimation(reduceMotion ? nil : .easeOut(duration: PanelsTheme.Motion.base)) { headerCollapsed = collapsed }
        }
        .navigationTitle(headerCollapsed ? detail?.title ?? "Orchestrator" : "")
        .navigationBarTitleDisplayMode(.inline).panelsScreen()
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
            detail = result; error = nil
        } catch { if !Task.isCancelled, api.baseURL == store.baseURL { self.error = error.localizedDescription } }
    }
}

struct AgentPageTitle: View {
    let title: String
    var body: some View {
        Text(title).font(PanelsTheme.TypeScale.title).foregroundStyle(PanelsTheme.strong)
            .fixedSize(horizontal: false, vertical: true).accessibilityAddTraits(.isHeader)
    }
}

struct AgentBreadcrumb: View {
    let title: String
    let signal: ConversationSignal?
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        HStack(spacing: PanelsTheme.Space.sm) {
            Button { Haptics.light(); dismiss() } label: {
                HStack(spacing: PanelsTheme.Space.sm) {
                    Text(title)
                    Text("/")
                    Image(systemName: "arrow.up.left")
                }.font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint).frame(minHeight: 44)
            }.accessibilityLabel("\(title), back to Workspace")
            Spacer()
            WorkerSignalMark(signal: signal)
        }
    }
}
