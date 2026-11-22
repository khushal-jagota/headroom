import SwiftUI
import UIKit

/// One destination is on screen and mounted at a time, in its own NavigationStack.
/// The shell keeps every destination's page path, so a return shows the page left.
struct AppShell: View {
    @Environment(AppStore.self) private var store
    @Environment(NotificationController.self) private var notifications
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var updateRequired = false
    @State private var savedReviewWork = ReviewSavedWork()
    @State private var reviewSession = ReviewSession()
    @State private var destination: AppDestination = .review
    @State private var paths: [AppDestination: [AppRoute]] = [:]
    @State private var reviewVisit = 0
    /// Drag offsets live in this object, so a moving finger redraws the Sidebar, not the shell.
    @State private var sidebar = SidebarPresentation()

    private var currentPath: [AppRoute] { paths[destination] ?? [] }

    var body: some View {
        GeometryReader { geometry in
            let width = min(geometry.size.width * 0.85, 400)
            ZStack(alignment: .leading) {
                stack
                    .gesture(SidebarEdgePan(enabled: !sidebar.open && currentPath.isEmpty,
                                            changed: { edgeDragChanged($0) },
                                            ended: { edgeDragEnded($0, width: width) }))
                if sidebar.shown {
                    SidebarOverlay(presentation: sidebar, width: width, current: destination, reviewBadge: reviewBadge,
                                   select: select, close: closeSidebar, reopen: { withAnimation(openMotion) { sidebar.settle(open: true) } })
                        .transition(.identity)
                }
            }
        }
        .task {
            while !Task.isCancelled {
                updateRequired = NativeBuildGate.requiresUpdate(store.baseURL)
                do { try await Task.sleep(for: .seconds(1)) } catch { return }
            }
        }
        .overlay {
            if updateRequired {
                VStack(spacing: 16) {
                    Image(systemName: "arrow.down.app").font(.largeTitle)
                    Text(NativeBuildGate.message).font(.title2)
                    Text("Your drafts and pending messages stay on this device.").font(.body)
                    Link("Open TestFlight", destination: URL(string: "itms-beta://")!)
                }
                .multilineTextAlignment(.center)
                .padding(32)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(PanelsTheme.surface)
                .accessibilityIdentifier("ios-update-required")
            }
        }
        .environment(savedReviewWork)
        .task(id: notifications.pendingOpen?.id) { openNotification() }
        .task(id: "push:\(store.baseURL):\(scenePhase == .active)") {
            if scenePhase == .active { await notifications.refresh(using: store.client) }
        }
        .alert("Notification", isPresented: Binding(get: { notifications.routingError != nil }, set: { if !$0 { notifications.routingError = nil } })) {
            Button("OK") { notifications.routingError = nil }
        } message: { Text(notifications.routingError ?? "") }
        .onChange(of: ReviewSavedWork.Key(server: LocalStore.server(store.baseURL), revision: LocalStore.shared.outgoingRevision(LocalStore.server(store.baseURL))), initial: true) { _, key in
            savedReviewWork.refresh(key.server)
        }
        // The shell keeps Review's queue current wherever Khushal is, so the card on
        // screen is always in the latest window and a return finds it as it is.
        .onChange(of: reviewInputs, initial: true) { _, _ in
            reviewSession.update(store: store, savedWork: savedReviewWork,
                                 freshSession: store.connection == "Following changes" ? store.connectionSession : nil)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { reviewSession.leaveApp(session: store.connectionSession) }
        }
        // Owner override: a destination change selects; pushes and back navigation give a light impact.
        .onChange(of: destination) { _, _ in Haptics.selection() }
        .onChange(of: paths) { old, new in if old[destination] != new[destination] { Haptics.light() } }
        .onChange(of: store.baseURL) { _, _ in
            paths = [:]; reviewSession = ReviewSession(); sidebar.settle(open: false)
            if destination != .review { showDestination(.review) }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            if let error = LocalStore.shared.outgoingError ?? LocalStore.shared.saveError { Text(error).font(.footnote).foregroundStyle(PanelsTheme.error).padding(8) }
        }
        .onChange(of: Outbox.shared.networkAvailable) { _, available in
            if available, scenePhase == .active {
                store.resources.resumeActiveReads()
                Task { await store.refresh() }
            }
        }
        .task(id: "outbox:\(store.baseURL):\(scenePhase == .active)") {
            if scenePhase == .active { await Outbox.shared.run(api: store.client) }
        }
        // The one foreground entry into the store. The Sidebar's cards join the scope
        // first and stay in it, so opening the Sidebar never changes the stream.
        .task(id: "\(store.baseURL.absoluteString):\(AppStore.follows(scenePhase))") {
            store.setNavigationDemand("sidebar", [ViewWindowRequest(section: "rail_parents", limit: NavigationScope.maximumWindowRows)])
            if AppStore.follows(scenePhase) { await store.run() }
        }
    }

