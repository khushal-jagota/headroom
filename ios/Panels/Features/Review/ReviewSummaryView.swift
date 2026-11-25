import SwiftUI

/// The summary screen: what waits, in two tones at one upper-centre baseline, the quiet
/// return button, and Message Chief anchored below. The app opens on it.
struct ReviewSummaryView: View {
    let summary: ReviewSummary
    /// Agents working right now; the line is left out at zero, with no layout shift.
    let workingCount: Int
    let isActive: Bool
    let onReturn: () -> Void
    @Environment(AppStore.self) private var store
    @Environment(\.screenTextEntry) private var textEntry

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Text(summary.headline).panelsScaledFont(size: 31, relativeTo: .largeTitle, weight: .bold)
                        .foregroundStyle(PanelsTheme.Web.strong)
                        .accessibilityIdentifier("review.summary.headline")
                    Text(summary.detail).panelsScaledFont(size: 31, relativeTo: .largeTitle, weight: .semibold)
                        .foregroundStyle(PanelsTheme.Web.heroMuted).padding(.top, 2)
                        .accessibilityIdentifier("review.summary.detail")
                    if let title = summary.returnTitle {
                        Button(title) { textEntry?.endEditing(); onReturn() }
                            .buttonStyle(ReviewQuietButtonStyle())
                            .padding(.top, 24)
                            .accessibilityIdentifier("review.return")
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
                    Text("\(workingCount) \(workingCount == 1 ? "agent" : "agents") working right now")
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

/// The summary's return button: the quiet matte charcoal of Skip and Return, never the accent.
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
