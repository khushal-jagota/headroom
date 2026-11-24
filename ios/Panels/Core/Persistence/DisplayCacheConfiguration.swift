import Foundation

/// Display snapshots are replaceable and do not carry current stream authority.
enum DisplayCacheConfiguration {
    static let checkpointInterval: TimeInterval = 5
    /// A saved view older than this is not shown at launch.
    static let maximumAge: TimeInterval = 7 * 24 * 60 * 60
    /// A saved view older than this restores without its asks: a turn that waited on one
    /// then has likely moved on, so its card waits for the conversation read instead.
    static let pendingAskMaximumAge: TimeInterval = 60 * 60
}
