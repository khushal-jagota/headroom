import SwiftUI

@main struct PanelsApp: App {
    @UIApplicationDelegateAdaptor(PanelsNotificationDelegate.self) private var notificationDelegate
    @State private var notifications = NotificationController.shared
    @State private var store = AppStore.configuredBaseURL.map { AppStore(baseURL: $0) }
    @Environment(\.scenePhase) private var scenePhase
    var body: some Scene {
        WindowGroup {
            Group {
#if DEBUG
                if let store, ProcessInfo.processInfo.arguments.contains("--conversation-smoke") {
                    if ProcessInfo.processInfo.arguments.contains("--conversation-detail-pages") {
                        ConversationDetailSmokeView().environment(store)
                    } else {
                        ConversationSmokeView().environment(store)
                    }
                } else {
                    home
                }
#else
                home
#endif
            }
                .environment(notifications)
                .preferredColorScheme(.dark)
                .tint(PanelsTheme.accent)
                .onChange(of: scenePhase, initial: true) { _, phase in KeepAwake.shared.foreground = phase == .active }
                .onOpenURL { url in
                    // The Lock Screen card and the Island link here. Only Review's summary opens.
                    guard url == ReviewActivityAttributes.reviewURL,
                          let server = AppStore.configuredBaseURL.flatMap({ NotificationRouting.origin($0.absoluteString) }) else { return }
                    notifications.receivedTap(NotificationOpen(id: UUID(), serverURL: server, destination: .review(ticketID: nil)))
                }
        }
    }

    @ViewBuilder private var home: some View {
        if let store {
            AppShell().environment(store)
        } else {
            ServerAddressSheet(initialAddress: "", isFirstLaunch: true) { url in
                SettingsServerAddress.save(url)
                store = AppStore(baseURL: url)
            }
        }
    }
}
