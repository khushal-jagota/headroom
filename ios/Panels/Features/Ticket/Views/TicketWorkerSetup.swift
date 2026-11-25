import SwiftUI

struct TicketWorkerSetup: View {
    let ticket: TicketDetail
    let refresh: () async -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var backends: [TicketBackend] = []
    @State private var backend: String
    @State private var model: String
    @State private var effort: String
    @State private var action = SheetAction()
    @State private var loadError: String?
    private var selected: TicketBackend? { backends.first { $0.id == backend } }
    private var efforts: [String] { selected?.availableModels.first { $0.id == model }?.reasoningEffortOptions ?? selected?.reasoningEffortOptions ?? [] }

    init(ticket: TicketDetail, refresh: @escaping () async -> Void) {
        self.ticket = ticket; self.refresh = refresh
        _backend = State(initialValue: ticket.employeeBackend)
        _model = State(initialValue: ticket.employeeLaunchModel ?? "")
        _effort = State(initialValue: ticket.employeeLaunchReasoningEffort ?? "")
    }
    var body: some View {
        SheetForm(title: "Worker setup", confirmTitle: "Save", canConfirm: !backends.isEmpty, action: action, confirm: {
            Task {
                if await action.run({
                    try await store.client.setTicketWorkerConfiguration(ticket.id, backend: backend, model: model.isEmpty ? nil : model, effort: effort.isEmpty ? nil : effort)
                    await refresh()
                }) { dismiss() }
            }
        }) {
            TicketMachineLabel(locale: ticket.locale, selectable: true)
            if backends.isEmpty && loadError == nil { RingSpinner("Loading models…") }
            Picker("Backend", selection: $backend) {
                ForEach(backends) { item in Text(store.backendLabel(item.id)).tag(item.id).disabled(!item.installed) }
            }
            Picker("Model", selection: $model) {
                Text("Backend default").tag("")
                ForEach(selected?.availableModels.filter(\.enabled) ?? []) { item in Text(item.displayName ?? item.id).tag(item.id) }
            }
            if !efforts.isEmpty {
                Picker("Reasoning", selection: $effort) {
                    Text("Backend default").tag("")
                    ForEach(efforts, id: \.self) { Text($0.capitalized).tag($0) }
                }
            }
            if let loadError { Text(loadError).textSelection(.enabled).foregroundStyle(PanelsTheme.error) }
        }
            .task {
                do { let catalog = try await store.client.conversationBackends(owner: .ticket(ticket.id)); backends = catalog.backends }
                catch { loadError = error.localizedDescription }
            }
            .onChange(of: backend) { _, _ in model = selected?.defaultModelId ?? ""; effort = selected?.defaultReasoningEffort ?? "" }
            .onChange(of: model) { _, _ in if !efforts.contains(effort) { effort = "" } }
    }
}
