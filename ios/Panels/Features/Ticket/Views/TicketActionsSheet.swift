import SwiftUI
import UIKit

/// The rare Ticket actions. Each waits for the server inside the sheet and shows its failure inline.
struct TicketActionsSheet: View {
    let ticket: TicketDetail
    let canChange: Bool
    let baseURL: URL
    let askForRecap: () async throws -> Void
    let setBacklog: () async throws -> Void
    @State private var action = SheetAction()
    @State private var running: String?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        SheetForm(title: "Ticket", action: action) {
            Section {
                if canChange {
                    row("Ask for a recap", systemImage: "arrow.clockwise", id: "recap", work: askForRecap)
                    row(ticket.isBacklogged ? "Resume ticket" : "Backlog ticket", systemImage: ticket.isBacklogged ? "play" : "pause", id: "backlog", work: setBacklog)
                }
                Button { copyLink() } label: { Label("Copy link", systemImage: "doc.on.doc") }
                    .foregroundStyle(.primary).accessibilityIdentifier("ticket-action-copy-link")
            }
        }.presentationDetents([.medium])
    }

    private func row(_ title: String, systemImage: String, id: String, work: @escaping () async throws -> Void) -> some View {
        Button {
            running = id
            Task { if await action.run(work) { dismiss() } }
        } label: {
            HStack { Label(title, systemImage: systemImage); Spacer(); if action.busy && running == id { ProgressView() } }
        }.foregroundStyle(.primary).accessibilityIdentifier("ticket-action-\(id)")
    }

    private func copyLink() {
        var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)
        components?.fragment = "/workspace/\(ticket.id)"
        UIPasteboard.general.url = components?.url
        Haptics.success(); dismiss()
    }
}
