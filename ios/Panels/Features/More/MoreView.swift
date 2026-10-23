import SwiftUI

struct MoreView: View {
    @Environment(AppStore.self) private var store
    var body: some View {
        List {
            Section {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    let state = DeploymentStatusPresentation.resolve(store.deployment, connected: store.connection == "Following changes", readFailed: store.errors["Deployment"] != nil, now: context.date)
                    NavigationLink(value: AppRoute.vps) {
                        HStack(spacing: PanelsTheme.Space.md) {
                            Image(systemName: state == .problem ? "exclamationmark.triangle" : "server.rack")
                                .foregroundStyle(state == .problem ? PanelsTheme.error : PanelsTheme.accent)
                            VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                                Text("Panels server").foregroundStyle(PanelsTheme.strong)
                                Text([state?.rawValue ?? "Up to date", store.deployment?.detail, store.deployment?.deployedSha.map { "Running \(String($0.prefix(7)))" }].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                                    .font(PanelsTheme.TypeScale.small)
                                    .foregroundStyle(state == .problem ? PanelsTheme.error : PanelsTheme.faint)
                            }
                        }.padding(.vertical, PanelsTheme.Space.xs)
                    }.accessibilityIdentifier("more.deployment")
                }
            }.listRowBackground(PanelsTheme.raised)
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
