import SwiftUI
import UIKit
import Observation

@MainActor @Observable final class AgentPageActions {
    var isSending = false
    var note: String?

    func perform(_ action: () async throws -> Void) async {
        guard !isSending else { return }
        isSending = true; note = nil
        defer { isSending = false }
        do { try await action(); Haptics.success() }
        catch {
            note = error.localizedDescription
            Haptics.error()
        }
    }

    func requestRecap(client: APIClient, target: Principal, refresh: () async -> Void) async {
        await perform {
            let result = try await client.requestRecap(target: target)
            await refresh()
            if !result.accepted { throw APIError(status: 0, message: result.note ?? "Delivery uncertain") }
        }
    }
}

struct AgentPageMenu: View {
    let target: Principal
    let settled: Bool?
    let actions: AgentPageActions
    let refresh: () async -> Void
    @Environment(AppStore.self) private var store
    var body: some View {
        Menu {
            Button("Ask for a recap", systemImage: "arrow.clockwise") {
                Task { await actions.requestRecap(client: store.client, target: target, refresh: refresh) }
            }.disabled(actions.isSending)
            if let settled {
                Button(settled ? "Reopen" : "Settle", systemImage: "checkmark") {
                    Task { await actions.perform {
                        try await store.client.setManagerSettled(target.id, settled: !settled)
                        await refresh()
                    } }
                }.disabled(actions.isSending)
            }
            Button("Copy link", systemImage: "doc.on.doc") {
                var url = URLComponents(url: store.baseURL, resolvingAgainstBaseURL: false)
                url?.fragment = target.kind == "chief" ? "/workspace/chief-of-staff" : "/workspace/manager/\(target.id)"
                UIPasteboard.general.url = url?.url; Haptics.success()
            }
        } label: {
            Image(systemName: "ellipsis")
        }.accessibilityLabel("Page actions").accessibilityIdentifier("agent-page-menu")
    }
}
