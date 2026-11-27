import UIKit

/// Stops the screen from auto-locking while any voice recording runs.
/// Each recorder holds and releases by its own identity, so two recorders
/// never undo each other. The screen locks normally once no recorder holds,
/// or while the app is not in the foreground.
@MainActor final class KeepAwake {
    static let shared = KeepAwake()

    private var holders = Set<ObjectIdentifier>()
    private var applied: Bool?
    private let apply: @MainActor (Bool) -> Void

    var foreground = true { didSet { update() } }
    var isAwake: Bool { foreground && !holders.isEmpty }

    init(apply: @escaping @MainActor (Bool) -> Void = { UIApplication.shared.isIdleTimerDisabled = $0 }) {
        self.apply = apply
    }

    func hold(_ holder: AnyObject) {
        holders.insert(ObjectIdentifier(holder)); update()
    }

    func release(_ holder: AnyObject) {
        holders.remove(ObjectIdentifier(holder)); update()
    }

    private func update() {
        let awake = isAwake
        guard applied != awake else { return }
        applied = awake
        apply(awake)
    }
}

/// The keyboard's own dictation key is the system's, so no recorder of ours runs.
/// This holds the screen while the keyboard is in dictation, and lets go when the
/// keyboard changes mode or goes away.
@MainActor final class DictationAwake {
    private let awake: KeepAwake
    /// Lives as long as the app, so the observers are never removed.
    private var observers: [NSObjectProtocol] = []

    init(awake: KeepAwake = .shared, center: NotificationCenter = .default) {
        self.awake = awake
        observers = [
            center.addObserver(forName: UITextInputMode.currentInputModeDidChangeNotification, object: nil, queue: .main) { [weak self] note in
                let language = (note.object as? UITextInputMode)?.primaryLanguage
                MainActor.assumeIsolated { self?.inputMode(language) }
            },
            center.addObserver(forName: UIResponder.keyboardDidHideNotification, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.inputMode(nil) }
            },
        ]
    }

    func inputMode(_ primaryLanguage: String?) {
        if primaryLanguage == "dictation" { awake.hold(self) } else { awake.release(self) }
    }
}
