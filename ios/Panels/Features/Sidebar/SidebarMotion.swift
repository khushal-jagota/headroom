import CoreGraphics

/// The Sidebar's two rules for a finger: where the panel sits under it, and which way a
/// release sends it. Offsets run from 0 (hidden) to the panel width (fully open).
enum SidebarMotion {
    /// A release faster than this, in points per second, goes the way the finger moved.
    static let flickSpeed: CGFloat = 300
    /// How far past fully open the panel can stretch under a finger.
    static let stretchLimit: CGFloat = 30

    /// True when a release leaves the Sidebar open. A flick goes its own way; a slower
    /// release goes to the nearer side.
    static func opens(offset: CGFloat, velocity: CGFloat, width: CGFloat) -> Bool {
        if abs(velocity) > flickSpeed { return velocity > 0 }
        return offset > width / 2
    }

    /// The panel's offset for a finger at `raw`: 1:1 between hidden and fully open, then
    /// growing resistance that never passes `stretchLimit`.
    static func position(_ raw: CGFloat, width: CGFloat) -> CGFloat {
        guard raw > width else { return max(0, raw) }
        let excess = raw - width
        return width + stretchLimit * (1 - 1 / (excess * 0.55 / stretchLimit + 1))
    }
}