    // MARK: Destinations

    private var stack: some View {
        let shown = destination
        return RouteStack(path: Binding(get: { paths[shown] ?? [] }, set: { paths[shown] = $0 })) {
            root(shown)
        }
        .id(shown)
    }

    @ViewBuilder private func root(_ destination: AppDestination) -> some View {
        switch destination {
        case .review:
            // Review keeps its own header row; the Sidebar button takes its empty leading corner.
            ReviewView(savedWork: savedReviewWork, session: reviewSession, isActive: currentPath.isEmpty && scenePhase == .active)
            .environment(\.reviewVisit, reviewVisit)
            .toolbar(.hidden, for: .navigationBar)
            .overlay(alignment: .topLeading) { reviewSidebarButton.padding(.leading, 16).padding(.top, 4) }
        case .chief: RouteDestination(route: .chief).toolbar { sidebarToolbarItem }
        case .manager(let id): RouteDestination(route: .orchestrator(id)).toolbar { sidebarToolbarItem }
        case .settings: RouteDestination(route: .settings).toolbar { sidebarToolbarItem }
        }
    }

    private var sidebarToolbarItem: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) { sidebarButton }
    }

    private var sidebarButton: some View {
        Button { openSidebar() } label: { Image(systemName: "sidebar.left") }
            .accessibilityLabel("Open sidebar")
            .accessibilityIdentifier("sidebar-button")
    }

    /// Review has no bar, so its button carries the bar button's own look.
    @ViewBuilder private var reviewSidebarButton: some View {
        if #available(iOS 26.0, *) {
            sidebarButton.buttonStyle(.glass).buttonBorderShape(.circle).controlSize(.large)
        } else {
            sidebarButton.buttonStyle(.bordered).buttonBorderShape(.circle).controlSize(.large)
        }
    }

    private func showDestination(_ next: AppDestination) {
        guard next != destination else { return }
        if next == .review { reviewVisit += 1 }
        var transaction = Transaction(animation: nil)
        transaction.disablesAnimations = true
        withTransaction(transaction) { destination = next }
    }

    private func select(_ next: AppDestination) {
        showDestination(next)
        closeSidebar()
    }

    /// What Review's queue is derived from: the view, the saved work and the stream's session.
    private var reviewInputs: [String] {
        [store.baseURL.absoluteString, store.view?.contentVersion ?? "", savedReviewWork.key.map { "\($0.server):\($0.revision)" } ?? "",
         store.connection, String(store.connectionSession)]
    }

    private var reviewBadge: Int {
        let count = store.counts?.review ?? 0
        guard let presentation = store.presentation, let review = store.review else { return count }
        let pending = ReviewSavedWork.pendingDecisionTickets(in: review.items,
            decisions: savedReviewWork.decisions(LocalStore.server(store.baseURL)))
        return presentation.reviewBadge(count: count, pendingDecisionTickets: pending)
    }

    // MARK: Sidebar

    private var openMotion: Animation? { reduceMotion ? nil : .timingCurve(0.2, 0.7, 0.5, 1, duration: 0.22) }
    private var closeMotion: Animation? { reduceMotion ? nil : .timingCurve(0.4, 0, 1, 1, duration: 0.15) }

    private func openSidebar() {
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
        Haptics.selection()
        withAnimation(openMotion) { sidebar.settle(open: true) }
    }

    private func closeSidebar() {
        withAnimation(closeMotion) { sidebar.settle(open: false) }
    }

    /// A drag from the leading screen edge of a root page opens the Sidebar under the finger.
    private func edgeDragChanged(_ translation: CGFloat) {
        if !sidebar.dragging {
            UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
        }
        sidebar.dragging = true
        sidebar.drag = max(0, translation)
    }

    private func edgeDragEnded(_ predicted: CGFloat, width: CGFloat) {
        let opens = predicted > width / 3
        if opens { Haptics.selection() }
        withAnimation(opens ? openMotion : closeMotion) { sidebar.settle(open: opens) }
    }

    // MARK: Notifications

    /// A Ticket or Goal opens on top of Review; Chief and a Manager open their own destination.
    private func openNotification() {
        guard let destination = notifications.consumeOpen(for: store.baseURL) else { return }
        sidebar.settle(open: false)
        switch destination {
        case .review(let ticketID):
            paths[.review] = ticketID.map { [.ticket($0)] } ?? []
            showDestination(.review)
        case .ticket(let id): paths[.review] = [.ticket(id)]; showDestination(.review)
        case .goal(let id): paths[.review] = [.goal(id)]; showDestination(.review)
        case .orchestrator(let id): paths[.manager(id)] = []; showDestination(.manager(id))
        case .chief: paths[.chief] = []; showDestination(.chief)
        }
    }
}

