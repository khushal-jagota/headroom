import SwiftUI

struct AppShell: View {
    @Environment(AppStore.self) private var store
    @Environment(\.scenePhase) private var scenePhase
    @State private var selectedTab: AppTab = .workspace
    @State private var reviewPath: [AppRoute] = []
    @State private var workspacePath: [AppRoute] = []
    @State private var morePath: [AppRoute] = []

    @State private var morePresented = false
    @State private var moreDetent: PresentationDetent = .medium

    var body: some View {
        TabView(selection: Binding(get: { selectedTab }, set: { tab in
            if tab == .more {
                Haptics.selection()
                morePath = []
                moreDetent = .medium
                morePresented = true
            } else {
                selectedTab = tab
            }
        })) {
            NavigationStack(path: $reviewPath) {
                ReviewView(openTicket: { id in
                    selectedTab = .workspace
                    workspacePath.append(.ticket(id))
                })
                    .safeAreaInset(edge: .bottom, spacing: 0) { DeploymentCapsule() }
                    .navigationDestination(for: AppRoute.self) { detail($0) }
            }.tabItem { Label("Review", systemImage: "checkmark.circle") }
                .badge(store.review?.items.count ?? 0).tag(AppTab.review)
            NavigationStack(path: $workspacePath) {
                WorkspaceView()
                    .safeAreaInset(edge: .bottom, spacing: 0) { DeploymentCapsule() }
                    .navigationDestination(for: AppRoute.self) { detail($0) }
            }.tabItem { Label("Workspace", systemImage: "rectangle.3.group") }.tag(AppTab.workspace)
            Color.clear.tabItem { Label("More", systemImage: "ellipsis") }.tag(AppTab.more)
        }
        .sheet(isPresented: $morePresented) {
            NavigationStack(path: $morePath) {
                MoreView()
                    .safeAreaInset(edge: .bottom, spacing: 0) { DeploymentCapsule() }
                    .navigationDestination(for: AppRoute.self) { detail($0) }
            }
            .presentationDetents([.medium, .large], selection: $moreDetent)
            .presentationDragIndicator(.visible)
            .presentationBackground(PanelsTheme.surface)
        }
        .onChange(of: reviewPath) { old, new in if new.count > old.count { Haptics.light() } }
        .onChange(of: morePath) { old, new in
            if old != new { Haptics.light() }
            if !new.isEmpty { moreDetent = .large }
        }
        .onChange(of: selectedTab) { _, _ in Haptics.selection() }
        .onChange(of: store.baseURL) { _, _ in reviewPath = []; workspacePath = []; morePath = [] }
        .task(id: "\(store.baseURL.absoluteString):\(scenePhase == .active)") {
            if scenePhase == .active { await store.run() }
        }
    }

    private func detail(_ route: AppRoute) -> some View {
        RouteDestination(route: route)
            .toolbar(.hidden, for: .tabBar)
            .safeAreaInset(edge: .bottom, spacing: 0) { DeploymentCapsule() }
    }
}

// Match the web's expiry rules: stale progress becomes reconnecting, and a
// successful deployment only stays visible until its server-provided expiry.
enum DeploymentCapsuleState: String {
    case deploying = "Deploying", reconnecting = "Reconnecting", problem = "Problem", deployed = "Deployed"

    static func resolve(_ status: DeploymentStatus?, connected: Bool, readFailed: Bool, now: Date) -> Self? {
        guard let status else { return .reconnecting }
        if status.state == "problem" || status.outcome == "failed" || status.outcome == "rolled_back" { return .problem }
        if readFailed { return .problem }
        let expiry = status.validUntil.flatMap { value in
            let format = ISO8601DateFormatter()
            format.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            return format.date(from: value) ?? ISO8601DateFormatter().date(from: value)
        }
        let fresh = expiry.map { now < $0 } ?? false
        switch status.state {
        case "preparing", "restarting": return fresh ? .deploying : .reconnecting
        case "back_up": return connected ? (fresh ? .deployed : nil) : .reconnecting
        case "idle": return connected ? nil : .reconnecting
        default: return .reconnecting
        }
    }
}

private struct DeploymentCapsule: View {
    @Environment(AppStore.self) private var store
    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            if let state = DeploymentCapsuleState.resolve(store.deployment, connected: store.connection == "Following changes", readFailed: store.errors["Deployment"] != nil, now: context.date) {
                HStack(spacing: PanelsTheme.Space.sm) {
                    Circle().fill(state == .problem ? PanelsTheme.error : PanelsTheme.accent).frame(width: 5, height: 5)
                    Text(state.rawValue).font(PanelsTheme.TypeScale.caption)
                }
                .foregroundStyle(PanelsTheme.muted)
                .padding(.horizontal, PanelsTheme.Space.md).padding(.vertical, PanelsTheme.Space.sm)
                .background(PanelsTheme.raised, in: Capsule())
                .padding(.vertical, PanelsTheme.Space.sm)
                .frame(maxWidth: .infinity)
                .accessibilityElement(children: .combine)
            }
        }
    }
}
