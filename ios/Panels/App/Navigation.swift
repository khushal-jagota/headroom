import SwiftUI

/// A Sidebar destination. Each one owns a page path in the shell.
enum AppDestination: Hashable {
    case review, chief, manager(String), settings
}

enum AppRoute: Hashable {
    case chief
    case goals, goal(String)
    /// The full conversation page. `linked` marks a link to one exact conversation,
    /// which reads as history once a newer conversation becomes current.
    case conversation(owner: ConversationOwner, id: String?, linked: Bool = true)
    case orchestrator(String)
    case ticket(String)
    case artifact(owner: ArtifactOwner, path: String, title: String, conversationOwner: ConversationOwner? = nil, showsConversation: Bool = true)
    case ideas, usage, vps, settings
}

extension AppRoute {
    static func artifact(_ target: ArtifactTarget, conversationOwner: ConversationOwner? = nil, showsConversation: Bool = true) -> AppRoute {
        .artifact(owner: target.owner, path: target.path, title: target.title, conversationOwner: conversationOwner, showsConversation: showsConversation)
    }
}

/// Pushes a route onto the current destination's page path, so the page
/// survives a move to another destination and back.
struct OpenRouteAction {
    let open: (AppRoute) -> Void
    func callAsFunction(_ route: AppRoute) { open(route) }
}

extension EnvironmentValues {
    @Entry var openRoute = OpenRouteAction { _ in }
    /// Changes each time Review becomes the destination.
    @Entry var reviewVisit = 0
}

/// A NavigationStack whose pushes all go through one page path.
struct RouteStack<Root: View>: View {
    @Binding var path: [AppRoute]
    @ViewBuilder let root: () -> Root
    var body: some View {
        NavigationStack(path: $path) {
            root()
                .navigationDestination(for: AppRoute.self) { RouteDestination(route: $0) }
        }
        .environment(\.openRoute, OpenRouteAction { path.append($0) })
    }
}

struct RouteDestination: View {
    let route: AppRoute
    @Environment(AppStore.self) private var store
    private var goalEdits: PageSessionRoute { store.goalEdits }
    var body: some View {
        destination
            .onChange(of: goalEdits.unfinishedKeys) { _, _ in goalEdits.prune() }
            .onChange(of: goalEdits.goals.unfinishedKeys) { _, _ in goalEdits.goals.prune() }
    }

    @ViewBuilder private var destination: some View {
        switch route {
        case .goals: AllGoalsView()
        case .goal(let id): GoalView(goalID: id, editOwner: goalEdits)
        case .chief: ChiefView(editOwner: goalEdits)
        case .conversation(let owner, let id, let linked):
            if case .goal = owner {
                ContentUnavailableView("Goal conversation retired", systemImage: "bubble.left", description: Text("Open the goal to talk to its holder."))
            } else {
                ConversationScreen(owner: owner, conversationID: id, linked: linked)
            }
        case .orchestrator(let id): OrchestratorView(managerID: id, editOwner: goalEdits)
        case .ticket(let id): TicketView(ticketID: id)
        case .artifact(let owner, let path, let title, let conversationOwner, let showsConversation):
            ArtifactViewerView(owner: owner, path: path, title: title, showsConversation: showsConversation, conversationOwner: conversationOwner)
        case .ideas: IdeasView()
        case .usage: UsageView()
        case .vps: VPSView()
        case .settings: SettingsView()
        }
    }
}
