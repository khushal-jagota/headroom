import Foundation
import Observation
#if canImport(UIKit)
import UIKit
#endif
import UserNotifications

@MainActor @Observable final class NotificationController {
    static let shared = NotificationController()
    var authorization: UNAuthorizationStatus = .notDetermined
    var pendingOpen: NotificationOpen?
    var routingError: String?
    var registrationMessage = "Server delivery is not connected yet."
    var requestingPermission = false
    private var deviceToken: String?
    private var registeredDevice: NativePushRegistration?
    private var registeredServer: URL?
    private var registering = false
    private var client: APIClient?
    private let registrationEnabled: Bool
    private let environment: String
    private let replyOutbox: Outbox
    private let selectedServer: () -> URL
    private let postLocalNotification: (UNNotificationRequest) async -> Void

    init(registrationEnabled: Bool = (Bundle.main.object(forInfoDictionaryKey: "PanelsAPNsRegistrationEnabled") as? String) == "YES",
         environment: String = Bundle.main.object(forInfoDictionaryKey: "PanelsAPNsEnvironment") as? String ?? "development",
         replyOutbox: Outbox? = nil,
         selectedServer: @escaping () -> URL = { AppStore.configuredBaseURL },
         postLocalNotification: @escaping (UNNotificationRequest) async -> Void = { request in
             try? await UNUserNotificationCenter.current().add(request)
         }) {
        self.registrationEnabled = registrationEnabled
        self.environment = environment
        self.replyOutbox = replyOutbox ?? .shared
        self.selectedServer = selectedServer
        self.postLocalNotification = postLocalNotification
    }

    private var permissionAllowsDelivery: Bool {
        if authorization == .authorized || authorization == .provisional { return true }
#if canImport(UIKit)
        if authorization == .ephemeral { return true }
#endif
        return false
    }

    func refresh(using client: APIClient) async {
        self.client = client
        authorization = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        if permissionAllowsDelivery {
#if canImport(UIKit)
            UIApplication.shared.registerForRemoteNotifications()
#endif
            await registerTokenIfPossible()
        } else if registrationEnabled, !registering, let registeredDevice, let registeredServer {
            // Remove the last successful registration when permission is revoked.
            // Use its original server, even if Settings selected another one.
            registering = true
            defer { registering = false }
            do {
                try await NativePushAPI(client: APIClient(baseURL: registeredServer, session: client.session)).unregister(registeredDevice)
                self.registeredDevice = nil
                self.registeredServer = nil
                registrationMessage = "This device is disconnected from notifications."
            } catch { registrationMessage = "Could not disconnect this device: \(error.localizedDescription)" }
        }
    }

    func requestPermission() async {
        guard !requestingPermission else { return }
        requestingPermission = true
        defer { requestingPermission = false }
        do {
            _ = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
            if let client { await refresh(using: client) }
        } catch { registrationMessage = error.localizedDescription }
    }

    func receivedDeviceToken(_ token: Data) {
        deviceToken = token.map { String(format: "%02x", $0) }.joined()
        Task { await registerTokenIfPossible() }
    }

    func registrationFailed(_ error: Error) {
        registrationMessage = registrationEnabled
            ? "Apple could not register this device. Check your connection and try again."
            : "Server delivery is not connected yet."
    }

    func receivedTap(_ open: NotificationOpen?) {
        guard let open else {
            routingError = "This notification has no supported Panels destination. Open Review or Workspace to find the item."
            return
        }
        // Retained until the shell exists, including a launch from a terminated app.
        pendingOpen = open
    }

    func receivedReply(_ reply: NotificationReply?, text: String, notificationID: String) async {
        guard let reply else {
            await reportUnsentReply("This notification cannot receive a reply. Open the conversation in Panels.", notificationID: notificationID)
            return
        }
        // A text response launches the app without requiring a SwiftUI scene.
        // Read the same selected server that an ordinary launch uses.
        let selected = selectedServer()
        let api = client.flatMap { LocalStore.server($0.baseURL) == LocalStore.server(selected) ? $0 : nil }
            ?? APIClient(baseURL: selected)
        do {
            try replyOutbox.enqueueNotificationReply(reply, text: text, notificationID: notificationID, api: api)
        } catch {
            await reportUnsentReply("Reply was not sent: \(error.localizedDescription)", notificationID: notificationID)
            return
        }
        // Persist first. iOS grants a short action-handling window, not a
        // repeating background worker. The foreground drainer resumes leftovers.
        await withTaskGroup(of: Void.self) { group in
            group.addTask { await self.replyOutbox.drain(api: api) }
            group.addTask { try? await Task.sleep(for: .seconds(NotificationActions.backgroundAttemptSeconds)) }
            _ = await group.next()
            group.cancelAll()
        }
    }

