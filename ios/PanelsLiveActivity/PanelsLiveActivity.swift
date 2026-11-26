import ActivityKit
import SwiftUI
import WidgetKit

@main struct PanelsLiveActivityBundle: WidgetBundle {
    var body: some Widget { ReviewLiveActivity() }
}

struct ReviewLiveActivity: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: ReviewActivityAttributes.self) { context in
            ReviewLockScreenView(state: context.state)
                .activityBackgroundTint(ReviewActivityTone.card)
                .widgetURL(ReviewActivityAttributes.reviewURL)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.center) {
                    ReviewIslandExpanded(state: context.state)
                }
            } compactLeading: {
                ReviewIslandLabel()
            } compactTrailing: {
                ReviewIslandCount(state: context.state)
            } minimal: {
                ReviewIslandCount(state: context.state)
            }
            .widgetURL(ReviewActivityAttributes.reviewURL)
        }
    }
}
