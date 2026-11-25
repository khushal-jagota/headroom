import SwiftUI
import UIKit

/// Current field values and recaps share separate native text and disclosure controls.
struct TicketExpandableText: View {
    let text: String
    let emptyText: String
    var body: some View {
        TicketReadOnlyText(text: text.isEmpty ? emptyText : text, collapsedLines: 8,
            font: .preferredFont(forTextStyle: .body), color: UIColor(PanelsTheme.muted), lineSpacing: 0)
    }
}

struct TicketRecapText: View {
    let text: String
    @ScaledMetric private var size = PanelsTheme.Web.bodySize
    var body: some View {
        TicketReadOnlyText(text: text, collapsedLines: 3, font: .systemFont(ofSize: size),
            color: UIColor(PanelsTheme.Web.text), lineSpacing: 5)
            .accessibilityIdentifier("ticket-recap")
    }
}

private struct TicketReadOnlyText: View {
    let text: String
    let collapsedLines: Int
    let font: UIFont
    let color: UIColor
    let lineSpacing: CGFloat
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var expanded = false
    @State private var fullHeight: CGFloat = 0
    @State private var collapsedHeight: CGFloat = 0
    private var rendered: AttributedString {
        (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            PanelsSelectableText(rendered, font: font, color: color,
                lineSpacing: lineSpacing, lineLimit: expanded ? nil : collapsedLines)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background {
                    Text(rendered).font(Font(font)).lineSpacing(lineSpacing)
                        .lineLimit(collapsedLines).fixedSize(horizontal: false, vertical: true)
                        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { collapsedHeight = $0 }
                        .hidden().accessibilityHidden(true)
                }
                .background {
                    Text(rendered).font(Font(font)).lineSpacing(lineSpacing)
                        .fixedSize(horizontal: false, vertical: true)
                        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { fullHeight = $0 }
                        .hidden().accessibilityHidden(true)
                }
            if fullHeight > collapsedHeight + 1 || expanded {
                Button(expanded ? "Show less" : "Show more") {
                    withAnimation(reduceMotion ? nil : PanelsTheme.Motion.disclosure) { expanded.toggle() }
                }.buttonStyle(.plain).panelsScaledFont(size: PanelsTheme.Web.smallSize)
                    .foregroundStyle(PanelsTheme.Web.secondary)
            }
        }.onChange(of: text) { _, _ in expanded = false }
    }
}
