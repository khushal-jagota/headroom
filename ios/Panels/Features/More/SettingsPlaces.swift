import SwiftUI

/// The server row and the places that live behind the Settings gear, above Settings itself.
struct SettingsPlaces: View {
    @Environment(AppStore.self) private var store
    var body: some View {
        Section {
            LabeledContent {
                let sha = store.updateStatus?.appSha ?? ""
                Text(store.connection == "Following changes"
                     ? (sha.isEmpty ? "Connected" : "Running \(sha.prefix(7))")
                     : store.connection)
                    .font(PanelsTheme.TypeScale.small)
            } label: {
                Label("Panels server", systemImage: "server.rack")
            }
            .accessibilityIdentifier("settings.deployment")
            .accessibilityElement(children: .combine)
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
        let detail = status.map(\.title).flatMap { $0 == "Up to date" ? nil : $0 }
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
