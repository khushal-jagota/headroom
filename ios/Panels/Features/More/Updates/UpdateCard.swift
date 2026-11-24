import SwiftUI

struct UpdateCard: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
                if let status = store.updateStatus {
                    let starting = store.updateActions.starting(status, now: context.date)
                    let expected = status.expectedRestart(now: context.date, starting: starting)
                    let connected = store.connection == "Following changes" && store.updateFailure == nil
                    let title = !connected ? (expected ? "Updating…" : "Can't reach Panels") : (starting ? "Updating…" : status.title)
                    Label(title, systemImage: status.needsAttention ? "exclamationmark.triangle" : "arrow.triangle.2.circlepath")
                        .foregroundStyle(status.needsAttention ? PanelsTheme.error : PanelsTheme.strong)
                    if let interruption = status.cardInterruption {
                        impact(interruption)
                    }
                    machineRows(status.cardMachines, status: status, actionable: connected)
                    if status.state == "available" { warnings(status.warnings) }
                    if let detail = status.cardDetail, !detail.isEmpty {
                        Text(detail).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    }
                    if connected && status.allowedActions.contains("update") && status.available != nil {
                        Button("Update") { perform(.install, status: status) }
                            .accessibilityIdentifier("updates.install")
                            .disabled(store.updateActions.busy(status, now: context.date))
                    }
                    if connected && status.allowedActions.contains("retry") && status.attempt != nil {
                        Button("Try again") { perform(.retryServer, status: status) }
                            .accessibilityIdentifier("updates.retry-server")
                            .disabled(store.updateActions.busy(status, now: context.date))
                    }
                    if !connected && !expected {
                        Button("Try now") { Task { await store.updateResource.refresh() } }
                    }
                } else {
                    Text(store.updateFailure == nil ? "Connecting to Panels" : "Update status unavailable")
                    Button("Try now") { Task { await store.updateResource.refresh() } }
                }
                if let error = store.updateActions.error {
                    Text(error).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error)
                        .accessibilityIdentifier("updates.error")
                }
            }
            .fixedSize(horizontal: false, vertical: true)
            .padding(.vertical, PanelsTheme.Space.xs)
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("updates.card")
            // The rebuilt app's in-list action buttons, as ScreenRetryButton. Each stays its own target in the row.
            .buttonStyle(.bordered)
        }
    }

    private func impact(_ value: PanelsUpdateStatus.Interruption) -> some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
            Text(value.sentence).foregroundStyle(PanelsTheme.strong)
            if value.stopsAgents && !value.agents.isEmpty {
                Text(value.agents.joined(separator: ", ")).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
            }
        }
    }
    private func warnings(_ values: [String]) -> some View {
        ForEach(values, id: \.self) { warning in
            Text(warning).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error)
        }
    }
    private func machineRows(_ rows: [PanelsUpdateStatus.Machine], status: PanelsUpdateStatus, actionable: Bool) -> some View {
        ForEach(rows) { machine in
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                let layout = dynamicTypeSize.isAccessibilitySize
                    ? AnyLayout(VStackLayout(alignment: .leading, spacing: PanelsTheme.Space.xs))
                    : AnyLayout(HStackLayout(alignment: .firstTextBaseline))
                layout {
                    Text(machine.name).foregroundStyle(PanelsTheme.strong)
                    if !dynamicTypeSize.isAccessibilitySize { Spacer(minLength: PanelsTheme.Space.sm) }
                    Text(machine.label).foregroundStyle(["failed", "unavailable"].contains(machine.state) ? PanelsTheme.error : PanelsTheme.faint)
                }
                if let detail = machine.detail { Text(detail).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint) }
                if actionable && machine.canRetry && machine.attemptId != nil {
                    Button("Try again") { perform(.retryMachine(machine), status: status) }
                        .accessibilityIdentifier("updates.retry-machine.\(machine.machineId)")
                        .disabled(store.updateActions.busy(status))
                }
            }
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("updates.machine.\(machine.machineId)")
        }
    }
    private func perform(_ action: PanelsUpdateAction, status: PanelsUpdateStatus) {
        let api = store.client
        let resource = store.updateResource
        Task {
            guard api.baseURL == store.baseURL else { return }
            await store.updateActions.perform(action, status: status, api: api) { await resource.refresh() }
        }
    }
}

/// The Updates screen behind Settings' Updates row: the shared update card.
struct UpdatesView: View {
    var body: some View {
        List {
            Section { UpdateCard() }.listRowBackground(PanelsTheme.raised)
        }
        .panelsNavigationTitle("Updates").panelsScreen()
    }
}
