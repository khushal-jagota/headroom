import SwiftUI
import UIKit

/// A separate scene window covers sheets as well as the navigation stack, without
/// dismissing either. The app's stream and durable outbox remain mounted beneath it.
@MainActor struct ServerConnectionCoverPresenter: UIViewRepresentable {
    let store: AppStore
    let updateRequired: Bool

    private var shown: Bool { updateRequired || store.serverConnection.isCovered }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeUIView(context: Context) -> WindowAnchor {
        let anchor = WindowAnchor()
        anchor.isUserInteractionEnabled = false
        anchor.onWindowChange = { [weak coordinator = context.coordinator] window in
            coordinator?.attach(to: window)
        }
        return anchor
    }

    func updateUIView(_ view: WindowAnchor, context: Context) {
        context.coordinator.update(store: store, updateRequired: updateRequired, shown: shown)
        context.coordinator.attach(to: view.window)
    }

    static func dismantleUIView(_ view: WindowAnchor, coordinator: Coordinator) {
        view.onWindowChange = nil
        coordinator.hide()
    }

    final class WindowAnchor: UIView {
        var onWindowChange: ((UIWindow?) -> Void)?
        override func didMoveToWindow() {
            super.didMoveToWindow()
            onWindowChange?(window)
        }
    }

    @MainActor final class Coordinator {
        private weak var anchorWindow: UIWindow?
        private weak var previousKeyWindow: UIWindow?
        private var coverWindow: UIWindow?
        private var hosting: UIHostingController<ServerConnectionCoverRoot>?
        private var hiddenWindows: [(window: UIWindow, wasHidden: Bool)] = []
        private var store: AppStore?
        private var updateRequired = false
        private var shown = false

        func update(store: AppStore, updateRequired: Bool, shown: Bool) {
            self.store = store
            self.updateRequired = updateRequired
            self.shown = shown
            presentIfNeeded()
        }

        func attach(to window: UIWindow?) {
            guard let window else { return }
            if anchorWindow !== window {
                hide()
                anchorWindow = window
            }
            presentIfNeeded()
        }

        private func presentIfNeeded() {
            guard shown, let store, let scene = anchorWindow?.windowScene else {
                hide()
                return
            }
            let root = ServerConnectionCoverRoot(store: store, updateRequired: updateRequired)
            if let hosting {
                hosting.rootView = root
                return
            }
            previousKeyWindow = scene.windows.first(where: \.isKeyWindow)
            // End editing before another window becomes key. Draft owners keep the text.
            anchorWindow?.endEditing(true)
            hiddenWindows = scene.windows.map { ($0, $0.accessibilityElementsHidden) }
            for (window, _) in hiddenWindows { window.accessibilityElementsHidden = true }
            let hosting = UIHostingController(rootView: root)
            hosting.overrideUserInterfaceStyle = .dark
            hosting.view.accessibilityViewIsModal = true
            let window = UIWindow(windowScene: scene)
            window.windowLevel = .alert + 1
            window.rootViewController = hosting
            window.accessibilityViewIsModal = true
            self.hosting = hosting
            coverWindow = window
            window.makeKeyAndVisible()
            UIAccessibility.post(notification: .screenChanged, argument: nil)
        }

        func hide() {
            guard let window = coverWindow else { return }
            window.isHidden = true
            window.rootViewController = nil
            coverWindow = nil
            hosting = nil
            for (window, wasHidden) in hiddenWindows { window.accessibilityElementsHidden = wasHidden }
            hiddenWindows = []
            previousKeyWindow?.makeKey()
            previousKeyWindow = nil
            UIAccessibility.post(notification: .screenChanged, argument: nil)
        }
    }
}

private struct ServerConnectionCoverRoot: View {
    let store: AppStore
    let updateRequired: Bool

    var body: some View {
        GeometryReader { geometry in
            ScrollView {
                Group {
                    if updateRequired {
                        ContentUnavailableView {
                            Label {
                                MoreUnavailableText(text: NativeBuildGate.message)
                            } icon: { Image(systemName: "arrow.down.app") }
                        } description: {
                            MoreUnavailableText(text: "Your drafts and pending messages stay on this device.", secondary: true)
                        } actions: {
                            Link("Open TestFlight", destination: URL(string: "itms-beta://")!)
                                .buttonStyle(.borderedProminent)
                        }
                        .accessibilityIdentifier("ios-update-required")
                    } else {
                        ServerUnreachableView(retry: { await store.pull() }, continueOffline: store.serverConnection.continueOffline)
                    }
                }
                .frame(maxWidth: .infinity, minHeight: geometry.size.height)
            }
            .background(PanelsTheme.surface.ignoresSafeArea())
        }
        .foregroundStyle(PanelsTheme.text)
        .tint(PanelsTheme.accent)
        .preferredColorScheme(.dark)
    }
}

private struct ServerUnreachableView: View {
    let retry: () async -> Void
    let continueOffline: () -> Void
    @State private var retrying = false

    var body: some View {
        ContentUnavailableView {
            Label {
                MoreUnavailableText(text: "Panels can’t reach the server.")
            } icon: { Image(systemName: "network.slash") }
        } description: {
            MoreUnavailableText(text: "Check that Tailscale is connected.", secondary: true)
        } actions: {
            VStack(spacing: 12) {
                Button {
                    guard !retrying else { return }
                    retrying = true
                    Task {
                        await retry()
                        retrying = false
                    }
                } label: {
                    Text(retrying ? "Retrying…" : "Try again").frame(minHeight: 32)
                }
                .buttonStyle(.borderedProminent)
                .disabled(retrying)
                .accessibilityIdentifier("server-unreachable.retry")
                Button("Open Tailscale") {
                    let app = URL(string: "tailscale://")!
                    let store = URL(string: "https://apps.apple.com/app/tailscale/id1470499037")!
                    UIApplication.shared.open(UIApplication.shared.canOpenURL(app) ? app : store)
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("server-unreachable.tailscale")
                Button("Continue offline", action: continueOffline)
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("server-unreachable.offline")
            }
        }
    }
}
