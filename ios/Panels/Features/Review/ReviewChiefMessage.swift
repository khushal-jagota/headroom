import SwiftUI

struct ReviewChiefMessage: View {
    let isActive: Bool
    @Environment(AppStore.self) private var store
    @State private var message = ReviewChiefMessageModel()

    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
            ConversationComposer(model: message.composer, owner: .chief, reviewReply: true,
                placeholder: "Message Chief…", sendReply: { await message.send() }, statusNote: message.note)
                .disabled(message.blocked)
            if let error = message.storageError ?? message.error {
                Text(error).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error)
            }
        }
        .accessibilityIdentifier("review.chief")
        .task(id: "\(store.baseURL)|\(isActive)") {
            guard isActive else { return }
            message.restore(api: store.client)
        }
        .onChange(of: LocalStore.shared.outgoingRevision(LocalStore.server(store.baseURL))) { _, _ in
            guard isActive else { return }
            message.refreshAttempt()
        }
    }
}
