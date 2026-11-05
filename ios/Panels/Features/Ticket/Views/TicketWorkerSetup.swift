import SwiftUI

struct TicketBackendCatalog: Decodable, Sendable {
    let backends: [TicketBackend]
}
struct TicketBackend: Decodable, Identifiable, Sendable {
    struct Model: Decodable, Identifiable, Sendable {
        let modelId: String
        let displayName: String?
        let enabled: Bool
        let reasoningEffortOptions: [String]?
        var id: String { modelId }
    }
    let backendKey: String
    let installed: Bool
    let availableModels: [Model]
    let reasoningEffortOptions: [String]
    let defaultModelId: String?
    let defaultReasoningEffort: String?
    var id: String { backendKey }
}

struct TicketWorkerSetup: View {
    let ticket: TicketDetail
    let refresh: () async -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss
    @State private var backends: [TicketBackend] = []
    @State private var backend: String
    @State private var model: String
    @State private var effort: String
    @State private var busy = false
    @State private var error: String?
    private var selected: TicketBackend? { backends.first { $0.id == backend } }
    private var efforts: [String] { selected?.availableModels.first { $0.id == model }?.reasoningEffortOptions ?? selected?.reasoningEffortOptions ?? [] }

    init(ticket: TicketDetail, refresh: @escaping () async -> Void) {
        self.ticket = ticket; self.refresh = refresh
        _backend = State(initialValue: ticket.employeeBackend)
        _model = State(initialValue: ticket.employeeLaunchModel ?? "")
        _effort = State(initialValue: ticket.employeeLaunchReasoningEffort ?? "")
    }
    var body: some View {
        NavigationStack {
            Form {
                if backends.isEmpty && error == nil { RingSpinner("Loading models…") }
                Picker("Backend", selection: $backend) {
                    ForEach(backends) { item in Text(item.id.capitalized).tag(item.id).disabled(!item.installed) }
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
                if let error { Text(error).foregroundStyle(PanelsTheme.error) }
            }.disabled(busy).panelsScreen().navigationTitle("Worker setup").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(busy) }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Save") {
                            busy = true; error = nil; Haptics.light()
                            Task {
                                do {
                                    try await store.client.setTicketWorkerConfiguration(ticket.id, backend: backend, model: model.isEmpty ? nil : model, effort: effort.isEmpty ? nil : effort)
                                    Haptics.success(); await refresh(); dismiss()
                                } catch { self.error = error.localizedDescription; Haptics.error() }
                                busy = false
                            }
                        }.disabled(busy || backends.isEmpty)
                    }
                }
        }.interactiveDismissDisabled(busy)
            .task {
                do { let catalog: TicketBackendCatalog = try await store.client.get("/api/conversation/backends"); backends = catalog.backends }
                catch { self.error = error.localizedDescription }
            }
            .onChange(of: backend) { _, _ in model = selected?.defaultModelId ?? ""; effort = selected?.defaultReasoningEffort ?? "" }
            .onChange(of: model) { _, _ in if !efforts.contains(effort) { effort = "" } }
    }
}
