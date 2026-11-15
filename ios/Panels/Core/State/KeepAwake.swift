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
