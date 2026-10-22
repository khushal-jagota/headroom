import SwiftUI

/// Ticket and Orchestrator screens enter through AppRoute.artifacts(owner:directory:).
struct ArtifactListView: View {
    let owner: ArtifactOwner
    var directory: String = ""
    @Environment(AppStore.self) private var store
    @State private var entries: [ArtifactEntry] = []
    @State private var total = 0
    @State private var nextOffset = 0
    @State private var revision: String?
    @State private var isLoading = false
    @State private var error: String?
    @State private var hasLoaded = false

    var body: some View {
        List {
            if let error { LoadErrorView(message: error) { await load() }.listRowBackground(Color.clear) }
            ForEach(entries) { entry in
                HStack(spacing: PanelsTheme.Space.md) {
                    if let target = ArtifactTarget.from(entry: entry, owner: owner, baseURL: store.baseURL) {
                        NavigationLink(value: AppRoute.artifact(owner: target.owner, path: target.path, title: target.title)) {
                            row(entry, symbol: target.kind.symbol)
                        }
                        if let folder = entry.directory {
                            NavigationLink(value: AppRoute.artifacts(owner: owner, directory: folder)) {
                                Image(systemName: "folder").padding(.vertical, PanelsTheme.Space.sm)
                            }.accessibilityLabel("Browse \(entry.label), \(entry.childCount) items")
                                .buttonStyle(.borderless)
                        }
                    } else if let folder = entry.directory {
                        NavigationLink(value: AppRoute.artifacts(owner: owner, directory: folder)) {
                            row(entry, symbol: "folder")
                        }
                    } else {
                        row(entry, symbol: "doc").foregroundStyle(PanelsTheme.faint)
                    }
                }.listRowBackground(Color.clear)
                    .listRowSeparatorTint(PanelsTheme.border)
                    .padding(.vertical, PanelsTheme.Space.xs)
            }
            if hasLoaded && nextOffset < total {
                Button { Haptics.light(); Task { await load(more: true) } } label: {
                    HStack { Text("Load more"); Spacer(); if isLoading { ProgressView() } }
                }.disabled(isLoading).listRowBackground(Color.clear)
            }
        }
        .listStyle(.plain)
        .overlay {
            if entries.isEmpty && (isLoading || (!hasLoaded && error == nil)) { ProgressView("Loading artifacts…") }
            else if hasLoaded && entries.isEmpty && error == nil {
                ContentUnavailableView("No artifacts yet", systemImage: "folder", description: Text("Files will appear here when they are added."))
            }
        }
        .navigationTitle(directory.isEmpty ? "Artifacts" : (directory as NSString).lastPathComponent)
        .navigationBarTitleDisplayMode(.inline)
        .panelsScreen()
        .task { await load() }
        .refreshable { Haptics.light(); await load() }
    }

    private func row(_ entry: ArtifactEntry, symbol: String) -> some View {
        HStack(spacing: PanelsTheme.Space.lg) {
            Image(systemName: symbol).font(.title3).foregroundStyle(PanelsTheme.accent)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                Text(entry.label).font(PanelsTheme.TypeScale.body).foregroundStyle(PanelsTheme.strong).lineLimit(2)
                Text(entry.href == nil ? "\(entry.childCount) items" : entry.kind.uppercased())
                    .font(PanelsTheme.TypeScale.caption).foregroundStyle(PanelsTheme.faint)
            }.padding(.vertical, PanelsTheme.Space.sm)
        }.frame(minHeight: 44)
    }

    private func load(more: Bool = false) async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            var page = try await store.client.artifacts(owner: owner, directory: directory, offset: more ? nextOffset : 0)
            try Task.checkCancellation()
            // A filesystem edit can reorder the directory between pages. Restart that window.
            let append = more && page.revision == revision
            if more && !append { page = try await store.client.artifacts(owner: owner, directory: directory) }
            try Task.checkCancellation()
            if append {
                let ids = Set(entries.map(\.id))
                entries += page.entries.filter { !ids.contains($0.id) }
            } else { entries = page.entries }
            nextOffset = page.offset + page.entries.count
            total = page.total; revision = page.revision; hasLoaded = true; error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}
