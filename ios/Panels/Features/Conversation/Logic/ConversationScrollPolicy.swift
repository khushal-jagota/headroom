/// Distances are points below the visible thread. A touch suspends following,
/// but only real distance shows Latest. Different show/hide distances prevent
/// flicker while a drag or a changing row hovers around the threshold.
enum ConversationScrollConfiguration {
    static let showLatestDistance: Double = 120
    static let nearLatestDistance: Double = 32
}

struct ConversationScrollPolicy {
    private(set) var followsLatest = true
    private(set) var returningToLatest = false
    private(set) var showLatest = false
    private(set) var hasNewOutput = false
    private(set) var userScrolling = false

    var shouldFollowContent: Bool { followsLatest && !userScrolling && !returningToLatest }

    mutating func open(atUnread: Bool) {
        self = Self()
        followsLatest = !atUnread
    }

    mutating func beginGesture() {
        userScrolling = true
        followsLatest = false
        returningToLatest = false
    }

    mutating func measure(distance: Double) {
        // A following scroll may briefly measure the old offset against new
        // content. That is a layout update, not the reader leaving the bottom.
        guard !shouldFollowContent else { return }
        if distance <= ConversationScrollConfiguration.nearLatestDistance {
            showLatest = false
            hasNewOutput = false
        } else if distance > ConversationScrollConfiguration.showLatestDistance {
            showLatest = true
        }
    }

    mutating func endGesture(distance: Double) {
        userScrolling = false
        followsLatest = distance <= ConversationScrollConfiguration.nearLatestDistance
        measure(distance: distance)
        if followsLatest { showLatest = false; hasNewOutput = false }
    }

    mutating func receiveOutput() {
        if !followsLatest && !returningToLatest { hasNewOutput = true }
    }

    mutating func returnToLatest() {
        userScrolling = false
        followsLatest = true
        returningToLatest = true
    }

    /// Called after native scrolling settles. Growth during the animation still
    /// belongs to this return; the view aligns once to the current bottom edge.
    mutating func finishReturn() {
        guard returningToLatest else { return }
        returningToLatest = false
        showLatest = false
        hasNewOutput = false
    }
}
