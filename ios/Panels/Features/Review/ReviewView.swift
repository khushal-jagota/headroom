import SwiftUI

struct ReviewView: View {
    @Environment(AppStore.self) private var store
    var body: some View {
        List {
            if let error = store.errors["Review"] { LoadErrorView(message: error) { await store.refresh() } }
            if let review = store.review {
                if review.items.isEmpty { Text("Nothing waiting for review").foregroundStyle(PanelsTheme.muted) }
                ForEach(review.items) { item in
                    NavigationLink(value: AppRoute.ticket(item.ticketId)) {
                        VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
                            Text(item.title)
                            Text(item.field.capitalized).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.accent)
                        }.padding(.vertical, PanelsTheme.Space.sm)
                    }.listRowBackground(PanelsTheme.raised)
                }
            } else if store.errors["Review"] == nil { ProgressView() }
        }.navigationTitle("Review").panelsScreen().refreshable { await store.refresh() }
    }
}
