import ActivityKit
import Foundation

/// Watches the system for the Review activity's tokens and tells the notification controller.
/// The app never starts the activity: the server does, by push, so it also returns after iOS
/// ends it. When the server's push starts a new one, the older one ends.
@MainActor final class ReviewLiveActivityController {
    static let shared = ReviewLiveActivityController()
    private var tokens = LiveActivityTokens()
    private var watching = false
    private var changed: (LiveActivityTokens) -> Void = { _ in }

    func watch(changed: @escaping (LiveActivityTokens) -> Void) {
        self.changed = changed
        guard !watching else { return }
        watching = true
        Task { [weak self] in
            for await data in Activity<ReviewActivityAttributes>.pushToStartTokenUpdates {
                self?.set { $0.pushToStart = Self.hex(data) }
            }
        }
        Task { [weak self] in
            for await activity in Activity<ReviewActivityAttributes>.activityUpdates { self?.follow(activity) }
        }
        for activity in Activity<ReviewActivityAttributes>.activities { follow(activity) }
    }

    private func follow(_ activity: Activity<ReviewActivityAttributes>) {
        Task { [weak self] in
            for await data in activity.pushTokenUpdates {
                guard let self else { return }
                self.set { $0.update = .token(Self.hex(data)) }
                await self.endOthers(than: activity)
            }
        }
        Task { [weak self] in
            for await state in activity.activityStateUpdates where state == .ended || state == .dismissed {
                self?.activityEnded(activity)
            }
        }
    }

    private func activityEnded(_ activity: Activity<ReviewActivityAttributes>) {
        // Another activity may already have taken over: only the last one running ends the report.
        guard Activity<ReviewActivityAttributes>.activities.allSatisfy({ $0.id == activity.id || $0.activityState != .active }) else { return }
        set { $0.update = .ended }
    }

    /// One Review activity at a time: the newest, which the server knows, stays.
    private func endOthers(than keep: Activity<ReviewActivityAttributes>) async {
        for other in Activity<ReviewActivityAttributes>.activities where other.id != keep.id {
            await other.end(nil, dismissalPolicy: .immediate)
        }
    }

    private func set(_ edit: (inout LiveActivityTokens) -> Void) {
        var next = tokens
        edit(&next)
        guard next != tokens else { return }
        tokens = next
        changed(next)
    }

    private static func hex(_ data: Data) -> String { data.map { String(format: "%02x", $0) }.joined() }
}
