import SwiftUI

struct AgentRecap: View {
    let recap: String
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOverEnabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var expanded = false
    @State private var fullHeight: CGFloat = 0
    @State private var clampedHeight: CGFloat = 0

    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
            if !recap.isEmpty {
                if typeSize.isAccessibilitySize || voiceOverEnabled {
                    recapText.fixedSize(horizontal: false, vertical: true)
                } else {
                    // Keep each text layout stable while only the clipping frame animates.
                    // Animating lineLimit makes SwiftUI interpolate glyphs between lines.
                    recapText.lineLimit(8)
                        .background { GeometryReader { geometry in Color.clear.onChange(of: geometry.size.height, initial: true) { _, height in clampedHeight = height } } }
                        .hidden().accessibilityHidden(true)
                        .overlay(alignment: .topLeading) {
                            recapText.fixedSize(horizontal: false, vertical: true)
                                .background { GeometryReader { geometry in Color.clear.onChange(of: geometry.size.height, initial: true) { _, height in fullHeight = height } } }
                        }
                        .frame(height: fullHeight > 0 ? (expanded ? fullHeight : clampedHeight) : nil, alignment: .top)
                        .clipped()
                    if fullHeight > clampedHeight + 1 || expanded {
                        Button(expanded ? "Show less" : "Show more") {
                            withAnimation(reduceMotion ? nil : PanelsTheme.Motion.disclosure) { expanded.toggle() }
                        }.font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                            .frame(minHeight: 44, alignment: .leading).accessibilityIdentifier("recap-disclosure")
                    }
                }
            }
        }.clipped()
    }

    private var recapText: some View {
        Text((try? AttributedString(markdown: recap, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(recap))
            .font(PanelsTheme.TypeScale.body).foregroundStyle(PanelsTheme.muted)
            .lineSpacing(PanelsTheme.Space.xs).frame(maxWidth: .infinity, alignment: .leading)
            .textSelection(.enabled)
    }
}
