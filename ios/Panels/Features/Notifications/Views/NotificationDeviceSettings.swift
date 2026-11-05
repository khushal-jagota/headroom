import SwiftUI
import UserNotifications

struct NotificationDeviceSettings: View {
    @Environment(AppStore.self) private var store
    @Environment(NotificationController.self) private var notifications
    @Environment(\.openURL) private var openURL

    var body: some View {
        Section("This Device") {
            switch notifications.authorization {
            case .notDetermined:
                Button("Allow notifications") { Task { await notifications.requestPermission() } }
                    .disabled(notifications.requestingPermission)
                    .accessibilityIdentifier("allow-notifications")
            case .denied:
                Text("Notifications are off for Panels.")
                Button("Open iOS Settings") { openURL(URL(string: UIApplication.openSettingsURLString)!) }
            case .authorized, .provisional, .ephemeral:
                Text(notifications.authorization == .provisional ? "Quiet notifications allowed" : "Notifications allowed")
                Button("Check connection") { Task { await notifications.refresh(using: store.client) } }
                Button("Open iOS Settings") { openURL(URL(string: UIApplication.openSettingsURLString)!) }
            @unknown default:
                Text("Check notification access in iOS Settings.")
            }
            Text(notifications.registrationMessage).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
            Text("Change which events notify you on the web.").font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.faint)
        }.listRowBackground(PanelsTheme.raised)
            .task { await notifications.refresh(using: store.client) }
    }
}