/// The system screen-edge pan: it claims only drags that start at the leading edge,
/// so taps there, tables, chart scrubbing and card swipes keep working. It is off on
/// pushed pages, where the same edge belongs to the system Back swipe.
private struct SidebarEdgePan: UIGestureRecognizerRepresentable {
    let enabled: Bool
    let changed: (CGFloat) -> Void
    /// The translation projected a short way ahead by the release velocity.
    let ended: (CGFloat) -> Void

    func makeCoordinator(converter: CoordinateSpaceConverter) -> Coordinator { Coordinator() }

    func makeUIGestureRecognizer(context: Context) -> UIScreenEdgePanGestureRecognizer {
        let recognizer = UIScreenEdgePanGestureRecognizer()
        recognizer.edges = .left
        recognizer.delegate = context.coordinator
        return recognizer
    }

    func updateUIGestureRecognizer(_ recognizer: UIScreenEdgePanGestureRecognizer, context: Context) {
        recognizer.isEnabled = enabled
    }

    func handleUIGestureRecognizerAction(_ recognizer: UIScreenEdgePanGestureRecognizer, context: Context) {
        let translation = recognizer.translation(in: recognizer.view).x
        switch recognizer.state {
        case .began, .changed: changed(translation)
        case .ended: ended(translation + 0.15 * recognizer.velocity(in: recognizer.view).x)
        case .cancelled, .failed: ended(0)
        default: break
        }
    }

    /// Some Settings pages push outside the shell's path. The edge under the finger
    /// belongs to Back whenever its navigation stack holds more than its root.
    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        func gestureRecognizerShouldBegin(_ recognizer: UIGestureRecognizer) -> Bool {
            guard let view = recognizer.view else { return false }
            var responder: UIResponder? = view.hitTest(recognizer.location(in: view), with: nil)
            while let current = responder {
                if let navigation = current as? UINavigationController { return navigation.viewControllers.count <= 1 }
                responder = current.next
            }
            return true
        }
    }
}
