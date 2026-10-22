import SwiftUI

enum AppTab: Hashable { case review, workspace, more }
enum AppRoute: Hashable {
    case chief
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
        case .chief: ChiefView()
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
