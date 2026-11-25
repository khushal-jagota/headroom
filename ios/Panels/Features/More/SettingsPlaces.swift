import SwiftUI

/// The server row and the places that live behind the Settings gear, above Settings itself.
struct SettingsPlaces: View {
    @Environment(AppStore.self) private var store
    var body: some View {
        Section {
            TimelineView(.periodic(from: .now, by: 1)) { context in
                let state = DeploymentStatusPresentation.resolve(store.deployment, connected: store.connection == "Following changes", readFailed: store.errors["Deployment"] != nil, now: context.date)
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
                .accessibilityIdentifier("settings.deployment")
                .accessibilityElement(children: .combine)
            }
            updatesRow
        }.listRowBackground(PanelsTheme.raised)
        Section {
            place("All goals", symbol: "target", route: .goals)
            place("Usage", symbol: "chart.bar", route: .usage)
            place("Diagnostics", symbol: "server.rack", route: .vps)
        }
    }

    /// The one place that tells of a server update: a short line while one is offered or running.
    private var updatesRow: some View {
        let status = store.updateStatus
        let detail = status.map(\.title).flatMap { ["Up to date", "Update complete"].contains($0) ? nil : $0 }
        return NavigationLink(value: AppRoute.updates) {
            HStack(spacing: PanelsTheme.Space.md) {
                Label("Updates", systemImage: "arrow.triangle.2.circlepath")
                Spacer(minLength: PanelsTheme.Space.sm)
                if let detail {
                    Text(detail).font(PanelsTheme.TypeScale.small)
                        .foregroundStyle(status?.needsAttention == true ? PanelsTheme.error : PanelsTheme.faint)
                }
            }.padding(.vertical, PanelsTheme.Space.xs)
        }
        .accessibilityIdentifier("settings.updates")
    }

    private func place(_ title: String, symbol: String, route: AppRoute) -> some View {
        NavigationLink(value: route) {
            Label(title, systemImage: symbol)
                .padding(.vertical, PanelsTheme.Space.xs)
        }
        .listRowBackground(PanelsTheme.raised)
    }
}
