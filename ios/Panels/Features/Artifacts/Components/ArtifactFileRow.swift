import SwiftUI

/// Shared entry row for Ticket, Orchestrator and Chief pages. The caller owns its catalog count.
struct ArtifactsRow: View {
    let owner: ArtifactOwner
    let count: Int
    @State private var opened = false
    var body: some View {
        if count > 0 {
            VStack(spacing: 0) {
                Rectangle().fill(PanelsTheme.border).frame(height: 0.5)
                Button { opened.toggle() } label: {
                    HStack { Text("Artifacts"); Spacer(); Text(count.formatted()).foregroundStyle(PanelsTheme.faint) }
                        .font(.body).foregroundStyle(PanelsTheme.text).frame(minHeight: 50).contentShape(Rectangle())
                }.buttonStyle(.plain)
                    .accessibilityLabel("Artifacts")
                    .accessibilityValue("\(count) files, \(opened ? "expanded" : "collapsed")")
                if opened { ArtifactListView(owner: owner) }
            }
        }
    }
}

struct ArtifactFileRow: View {
    let owner: ArtifactOwner
    let entry: ArtifactEntry
    let open: (ArtifactTarget) -> Void
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(AppStore.self) private var store
    @State private var shareDocument: ArtifactDocument?
    @State private var sharing = false
    @State private var downloading = false
    @State private var error: String?
    private var target: ArtifactTarget? { ArtifactTarget.from(entry: entry, owner: owner, baseURL: store.baseURL) }

    var body: some View {
        Button { if let target { open(target) } } label: {
            HStack(spacing: 12) {
                if let target, target.kind == .image, let url = try? store.client.artifactURL(target) {
                    AsyncImage(url: url) { image in image.resizable().scaledToFill() } placeholder: { Image(systemName: "photo").foregroundStyle(PanelsTheme.faint) }
                        .frame(width: 36, height: 36).clipShape(RoundedRectangle(cornerRadius: 6))
                } else { Image(systemName: symbol).font(.system(size: 22)).foregroundStyle(PanelsTheme.faint).frame(width: 36) }
                VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
                    Text(entry.label).panelsScaledFont(size: 16).foregroundStyle(PanelsTheme.text)
                        .lineLimit(typeSize.isAccessibilitySize ? nil : 2).fixedSize(horizontal: false, vertical: true)
                    if typeSize.isAccessibilitySize {
                        Text((entry.path as NSString).pathExtension.lowercased()).font(.caption).foregroundStyle(PanelsTheme.faint)
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
                if downloading { RingSpinner().accessibilityHidden(true) }
                else if !typeSize.isAccessibilitySize { Text((entry.path as NSString).pathExtension.lowercased()).font(.caption).foregroundStyle(PanelsTheme.faint) }
            }.frame(minHeight: target?.kind == .image ? 56 : 48).contentShape(Rectangle())
        }
        .buttonStyle(.plain).disabled(target == nil)
        .listRowBackground(Color.clear).listRowSeparatorTint(PanelsTheme.border)
        .listRowInsets(EdgeInsets(top: 0, leading: 16, bottom: 0, trailing: 16))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(entry.label)
        .accessibilityValue(downloading ? "Downloading for sharing" : (entry.path as NSString).pathExtension.uppercased())
        .accessibilityHint("Opens artifact")
        .accessibilityAction(named: "Share") { Task { await share() } }
        .accessibilityAction(named: "Copy link") { copyLink() }
        .contextMenu {
            Button("Open", systemImage: "arrow.up.right.square") { if let target { open(target) } }
            Button("Share…", systemImage: "square.and.arrow.up") { Task { await share() } }
            Button("Copy link", systemImage: "link") { copyLink() }
        } preview: {
            if let target { NavigationStack { ArtifactViewerView(owner: target.owner, path: target.path, title: target.title, showsConversation: false) }.environment(store).frame(width: 320, height: 420) }
        }
        .sheet(isPresented: $sharing) { if let shareDocument { ArtifactShareSheet(document: shareDocument) } }
        .alert("Couldn't share file", isPresented: Binding(get: { error != nil }, set: { if !$0 { error = nil } })) { Button("OK") { error = nil } } message: { Text(error ?? "") }
    }
    private var symbol: String {
        switch (entry.path as NSString).pathExtension.lowercased() {
        case "json", "jsonl": "curlybraces"
        case "csv", "tsv": "tablecells"
        case "pdf": "doc.richtext"
        case "mp3", "m4a", "wav", "ogg": "waveform"
        default: target?.kind.symbol ?? "doc"
        }
    }
    private func copyLink() {
        guard let target else { return }
        UIPasteboard.general.url = target.previewURL(baseURL: store.baseURL)
        Haptics.success()
    }
    private func share() async {
        guard let target, !downloading else { return }
        downloading = true
        defer { downloading = false }
        do { shareDocument = try await store.client.artifactDocument(target); sharing = true }
        catch { self.error = error.localizedDescription }
    }
}
