import SwiftUI

struct ChiefView: View {
    @Environment(AppStore.self) private var store
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xl) {
                if let error = store.errors["Chief"] { LoadErrorView(message: error) { await store.refresh() } }
                if let chief = store.chief { Text(chief.recap).textSelection(.enabled) }
                ConversationPlaceholder()
            }.padding(PanelsTheme.Space.lg)
        }.navigationTitle(store.chief?.label ?? "Chief of Staff").panelsScreen()
    }
}
