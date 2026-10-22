import SwiftUI

struct AgentRecap: View {
    let recap: String
    let target: Principal
    let refresh: () async -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var expanded = false
    @State private var fullHeight: CGFloat = 0
    @State private var clampedHeight: CGFloat = 0
    @State private var sending = false
    @State private var note: String?

    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.sm) {
            if !recap.isEmpty {
                recapText.lineLimit(expanded ? nil : 6)
                    .background { GeometryReader { geometry in Color.clear.onChange(of: geometry.size.height, initial: true) { _, height in if !expanded { clampedHeight = height } } } }
                    .overlay(alignment: .topLeading) {
                        recapText.fixedSize(horizontal: false, vertical: true)
                            .background { GeometryReader { geometry in Color.clear.onChange(of: geometry.size.height, initial: true) { _, height in fullHeight = height } } }
                            .hidden().accessibilityHidden(true)
                    }
                if fullHeight > clampedHeight + 1 || expanded {
                    Button(expanded ? "Show less" : "Show more") {
                        Haptics.selection()
                        withAnimation(reduceMotion ? nil : .easeInOut(duration: PanelsTheme.Motion.slow)) { expanded.toggle() }
                    }.font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                        .frame(minHeight: 44, alignment: .leading).accessibilityIdentifier("recap-disclosure")
                }
            }
            HStack(spacing: PanelsTheme.Space.sm) {
                Button("Ask for a recap") {
                    guard !sending else { return }
                    Haptics.light(); sending = true; note = nil
                    Task {
                        defer { sending = false }
                        do {
                            let result = try await store.client.requestRecap(target: target)
                            note = result.note
                            if result.accepted { Haptics.success() }
                            await refresh()
                        } catch { note = error.localizedDescription }
                    }
                }.disabled(sending).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                    .frame(minHeight: 44).accessibilityIdentifier("request-recap")
                if sending { ProgressView().controlSize(.mini) }
            }
            if let note { Text(note).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted).accessibilityAddTraits(.updatesFrequently) }
        }.onChange(of: recap) { _, _ in expanded = false }
    }

    private var recapText: some View {
        Text((try? AttributedString(markdown: recap, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(recap))
            .font(PanelsTheme.TypeScale.body).foregroundStyle(PanelsTheme.muted)
            .lineSpacing(PanelsTheme.Space.xs).frame(maxWidth: .infinity, alignment: .leading)
    }
}
