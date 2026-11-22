import SwiftUI

struct ArtifactMarkdownView: View {
    let source: String
    let baseURL: URL
    var body: some View {
        ScrollView {
            ArtifactMarkdownContent(source: source, baseURL: baseURL, showsInlineMedia: false)
                .frame(maxWidth: 720, alignment: .leading)
                .padding(PanelsTheme.Space.xl).frame(maxWidth: .infinity)
        }
    }
}

struct ArtifactMarkdownContent: View {
    let source: String
    let baseURL: URL
    var showsInlineMedia = true
    var body: some View {
            LazyVStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
                ForEach(ArtifactMarkdownBlock.parse(source)) { block in
                    switch block.kind {
                    case .rule: Divider().overlay(PanelsTheme.border)
                    case .code:
                        ScrollView(.horizontal) {
                            Text(block.text).font(.system(.footnote, design: .monospaced)).textSelection(.enabled)
                                .padding(PanelsTheme.Space.lg)
                        }.background(PanelsTheme.recessed, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
                    case .heading(let level):
                        inline(block.text, font: PanelsTheme.TypeScale.markdownHeading(level))
                            .foregroundStyle(PanelsTheme.strong).padding(.top, PanelsTheme.Space.sm)
                            .accessibilityAddTraits(.isHeader)
                    case .quote:
                        HStack(alignment: .top, spacing: PanelsTheme.Space.md) {
                            Rectangle().fill(PanelsTheme.accent).frame(width: 2)
                            inline(block.text).foregroundStyle(PanelsTheme.muted)
                        }.fixedSize(horizontal: false, vertical: true)
                    case .paragraph, .list: inline(block.text)
                    }
                }
                if showsInlineMedia { ArtifactInlineMediaPreviews(source: source, baseURL: baseURL) }
            }
    }
    private func inline(_ source: String, font: Font = PanelsTheme.TypeScale.body) -> some View {
        Text((try? AttributedString(markdown: source, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace), baseURL: baseURL)) ?? AttributedString(source))
            .font(font).textSelection(.enabled).lineSpacing(4)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// This view never receives a non-media file, so prose cannot open one in full.
struct ArtifactInlineMediaPreviews: View {
    let source: String
    let baseURL: URL
    var body: some View {
        ForEach(ArtifactInlineMedia.targets(in: source, baseURL: baseURL)) { target in
            ArtifactInlineMediaPreview(target: target)
        }
    }
}

private struct ArtifactInlineMediaPreview: View {
    let target: ArtifactTarget
    @Environment(AppStore.self) private var store
    @Environment(\.openURL) private var openURL
    @State private var document: ArtifactDocument?
    @State private var image: UIImage?
    @State private var error: String?

    var body: some View {
        Group {
            if let image {
                Button { follow() } label: {
                    Image(uiImage: image).resizable().scaledToFit()
                        .frame(maxHeight: PanelsTheme.inlineArtifactMediaHeight)
                }.buttonStyle(.plain)
                    .accessibilityLabel(target.title)
                    .accessibilityIdentifier("inline-artifact-image")
            } else if let document, ArtifactInlineMedia.isVideo(target) {
                ArtifactMediaView(url: document.localURL)
                    .frame(height: PanelsTheme.inlineArtifactMediaHeight)
                    .accessibilityLabel(target.title)
                    .accessibilityIdentifier("inline-artifact-video")
            } else if let error {
                LoadErrorView(message: error) { await load() }
            } else { RingSpinner() }
        }
        .task(id: "\(store.baseURL)|\(target.id)|\(image == nil && document == nil ? store.connectionSession : 0)") { await load() }
    }

    private func follow() {
        if let url = try? store.client.artifactURL(target) { openURL(url) }
    }
    private func load() async {
        do {
            let result = try await store.client.artifactDocument(target)
            try Task.checkCancellation()
            let loadedImage = ArtifactInlineMedia.isPhoto(target) ? UIImage(contentsOfFile: result.localURL.path) : nil
            if ArtifactInlineMedia.isPhoto(target), loadedImage == nil {
                throw APIError(status: 0, message: "This image could not be opened.")
            }
            image = loadedImage; document = result; error = nil
        } catch { if !Task.isCancelled { self.error = ServerReadFailure.note(error) } }
    }
}
