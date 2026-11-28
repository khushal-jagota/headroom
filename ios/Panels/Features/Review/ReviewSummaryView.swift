import SwiftUI

/// The summary screen: what waits, in two tones at one upper-centre baseline, the quiet
/// pass buttons, and Message Chief anchored below. The app opens on it.
struct ReviewSummaryView: View {
    let summary: ReviewSummary
    /// Agents working right now; the line is left out at zero, with no layout shift.
    let workingCount: Int
    let isActive: Bool
    /// A pass is starting: its buttons wait.
    var starting = false
    let onPass: (ReviewPass) -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.screenTextEntry) private var textEntry

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Text(summary.headline).textSelection(.enabled).panelsScaledFont(size: 31, relativeTo: .largeTitle, weight: .bold)
                        .foregroundStyle(PanelsTheme.Web.strong)
                        .accessibilityIdentifier("review.summary.headline")
                    Text(summary.detail).textSelection(.enabled).panelsScaledFont(size: 31, relativeTo: .largeTitle, weight: .semibold)
                        .foregroundStyle(PanelsTheme.Web.heroMuted).padding(.top, 2)
                        .accessibilityIdentifier("review.summary.detail")
                    if !summary.passes.isEmpty {
                        HStack(spacing: 12) {
                            ForEach(summary.passes, id: \.self) { pass in
                                Button(pass == .new ? ReviewSummary.newPassTitle : ReviewSummary.skippedPassTitle) { textEntry?.endEditing(); onPass(pass) }
                                    .buttonStyle(ReviewQuietButtonStyle())
                                    .disabled(starting)
                                    .accessibilityIdentifier(Self.identifier(pass, in: summary))
                            }
                        }
                        .padding(.top, 24)
                    }
                }
                .tracking(-0.9).monospacedDigit()
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 20)
                // One optical baseline for every state, a little above the centre.
                .padding(.top, max(PanelsTheme.Space.xl, geometry.size.height * 0.3))
                .frame(minHeight: geometry.size.height, alignment: .top)
                .accessibilityElement(children: .contain)
            }
            .modifier(DecisionForegroundFade())
            .modifier(DecisionScrollViewport())
            .scrollDismissesKeyboard(.interactively)
            .gesture(TapGesture().onEnded { textEntry?.endEditing() })
            .overlay(alignment: .bottomLeading) {
                if workingCount > 0 {
                    Text("\(workingCount) \(workingCount == 1 ? "agent" : "agents") working right now").textSelection(.enabled)
                        .panelsScaledFont(size: 13.5).monospacedDigit().foregroundStyle(PanelsTheme.Web.tertiary)
                        .padding(.leading, 20).padding(.bottom, 12)
                        .accessibilityIdentifier("review.summary.working")
                }
            }
            .panelsRefreshable { await store.pull() }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            ReviewDock { ReviewChiefMessage(isActive: isActive) }
        }
    }
}

extension ReviewSummaryView {
    /// The first button is `review.return`, whichever pass it starts; the skipped pass beside
    /// "Review new" is `review.returnSkipped`.
    static func identifier(_ pass: ReviewPass, in summary: ReviewSummary) -> String {
        pass == summary.passes.first ? "review.return" : "review.returnSkipped"
    }
}

/// The summary's pass buttons: the quiet matte charcoal of Skip and Return, never the accent.
struct ReviewQuietButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .panelsScaledFont(size: 15.5, weight: .semibold)
            .tracking(-0.2)
            .foregroundStyle(PanelsTheme.strong)
            .padding(.horizontal, 22)
            .frame(minHeight: ReviewStyle.actionHeight)
            .background(configuration.isPressed ? PanelsTheme.Web.selected : PanelsTheme.Web.hover, in: RoundedRectangle(cornerRadius: PanelsTheme.Radius.lg))
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
    }
}
