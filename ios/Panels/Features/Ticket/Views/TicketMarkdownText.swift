import SwiftUI

/// Native reading layout for ticket prose, with inline links handled by the route.
struct TicketMarkdownText: View {
    let text: String
    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.lg) {
            ForEach(Array(text.components(separatedBy: "\n\n").enumerated()), id: \.offset) { _, paragraph in
                block(paragraph)
            }
        }.frame(maxWidth: .infinity, alignment: .leading).textSelection(.enabled)
    }
    @ViewBuilder private func block(_ paragraph: String) -> some View {
        let trimmed = paragraph.trimmingCharacters(in: .whitespacesAndNewlines)
        let hashes = trimmed.prefix(while: { $0 == "#" }).count
        if (1...6).contains(hashes), trimmed.dropFirst(hashes).first == " " {
            Text(inline(String(trimmed.dropFirst(hashes + 1))))
                .font(hashes < 3 ? PanelsTheme.TypeScale.heading : PanelsTheme.TypeScale.body.weight(.semibold))
                .foregroundStyle(PanelsTheme.strong).accessibilityAddTraits(.isHeader)
        } else if trimmed.hasPrefix("```"), trimmed.hasSuffix("```"), trimmed.contains("\n") {
            let lines = trimmed.components(separatedBy: "\n")
            Text(lines.dropFirst().dropLast().joined(separator: "\n"))
                .font(.system(.footnote, design: .monospaced)).foregroundStyle(PanelsTheme.text)
                .frame(maxWidth: .infinity, alignment: .leading).padding(PanelsTheme.Space.md)
                .background(PanelsTheme.recessed, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.md))
        } else {
            Text(inline(paragraph)).font(PanelsTheme.TypeScale.body).lineSpacing(5)
                .foregroundStyle(PanelsTheme.text).frame(maxWidth: .infinity, alignment: .leading)
        }
    }
    private func inline(_ text: String) -> AttributedString {
        (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
    }
}
