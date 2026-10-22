import SwiftUI

struct MoreView: View {
    var body: some View {
        List {
            NavigationLink(value: AppRoute.ideas) { Label("Ideas", systemImage: "lightbulb") }
            NavigationLink(value: AppRoute.feedback) { Label("Feedback", systemImage: "bubble.left") }
            NavigationLink(value: AppRoute.usage) { Label("Usage", systemImage: "chart.bar") }
            NavigationLink(value: AppRoute.vps) { Label("VPS", systemImage: "server.rack") }
            NavigationLink(value: AppRoute.settings) { Label("Settings", systemImage: "gearshape") }
        }.navigationTitle("More").panelsScreen()
    }
}
