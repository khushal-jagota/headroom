import SwiftUI

struct DeploymentInterruptionsView: View {
    var conversationID: String? = nil
    @Environment(AppStore.self) private var store
    @State private var pending: String?
    @State private var actionMessage: String?

    private var entries: [DeploymentInterruption] {
        (store.interruptions?.accounts.flatMap(\.entries) ?? []).filter {
            conversationID == nil || $0.conversationId == conversationID
        }
    }
    private var unavailable: [DeploymentInterruptionSummary.Account] {
        store.interruptions?.accounts.filter { $0.captureState == "unavailable" } ?? []
    }
    var body: some View {
        if !entries.isEmpty || (conversationID == nil && (!unavailable.isEmpty || store.errors["Interrupted sessions"] != nil)) {
            DisclosureGroup(entries.isEmpty ? "Interruption account unavailable" : "\(entries.count) interrupted \(entries.count == 1 ? "session" : "sessions")") {
                ScrollView {
                VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
                    if conversationID == nil {
                        ForEach(unavailable) { account in
                            Text("Interruption account unavailable. \(account.detail ?? "The deploy did not supply reliable evidence.")")
                        }
                        if let error = store.errors["Interrupted sessions"] {
                            Text(error).foregroundStyle(PanelsTheme.error)
                            Button("Try again") { Task { await store.refresh() } }
                        }
                    }
                    ForEach(entries) { entry in
                        VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                            Text(entry.displayName).foregroundStyle(PanelsTheme.strong)
                            Text(entry.recoveryLabel).foregroundStyle(PanelsTheme.faint)
                            Text(entry.detail)
                            if conversationID == nil, let route = route(for: entry) {
                                NavigationLink(entry.recoveryState == "resumed" || entry.recoveryState == "closed" ? "Open workspace · conversation history" : "Open conversation", value: route)
                            }
                            if entry.canRestart, entry.ownerKind == "ticket", let id = entry.ownerId {
                                Button(pending == entry.id ? "Restart requested…" : "Restart Worker") {
                                    pending = entry.id; actionMessage = nil
                                    Task {
                                        do { actionMessage = try await store.client.restartInterruptedWorker(id).message }
                                        catch { actionMessage = error.localizedDescription }
                                        await store.refresh()
                                        pending = nil
                                    }
                                }.disabled(pending != nil)
                            }
                        }
                    }
                    if let actionMessage { Text(actionMessage).accessibilityAddTraits(.updatesFrequently) }
                }.frame(maxWidth: .infinity, alignment: .leading).padding(.top, PanelsTheme.Space.sm)
                }.frame(maxHeight: 240)
            }.font(PanelsTheme.TypeScale.small)
        }
    }
    private func route(for entry: DeploymentInterruption) -> AppRoute? {
        guard entry.conversationId != nil else { return nil }
        switch entry.ownerKind {
        case "chief": return .chief
        case "ticket": return entry.ownerId.map { .ticket($0) }
        case "manager": return entry.ownerId.map { .orchestrator($0) }
        default: return nil
        }
    }
}
