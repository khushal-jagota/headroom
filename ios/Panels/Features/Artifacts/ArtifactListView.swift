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
    @State private var selectedArtifact: ArtifactTarget?
    @State private var search = ""
    @State private var refreshVersion = 0
    private var fileCount: Int { entries.reduce(0) { $0 + ($1.directory == nil ? 1 : $1.childCount) } }

    var body: some View {
        searchContent
            .navigationDestination(item: $selectedArtifact) { target in
                ArtifactViewerView(owner: target.owner, path: target.path, title: target.title)
            }
            .navigationTitle("Artifacts")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar(.hidden, for: .tabBar)
            .panelsScreen()
            .task { await load() }
            .refreshable { await load(); refreshVersion += 1 }
    }
    @ViewBuilder private var searchContent: some View {
        if fileCount > 12 { list.searchable(text: $search, placement: .navigationBarDrawer(displayMode: .automatic), prompt: "Find a file") }
        else { list }
    }
    private var list: some View {
        List {
            ForEach(entries.filter { $0.directory != nil }) { folder in
                ArtifactFolderSection(owner: owner, folder: folder, search: search, refreshVersion: refreshVersion, open: { selectedArtifact = $0 })
            }
            fileSection(entries.filter { $0.directory == nil && !$0.key.hasPrefix("linked:") }, title: directory.isEmpty ? "Files" : (directory as NSString).lastPathComponent)
            fileSection(entries.filter { $0.key.hasPrefix("linked:") }, title: "From fields")
            if hasLoaded && nextOffset < total {
                Button { Task { await load(more: true) } } label: {
                    HStack { Text("More files"); Spacer(); if isLoading { ProgressView() } }
                }.disabled(isLoading).listRowBackground(Color.clear)
            }
        }
        .listStyle(.plain)
        .overlay {
            if hasLoaded && entries.isEmpty {
                ContentUnavailableView("No artifacts yet", systemImage: "folder", description: Text("Files will appear here when they are added."))
            }
        }
        .screenLoadState("artifacts", hasContent: hasLoaded, error: error) { await load(); refreshVersion += 1 }
    }
    @ViewBuilder private func fileSection(_ files: [ArtifactEntry], title: String) -> some View {
        if !files.isEmpty {
            Section { ForEach(files.filter { search.isEmpty || $0.label.localizedCaseInsensitiveContains(search) }) { entry in
                ArtifactFileRow(owner: owner, entry: entry, open: { selectedArtifact = $0 })
            } } header: { Text(title).textCase(nil).font(.caption2).foregroundStyle(PanelsTheme.faint) }
        }
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

private struct ArtifactFolderSection: View {
    let owner: ArtifactOwner
    let folder: ArtifactEntry
    let search: String
    let refreshVersion: Int
    let open: (ArtifactTarget) -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var entries: [ArtifactEntry] = []
    @State private var total = 0
    @State private var revision: String?
    @State private var expanded = false
    @State private var loading = false
    @State private var error: String?
    private var visible: [ArtifactEntry] {
        if !search.isEmpty { return entries.filter { $0.label.localizedCaseInsensitiveContains(search) } }
        return expanded ? entries : Array(entries.prefix(5))
    }
    var body: some View {
        Section {
            ForEach(visible) { entry in
                if let directory = entry.directory {
                    NavigationLink(value: AppRoute.artifacts(owner: owner, directory: directory)) {
                        Label(entry.label, systemImage: "folder")
                    }.listRowBackground(Color.clear)
                } else { ArtifactFileRow(owner: owner, entry: entry, open: open) }
            }
            if let error { LoadErrorView(message: error) { await load() }.listRowBackground(Color.clear) }
            if loading && entries.isEmpty { ProgressView().listRowBackground(Color.clear) }
            if search.isEmpty && total > visible.count {
                Button("\(total - visible.count) more") {
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.24)) { expanded = true }
                    if entries.count < total { Task { await load(more: true) } }
                }.disabled(loading).listRowBackground(Color.clear)
            }
        } header: { Text(folder.label.prefix(1).uppercased() + folder.label.dropFirst()).textCase(nil).font(.caption2).foregroundStyle(PanelsTheme.faint) }
        .task(id: refreshVersion) { await load() }
    }
    private func load(more: Bool = false) async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        do {
            var page = try await store.client.artifacts(owner: owner, directory: folder.directory ?? folder.path, offset: more ? entries.count : 0)
            try Task.checkCancellation()
            let append = more && revision == page.revision
            if more && !append { page = try await store.client.artifacts(owner: owner, directory: folder.directory ?? folder.path) }
            try Task.checkCancellation()
            entries = append ? entries + page.entries.filter { item in !entries.contains { $0.id == item.id } } : page.entries
            total = page.total; revision = page.revision; error = nil
        } catch { if !Task.isCancelled { self.error = error.localizedDescription } }
    }
}
