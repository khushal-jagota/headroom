import SwiftUI

struct MoreView: View {
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        List {
            Section("Work") {
                destination("Ideas", symbol: "lightbulb", route: .ideas)
                destination("Feedback", symbol: "bubble.left", route: .feedback)
            }
            Section("System") {
                destination("Usage", symbol: "chart.bar", route: .usage)
                destination("VPS", symbol: "server.rack", route: .vps)
                destination("Settings", symbol: "gearshape", route: .settings)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("More")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Done") { Haptics.light(); dismiss() }
            }
        }
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
