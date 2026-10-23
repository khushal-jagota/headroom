import SwiftUI

struct AppShell: View {
    @Environment(AppStore.self) private var store
    @Environment(NotificationController.self) private var notifications
    @Environment(\.scenePhase) private var scenePhase
    @State private var deploymentState: DeploymentStatusPresentation?
    @State private var selectedTab: AppTab = .workspace
    @State private var reviewPath: [AppRoute] = []
    @State private var workspacePath: [AppRoute] = []
    @State private var morePath: [AppRoute] = []

    var body: some View {
        TabView(selection: $selectedTab) {
            NavigationStack(path: $reviewPath) {
                ReviewView(openTicket: { id in
                    var transaction = Transaction(animation: nil)
                    transaction.disablesAnimations = true
                    withTransaction(transaction) {
                        workspacePath.append(.ticket(id))
                        selectedTab = .workspace
                    }
                })
                    .navigationDestination(for: AppRoute.self) { detail($0) }
            }
                .tabItem { Label("Review", systemImage: "checkmark.circle") }
                .badge(store.review?.items.count ?? 0).tag(AppTab.review)
            NavigationStack(path: $workspacePath) {
                WorkspaceView()
                    .navigationDestination(for: AppRoute.self) { detail($0) }
            }
                .tabItem { Label("Workspace", systemImage: "rectangle.3.group") }.tag(AppTab.workspace)
            NavigationStack(path: $morePath) {
                MoreView()
                    .navigationDestination(for: AppRoute.self) { detail($0) }
            }
                .tabItem { Label { Text("More") } icon: { Image(uiImage: moreTabImage) } }.tag(AppTab.more)
        }
        .task {
            while !Task.isCancelled {
                deploymentState = DeploymentStatusPresentation.resolve(store.deployment, connected: store.connection == "Following changes", readFailed: store.errors["Deployment"] != nil, now: .now)
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
            }
        }
        .task(id: notifications.pendingOpen?.id) { openNotification() }
        .task(id: "push:\(store.baseURL):\(scenePhase == .active)") {
            if scenePhase == .active { await notifications.refresh(using: store.client) }
        }
        .alert("Notification", isPresented: Binding(get: { notifications.routingError != nil }, set: { if !$0 { notifications.routingError = nil } })) {
            Button("OK") { notifications.routingError = nil }
        } message: { Text(notifications.routingError ?? "") }
        // Owner override: tabs select; pushes and back navigation give a light impact.
        .onChange(of: selectedTab) { _, _ in Haptics.selection() }
        .onChange(of: reviewPath) { old, new in if old != new { Haptics.light() } }
        .onChange(of: workspacePath) { old, new in if old != new { Haptics.light() } }
        .onChange(of: morePath) { old, new in if old != new { Haptics.light() } }
        .onChange(of: store.baseURL) { _, _ in reviewPath = []; workspacePath = []; morePath = [] }
        .task(id: "\(store.baseURL.absoluteString):\(scenePhase == .active)") {
            if scenePhase == .active { await store.run() }
        }
    }

    private var moreTabImage: UIImage {
        let symbol = UIImage(systemName: "ellipsis", withConfiguration: UIImage.SymbolConfiguration(pointSize: 23))!
        guard let deploymentState else { return symbol }
        return UIGraphicsImageRenderer(size: CGSize(width: 32, height: 25)).image { _ in
            symbol.withTintColor(UIColor(selectedTab == .more ? PanelsTheme.accent : PanelsTheme.faint)).draw(in: CGRect(x: 1, y: 9, width: 25, height: 6))
            UIColor(deploymentState == .problem ? PanelsTheme.error : PanelsTheme.accent).setFill()
            UIBezierPath(ovalIn: CGRect(x: 26, y: 0, width: 6, height: 6)).fill()
        }.withRenderingMode(.alwaysOriginal)
    }

    private func openNotification() {
        guard let destination = notifications.consumeOpen(for: store.baseURL) else { return }
        switch destination {
        case .review(let ticketID):
            if let ticketID {
                workspacePath = [.ticket(ticketID)]
                selectedTab = .workspace
            } else {
                reviewPath = []
                selectedTab = .review
            }
        case .ticket(let id): selectedTab = .workspace; workspacePath = [.ticket(id)]
        case .orchestrator(let id): selectedTab = .workspace; workspacePath = [.orchestrator(id)]
        case .chief: selectedTab = .workspace; workspacePath = [.chief]
        }
    }

    private func detail(_ route: AppRoute) -> some View {
        RouteDestination(route: route)
    }
}
