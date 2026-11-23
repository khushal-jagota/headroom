import SwiftUI

struct MoreView: View {
    @Environment(AppStore.self) private var store
    var body: some View {
        List {
            Section {
                UpdateCard()
            }.listRowBackground(PanelsTheme.raised)
            Section {
                destination("Feedback", symbol: "bubble.left", route: .feedback)
            } header: { Text("Work").panelsSectionHeading(.navigation) }
            Section {
                destination("Usage", symbol: "chart.bar", route: .usage)
                destination("VPS", symbol: "server.rack", route: .vps)
                destination("Settings", symbol: "gearshape", route: .settings)
            } header: { Text("System").panelsSectionHeading(.navigation) }
        }
        .listStyle(.insetGrouped)
        .panelsNavigationTitle("More")
        .navigationBarTitleDisplayMode(.inline)
        .panelsScreen()
    }

    private func destination(_ title: String, symbol: String, route: AppRoute) -> some View {
        NavigationLink(value: route) {
            Label(title, systemImage: symbol)
                .padding(.vertical, PanelsTheme.Space.xs)
        }
        .listRowBackground(PanelsTheme.raised)
    }
}
