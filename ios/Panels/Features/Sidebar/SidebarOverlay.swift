import SwiftUI
import Observation

/// Whether the Sidebar is open, and where a finger holds it while it moves.
@MainActor @Observable final class SidebarPresentation {
    var open = false
    /// The finger's offset: positive while an edge drag opens the Sidebar.
    var drag: CGFloat = 0
    var dragging = false
    var shown: Bool { open || dragging }

    func settle(open: Bool) {
        self.open = open; drag = 0; dragging = false
    }
}

/// The scrim and the sliding panel. It alone reads the drag offsets.
struct SidebarOverlay: View {
    let presentation: SidebarPresentation
    let width: CGFloat
    let current: AppDestination
    let reviewBadge: Int
    let select: (AppDestination) -> Void
    let close: () -> Void
    /// Returns the panel to open after a close drag that fell short.
    let reopen: () -> Void
    /// A close drag resets on its own when the system cancels it.
    @GestureState private var closing: CGFloat = 0
    @State private var appeared = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// How far the panel is out, from 0 (hidden) to 1 (open).
    private var shown: CGFloat {
        let base: CGFloat = presentation.open ? (appeared ? 1 : 0) : 0
        return min(1, max(0, base + (presentation.drag + closing) / width))
    }

    var body: some View {
        ZStack(alignment: .leading) {
            Color.black.opacity(0.55 * shown).ignoresSafeArea()
                .onTapGesture { close() }
                .accessibilityAddTraits(.isButton)
                .accessibilityLabel("Close sidebar")
            SidebarView(current: current, reviewBadge: reviewBadge, select: select)
                .frame(width: width)
                .background(PanelsTheme.Web.surface.ignoresSafeArea())
                .shadow(color: .black.opacity(0.65 * shown), radius: 21, x: 18)
                .offset(x: -width * (1 - shown))
                .accessibilityAddTraits(.isModal)
        }
        .gesture(closeDrag)
        .accessibilityAction(.escape) { close() }
        // A button opening slides the panel in from its hidden place.
        .onAppear {
            withAnimation(reduceMotion ? nil : .timingCurve(0.2, 0.7, 0.5, 1, duration: 0.22)) { appeared = true }
        }
    }

    private var closeDrag: some Gesture {
        DragGesture(minimumDistance: 8, coordinateSpace: .global)
            .updating($closing) { value, state, _ in
                guard presentation.open, abs(value.translation.width) > abs(value.translation.height) else { return }
                state = min(0, value.translation.width)
            }
            .onEnded { value in
                guard presentation.open, abs(value.translation.width) > abs(value.translation.height) else { return }
                if -value.predictedEndTranslation.width > width / 3 { close() } else { reopen() }
            }
    }
}
