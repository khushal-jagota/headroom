import SwiftUI

struct TicketExpandableText: View {
    let text: String
    let emptyText: String
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var expanded = false
    @State private var fullHeight: CGFloat = 0
    @State private var collapsedHeight: CGFloat = 0

    private var rendered: AttributedString {
        (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
    }
    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.xs) {
            Text(text.isEmpty ? AttributedString(emptyText) : rendered)
                .lineLimit(expanded ? nil : 5)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background {
                    Text(rendered).lineLimit(5).fixedSize(horizontal: false, vertical: true)
                        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { collapsedHeight = $0 }
                        .hidden()
                }
                .background {
                    Text(rendered).fixedSize(horizontal: false, vertical: true)
                        .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { fullHeight = $0 }
                        .hidden()
                }
            if fullHeight > collapsedHeight + 1 || expanded {
                Button(expanded ? "Show less" : "Show more") {
                    Haptics.selection()
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: PanelsTheme.Motion.base)) { expanded.toggle() }
                }.font(PanelsTheme.TypeScale.small).frame(minHeight: 44).buttonStyle(.plain)
            }
        }
        .font(PanelsTheme.TypeScale.body).foregroundStyle(PanelsTheme.faint)
        .onChange(of: text) { _, _ in expanded = false }
    }
}
