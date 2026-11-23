import SwiftUI

struct UpdateCard: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dynamicTypeSize) private var dynamicTypeSize
    @State private var offer: CapturedUpdateOffer?

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
                    if let detail = status.detail, !detail.isEmpty {
                        Text(detail).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    }
                    if connected && status.allowedActions.contains("update") && status.available != nil {
                        Button("Update") { offer = CapturedUpdateOffer(status: status, server: store.baseURL) }
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
        .sheet(item: $offer) { captured in
            UpdateOfferSheet(captured: captured,
                             interruption: { impact(captured.status.interruption) },
                             machines: { machineRows(captured.status.availableMachines, status: captured.status, actionable: false) },
                             warnings: { warnings(captured.status.warnings) },
                             close: { offer = nil })
        }
        .onChange(of: store.baseURL) { _, _ in offer = nil }
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

/// The confirmation, in the shared sheet: it waits on the install and shows a refusal inside.
private struct UpdateOfferSheet<Interruption: View, Machines: View, Warnings: View>: View {
    @Environment(AppStore.self) private var store
    @State private var action = SheetAction()
    let captured: CapturedUpdateOffer
    @ViewBuilder let interruption: () -> Interruption
    @ViewBuilder let machines: () -> Machines
    @ViewBuilder let warnings: () -> Warnings
    let close: () -> Void

    private var connected: Bool { store.connection == "Following changes" && store.updateFailure == nil }

    var body: some View {
        SheetForm(title: "Update Panels", confirmTitle: "Update",
                  canConfirm: connected && !store.updateActions.busy(captured.status), action: action,
                  confirm: confirm) {
            Section { interruption() }
            Section { machines() }
            Section { warnings() }
        }
    }

    private func confirm() {
        let api = store.client
        let resource = store.updateResource
        let actions = store.updateActions
        Task {
            let done = await action.run {
                guard captured.server == store.baseURL, api.baseURL == store.baseURL, connected else {
                    throw UpdateOfferRefusal(message: "Panels can't be reached. Try again when it is back.")
                }
                await actions.perform(.install, status: captured.status, api: api) { await resource.refresh() }
                if let error = actions.error { throw UpdateOfferRefusal(message: error) }
            }
            if done { close() }
        }
    }
}

private struct UpdateOfferRefusal: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

private struct CapturedUpdateOffer: Identifiable {
    let id = UUID()
    let status: PanelsUpdateStatus
    let server: URL
}
