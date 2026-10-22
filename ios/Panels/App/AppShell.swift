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
                ReviewView().navigationDestination(for: AppRoute.self) { RouteDestination(route: $0) }
            }.safeAreaInset(edge: .bottom, spacing: 0) { DeploymentStrip() }.tabItem { Label("Review", systemImage: "checkmark.circle") }
                .badge(store.review?.items.count ?? 0).tag(AppTab.review)
            NavigationStack(path: $workspacePath) {
                WorkspaceView().navigationDestination(for: AppRoute.self) { RouteDestination(route: $0) }
            }.safeAreaInset(edge: .bottom, spacing: 0) { DeploymentStrip() }.tabItem { Label("Workspace", systemImage: "rectangle.3.group") }.tag(AppTab.workspace)
            Color.clear.tabItem { Label("More", systemImage: "ellipsis") }.tag(AppTab.more)
        }
        .sheet(isPresented: $morePresented) {
            NavigationStack(path: $morePath) {
                MoreView().navigationDestination(for: AppRoute.self) { RouteDestination(route: $0) }
            }
            .presentationDetents([.medium, .large], selection: $moreDetent)
            .presentationDragIndicator(.visible)
            .presentationBackground(PanelsTheme.surface)
        }
        .onChange(of: reviewPath) { old, new in if new.count > old.count { Haptics.light() } }
        .onChange(of: workspacePath) { old, new in if new.count > old.count { Haptics.light() } }
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
}
