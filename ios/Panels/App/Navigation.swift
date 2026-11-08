import SwiftUI

enum AppTab: Hashable { case review, workspace, more }
enum AppRoute: Hashable {
    case chief
    case goals, goal(String)
    case conversation(owner: ConversationOwner, id: String)
    case orchestrator(String)
    case ticket(String)
    case artifact(owner: ArtifactOwner, path: String, title: String)
    case ideas, feedback, usage, vps, settings
}

struct RouteDestination: View {
    let route: AppRoute
    var body: some View {
        // Roots use the system default. Hide here for every detail route and
        // its pushed children; root visibility overrides compete with this rule.
        destination.toolbar(.hidden, for: .tabBar)
    }

    @ViewBuilder private var destination: some View {
        switch route {
        case .goals: AllGoalsView()
        case .goal(let id): GoalView(goalID: id)
        case .chief: ChiefView()
        case .conversation(let owner, let id):
            switch owner {
            case .goal: ContentUnavailableView("Goal conversation retired", systemImage: "bubble.left", description: Text("Open the goal to talk to its holder."))
            case .chief: ChiefView(openConversationID: id)
            case .manager(let managerID): OrchestratorView(managerID: managerID, openConversationID: id)
            case .ticket(let ticketID): TicketView(ticketID: ticketID, openConversationID: id)
            case .goal:
                Color.clear.panelsScreen()
                    .conversationNavigation(owner: owner, conversationID: id, openConversationID: id)
            }
        case .orchestrator(let id): OrchestratorView(managerID: id)
        case .ticket(let id): TicketView(ticketID: id)
        case .artifact(let owner, let path, let title): ArtifactViewerView(owner: owner, path: path, title: title)
        case .ideas: IdeasView()
        case .feedback: FeedbackView()
        case .usage: UsageView()
        case .vps: VPSView()
        case .settings: SettingsView()
        }
    }
}
