import SwiftUI
import UIKit

/// The page actions button for Chief and Manager pages. It opens the actions sheet.
struct AgentPageMenu: View {
    let target: Principal
    let settled: Bool?
    let refresh: () async -> Void
    @Environment(AppStore.self) private var store
    @State private var open = false
    var body: some View {
        Button { open = true } label: { Image(systemName: "ellipsis") }
            .accessibilityLabel("Page actions").accessibilityIdentifier("agent-page-menu")
            .sheet(isPresented: $open) {
                AgentActionsSheet(target: target, settled: settled, baseURL: store.baseURL,
                    askForRecap: {
                        let result = try await store.client.requestRecap(target: target)
                        await refresh()
                        if let note = result.note { throw APIError(status: 0, message: note) }
                    },
                    setSettled: {
                        try await store.client.setManagerSettled(target.id, settled: !(settled ?? false))
                        await refresh()
                    })
            }
    }
}

/// The rare page actions. Each waits for the server inside the sheet and shows its failure inline.
struct AgentActionsSheet: View {
    let target: Principal
    let settled: Bool?
    let baseURL: URL
    let askForRecap: () async throws -> Void
    let setSettled: () async throws -> Void
    @State private var action = SheetAction()
    @State private var running: String?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        SheetForm(title: target.kind == "chief" ? "Chief of Staff" : "Manager", action: action) {
            Section {
                row("Ask for a recap", systemImage: "arrow.clockwise", id: "recap", work: askForRecap)
                if let settled {
                    row(settled ? "Reopen" : "Settle", systemImage: "checkmark", id: "settle", work: setSettled)
                }
                Button { copyLink() } label: { Label("Copy link", systemImage: "doc.on.doc") }
                    .foregroundStyle(.primary).accessibilityIdentifier("agent-action-copy-link")
            }
        }.presentationDetents([.medium])
    }

    private func row(_ title: String, systemImage: String, id: String, work: @escaping () async throws -> Void) -> some View {
        Button {
            running = id
            Task { if await action.run(work) { dismiss() } }
        } label: {
            HStack { Label(title, systemImage: systemImage); Spacer(); if action.busy && running == id { ProgressView() } }
        }.foregroundStyle(.primary).accessibilityIdentifier("agent-action-\(id)")
    }

    private func copyLink() {
        var url = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)
        url?.fragment = target.kind == "chief" ? "/workspace/chief-of-staff" : "/workspace/manager/\(target.id)"
        UIPasteboard.general.url = url?.url
        Haptics.success(); dismiss()
    }
}
