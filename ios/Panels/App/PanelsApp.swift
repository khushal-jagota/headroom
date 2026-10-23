import SwiftUI

@main struct PanelsApp: App {
    @UIApplicationDelegateAdaptor(PanelsNotificationDelegate.self) private var notificationDelegate
    @State private var notifications = NotificationController.shared
    @State private var store = AppStore()
    var body: some Scene {
        WindowGroup {
            Group {
#if DEBUG
                if ProcessInfo.processInfo.arguments.contains("--conversation-smoke") {
                    if ProcessInfo.processInfo.arguments.contains("--conversation-detail-pages") {
                        ConversationDetailSmokeView()
                    } else {
                        ConversationSmokeView()
                    }
                } else {
                    AppShell()
                }
#else
                AppShell()
#endif
            }.conversationPresentationRoot()
                .environment(store).environment(notifications)
                .preferredColorScheme(.dark)
                .tint(PanelsTheme.accent)
        }
    }
}
