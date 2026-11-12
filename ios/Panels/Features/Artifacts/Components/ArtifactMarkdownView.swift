import SwiftUI

struct ArtifactMarkdownView: View {
    let source: String
    let baseURL: URL
    var body: some View {
        ScrollView {
            ArtifactMarkdownContent(source: source, baseURL: baseURL)
                .frame(maxWidth: 720, alignment: .leading)
                .padding(PanelsTheme.Space.xl).frame(maxWidth: .infinity)
        }
    }
}

struct ArtifactMarkdownContent: View {
    let source: String
    let baseURL: URL
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
            }
    }
    private func inline(_ source: String, font: Font = PanelsTheme.TypeScale.body) -> some View {
        Text((try? AttributedString(markdown: source, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace), baseURL: baseURL)) ?? AttributedString(source))
            .font(font).textSelection(.enabled).lineSpacing(4)
            .frame(maxWidth: .infinity, alignment: .leading)
    }
}
