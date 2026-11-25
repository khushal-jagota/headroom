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
                Text("Notifications are off for Panels.").textSelection(.enabled)
                Button("Open iOS Settings") { openURL(URL(string: UIApplication.openSettingsURLString)!) }
            case .authorized, .provisional, .ephemeral:
                Text(notifications.authorization == .provisional ? "Quiet notifications allowed" : "Notifications allowed").textSelection(.enabled)
                Button("Check connection") { Task { await notifications.refresh(using: store.client) } }
                Button("Open iOS Settings") { openURL(URL(string: UIApplication.openSettingsURLString)!) }
            @unknown default:
                Text("Check notification access in iOS Settings.").textSelection(.enabled)
            }
            Text(notifications.registrationMessage).textSelection(.enabled).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.muted)
        }.listRowBackground(PanelsTheme.raised)
            .task { await notifications.refresh(using: store.client) }
    }
}
