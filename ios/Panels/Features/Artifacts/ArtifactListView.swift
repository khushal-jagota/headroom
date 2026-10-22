import SwiftUI

struct ArtifactListView: View {
    let owner: ArtifactOwner
    let directory: String
    @Environment(AppStore.self) private var store
    @State private var entries: [ArtifactEntry] = []
    @State private var total = 0
    @State private var isLoading = false
    @State private var error: String?
    var body: some View {
        List {
            if let error { LoadErrorView(message: error) { await load() } }
            ForEach(entries) { entry in
                if let folder = entry.directory, entry.href == nil {
                    NavigationLink(value: AppRoute.artifacts(owner: owner, directory: folder)) { Label(entry.label, systemImage: "folder") }
                } else {
                    NavigationLink(value: AppRoute.artifact(owner: owner, path: entry.path, title: entry.label)) { Label(entry.label, systemImage: "doc") }
                }
            }
            if entries.count < total { Button("Load more") { Task { await load(more: true) } } }
        }.navigationTitle("Artifacts").panelsScreen()
            .task { await load() }.refreshable { await load() }
    }
    private func load(more: Bool = false) async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            let page = try await store.client.artifacts(owner: owner, directory: directory, offset: more ? entries.count : 0)
            try Task.checkCancellation()
            if more { let ids = Set(entries.map(\.id)); entries += page.entries.filter { !ids.contains($0.id) } }
            else { entries = page.entries }
            total = page.total; error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}
