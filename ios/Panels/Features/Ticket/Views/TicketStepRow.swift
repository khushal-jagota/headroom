import SwiftUI

struct TicketStepRow: View {
    let field: TicketWorkerType.Field
    let presentation: TicketPresentation
    private var current: Bool { field.id == presentation.currentField }
    private var complete: Bool { presentation.isComplete(field.id) }

    var body: some View {
        NavigationLink {
            TicketStepPage(ticketID: presentation.ticket.id, field: field, manifest: presentation.manifest)
        } label: {
            HStack(alignment: .firstTextBaseline, spacing: PanelsTheme.Space.md) {
                TicketStepMark(field: field.id, presentation: presentation)
                Text(field.label).foregroundStyle(current ? PanelsTheme.strong : PanelsTheme.muted)
                Spacer(minLength: 0)
                if current && presentation.ticket.pendingProposal?.field == field.id {
                    Text("awaiting approval").foregroundStyle(PanelsTheme.accent).font(PanelsTheme.TypeScale.caption)
                } else if let approval = presentation.ticket.fieldApprovals[field.id] {
                    Text(approval.displayLabel).foregroundStyle(PanelsTheme.faint).font(PanelsTheme.TypeScale.caption)
                }
            }.font(PanelsTheme.TypeScale.body).frame(minHeight: 44).contentShape(Rectangle())
        }.buttonStyle(.plain).accessibilityIdentifier("ticket-step-\(field.id)")
    }
}

struct TicketStepMark: View {
    let field: String
    let presentation: TicketPresentation
    private var current: Bool { field == presentation.currentField }
    var body: some View {
        Group {
            if presentation.isComplete(field) {
                Circle().fill(PanelsTheme.success).accessibilityLabel("Complete")
            } else if current && presentation.ticket.agentState == "errored" {
                Circle().fill(PanelsTheme.error).accessibilityLabel("Error")
            } else if current && presentation.working && presentation.ticket.pendingProposal == nil {
                ProgressView().controlSize(.mini).tint(PanelsTheme.accent).accessibilityLabel("Working")
            } else {
                Circle().strokeBorder(current ? PanelsTheme.accent : PanelsTheme.faint.opacity(0.4), lineWidth: 1.5)
                    .accessibilityLabel(current ? "Current step" : "Not started")
            }
        }.frame(width: 10, height: 10)
    }
}

struct TicketStepPage: View {
    let ticketID: String
    let field: TicketWorkerType.Field
    let manifest: TicketWorkerType
    @Environment(AppStore.self) private var store
    @State private var detail: TicketDetail?
    @State private var error: String?
    @State private var editing = false
    @State private var draft = ""
    @State private var busy = false
    @State private var artifact: AppRoute?
    private var presentation: TicketPresentation? { detail.map { TicketPresentation(ticket: $0, manifest: manifest) } }
    private var pendingProposal: TicketProposal? {
        detail?.pendingProposal.flatMap { $0.field == field.id ? $0 : nil }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
                if let error { LoadErrorView(message: error) { await load() } }
                if let presentation {
                    HStack(spacing: PanelsTheme.Space.md) {
                        TicketStepMark(field: field.id, presentation: presentation)
                        Text(pendingProposal != nil ? "Awaiting approval" : detail?.fieldApprovals[field.id]?.displayLabel ?? "")
                            .font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    }
                    if editing {
                        TextEditor(text: $draft).frame(minHeight: 360).scrollContentBackground(.hidden)
                            .padding(PanelsTheme.Space.md).background(PanelsTheme.recessed)
                    } else {
                        let text = pendingProposal?.body ?? detail?.fieldValues[field.id] ?? ""
                        ArtifactMarkdownContent(source: text.isEmpty ? "Not started" : text, baseURL: store.baseURL)
                    }
                } else if error == nil { ProgressView() }
            }.padding(PanelsTheme.Space.xl)
        }.panelsScreen().navigationTitle(field.label).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                if pendingProposal == nil, presentation?.isComplete(field.id) == true {
                    ToolbarItem(placement: .primaryAction) {
                        Button(editing ? "Done" : "Edit") {
                            if editing {
                                busy = true; Haptics.light()
                                Task {
                                    do {
                                        try await store.client.editTicketField(ticketID, field: field.id, body: draft)
                                        editing = false; Haptics.success(); await load(); await store.refresh()
                                    } catch { self.error = error.localizedDescription; Haptics.error() }
                                    busy = false
                                }
                            } else { draft = detail?.fieldValues[field.id] ?? ""; editing = true }
                        }.disabled(busy)
                    }
                }
                if editing { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { editing = false }.disabled(busy) } }
            }
            .task(id: store.revision) { await load() }
            .navigationDestination(item: $artifact) { RouteDestination(route: $0) }
            .environment(\.openURL, OpenURLAction { url in
                if let route = TicketArtifactRoute.linkedDestination(url, baseURL: store.baseURL, title: url.lastPathComponent) { artifact = route; return .handled }
                return .systemAction
            })
    }
    private func load() async {
        do { let value = try await store.client.ticket(ticketID); try Task.checkCancellation(); detail = value; error = nil }
        catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}