    private func reportUnsentReply(_ error: String, notificationID: String) async {
        routingError = error
        let content = UNMutableNotificationContent()
        content.title = "Reply not sent"
        content.body = "Your reply was not sent. Open Panels to reply."
        content.sound = .default
        // A tap opens the selected server's Review. Do not reuse an invalid or
        // different-server payload, and never place reply text or errors here.
        content.userInfo = ["server_url": selectedServer().absoluteString, "route": "/#/review"]
        let request = UNNotificationRequest(identifier: notificationID + ".reply-not-sent", content: content, trigger: nil)
        await postLocalNotification(request)
    }

    func consumeOpen(for serverURL: URL) -> NotificationDestination? {
        guard let open = pendingOpen else { return nil }
        pendingOpen = nil
        guard NotificationRouting.origin(serverURL.absoluteString) == open.serverURL else {
            routingError = "This notification belongs to another Panels server. Select that server in Settings, then find the item in Review or Workspace."
            return nil
        }
        return open.destination
    }

    private func registerTokenIfPossible() async {
        guard registrationEnabled else {
            registrationMessage = "Server delivery is not connected yet."
            return
        }
        guard let deviceToken, let client, !registering,
              permissionAllowsDelivery else { return }
        guard ["development", "production"].contains(environment), let bundleID = Bundle.main.bundleIdentifier else {
            registrationMessage = "Push configuration is incomplete."
            return
        }
        let device = NativePushRegistration(deviceToken: deviceToken, bundleId: bundleID, environment: environment)
        guard device != registeredDevice || client.baseURL != registeredServer else { return }
        registering = true
        registrationMessage = "Connecting this device…"
        do {
            try await NativePushAPI(client: client).register(device)
            registeredDevice = device
            registeredServer = client.baseURL
            registrationMessage = "This device is registered for notifications."
        } catch { registrationMessage = "Could not register this device: \(error.localizedDescription)" }
        registering = false
        // A token or server may change while registration is in flight. Refresh
        // that new pair, but leave failures to the explicit retry/next foreground.
        if self.deviceToken != device.deviceToken || self.client?.baseURL != client.baseURL {
            await registerTokenIfPossible()
        }
    }
}

#if canImport(UIKit)
final class PanelsNotificationDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let reply = UNTextInputNotificationAction(identifier: NotificationActions.reply, title: "Reply",
            options: [.authenticationRequired], textInputButtonTitle: "Send", textInputPlaceholder: "Reply")
        center.setNotificationCategories([UNNotificationCategory(identifier: NotificationActions.messageCategory,
            actions: [reply], intentIdentifiers: [])])
        return true
    }
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        NotificationController.shared.receivedDeviceToken(deviceToken)
    }
    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        NotificationController.shared.registrationFailed(error)
    }
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                           withCompletionHandler completionHandler: @escaping @Sendable (UNNotificationPresentationOptions) -> Void) {
        Task { @MainActor in completionHandler([.banner, .list, .sound]) }
    }
    nonisolated func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                           withCompletionHandler completionHandler: @escaping @Sendable () -> Void) {
        let isDefaultTap = response.actionIdentifier == UNNotificationDefaultActionIdentifier
        // Decode before crossing actors; UNNotificationResponse stays on its
        // callback executor. UIKit's response completion must run on main too:
        // the async protocol bridge completed off-main and crashed on resume.
        let userInfo = response.notification.request.content.userInfo
        let open = isDefaultTap ? NotificationRouting.parse(userInfo) : nil
        let text = response.actionIdentifier == NotificationActions.reply ? (response as? UNTextInputNotificationResponse)?.userText : nil
        let reply = text == nil ? nil : NotificationRouting.reply(userInfo)
        let notificationID = response.notification.request.identifier
        Task { @MainActor in
            if isDefaultTap { NotificationController.shared.receivedTap(open) }
            if let text { await NotificationController.shared.receivedReply(reply, text: text, notificationID: notificationID) }
            completionHandler()
        }
    }
}
#endif
