import SwiftUI

struct TicketExpandableText: View {
    let text: String
    let emptyText: String
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var expanded = false
    @State private var fullHeight: CGFloat = 0
    @State private var collapsedHeight: CGFloat = 0
    private var truncated: Bool { fullHeight > collapsedHeight + 1 }
    private var rendered: AttributedString {
        (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
    }
    var body: some View {
        Text(text.isEmpty ? AttributedString(emptyText) : rendered)
            .lineLimit(expanded ? nil : 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                Text(rendered).lineLimit(8).fixedSize(horizontal: false, vertical: true)
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { collapsedHeight = $0 }
                    .hidden()
            }
            .background {
                Text(rendered).fixedSize(horizontal: false, vertical: true)
                    .onGeometryChange(for: CGFloat.self) { $0.size.height } action: { fullHeight = $0 }
                    .hidden()
            }
            .mask {
                VStack(spacing: 0) {
                    Rectangle()
                    LinearGradient(colors: [.black, truncated && !expanded ? .clear : .black], startPoint: .top, endPoint: .bottom)
                        .frame(height: PanelsTheme.Space.xl)
                }
            }
            .contentShape(Rectangle())
            .onTapGesture {
                guard truncated || expanded else { return }
                withAnimation(reduceMotion ? nil : .easeInOut(duration: PanelsTheme.Motion.slow)) { expanded.toggle() }
            }
            .accessibilityAddTraits(truncated ? .isButton : [])
            .accessibilityHint(truncated ? (expanded ? "Tap to collapse recap" : "Tap to expand recap") : "")
            .accessibilityIdentifier("ticket-recap")
            .font(PanelsTheme.TypeScale.body).foregroundStyle(PanelsTheme.muted)
            .onChange(of: text) { _, _ in expanded = false }
    }
}
