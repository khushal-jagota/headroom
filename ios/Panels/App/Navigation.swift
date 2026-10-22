import SwiftUI

enum AppTab: Hashable { case review, workspace, more }
enum AppRoute: Hashable {
    case chief
    case orchestrator(String)
    case ticket(String)
    case artifacts(owner: ArtifactOwner, directory: String)
    case artifact(owner: ArtifactOwner, path: String, title: String)
    case ideas, feedback, usage, vps, settings
}

struct RouteDestination: View {
    let route: AppRoute
    var body: some View {
        destination.toolbar(.hidden, for: .tabBar)
    }

    @ViewBuilder private var destination: some View {
        switch route {
        case .chief: ChiefView()
        case .orchestrator(let id): OrchestratorView(managerID: id)
        case .ticket(let id): TicketView(ticketID: id)
        case .artifacts(let owner, let directory): ArtifactListView(owner: owner, directory: directory)
        case .artifact(let owner, let path, let title): ArtifactViewerView(owner: owner, path: path, title: title)
        case .ideas: IdeasView()
        case .feedback: FeedbackView()
        case .usage: UsageView()
        case .vps: VPSView()
        case .settings: SettingsView()
        }
    }
}
