import SwiftUI
import UIKit

struct AgentRecap: View {
    let recap: String
    let target: Principal
    let refresh: () async -> Void
    let actions: AgentPageActions
    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.accessibilityVoiceOverEnabled) private var voiceOverEnabled
    @Environment(AppStore.self) private var store
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var expanded = false
    @State private var fullHeight: CGFloat = 0
    @State private var clampedHeight: CGFloat = 0
    @State private var editing = false

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
                            withAnimation(reduceMotion ? nil : .easeInOut(duration: PanelsTheme.Motion.slow)) { expanded.toggle() }
                        }.font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
                            .frame(minHeight: 44, alignment: .leading).accessibilityIdentifier("recap-disclosure")
                    }
                }
            }
            if recap.isEmpty {
                Text("Write a concise recap…").font(PanelsTheme.TypeScale.body).foregroundStyle(PanelsTheme.faint)
            }
            if actions.isSending { ProgressView("Sending recap request…").controlSize(.mini) }
            if let note = actions.note { Text(note).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted) }
        }.onChange(of: recap) { _, _ in expanded = false }
            .contextMenu {
                Button("Copy", systemImage: "doc.on.doc") { UIPasteboard.general.string = recap; Haptics.success() }
                Button("Edit", systemImage: "pencil") { editing = true }
                Button("Ask for a recap", systemImage: "arrow.clockwise") {
                    Task { await actions.requestRecap(client: store.client, target: target, refresh: refresh) }
                }.disabled(actions.isSending)
            }
            .navigationDestination(isPresented: $editing) {
                AgentReadingPage(title: "Recap", originalText: recap, initiallyEditing: true) { text in
                    try await store.client.saveAgentRecap(target: target, recap: text)
                    await refresh()
                }
            }
    }

    private var recapText: some View {
        Text((try? AttributedString(markdown: recap, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(recap))
            .font(PanelsTheme.TypeScale.body).foregroundStyle(PanelsTheme.muted)
            .lineSpacing(PanelsTheme.Space.xs).frame(maxWidth: .infinity, alignment: .leading)
    }
}
