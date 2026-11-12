import Observation
import UIKit
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

    init(registrationEnabled: Bool = (Bundle.main.object(forInfoDictionaryKey: "PanelsAPNsRegistrationEnabled") as? String) == "YES",
         environment: String = Bundle.main.object(forInfoDictionaryKey: "PanelsAPNsEnvironment") as? String ?? "development") {
        self.registrationEnabled = registrationEnabled
        self.environment = environment
    }

    func refresh(using client: APIClient) async {
        self.client = client
        authorization = await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
        if authorization == .authorized || authorization == .provisional || authorization == .ephemeral {
            UIApplication.shared.registerForRemoteNotifications()
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
              authorization == .authorized || authorization == .provisional || authorization == .ephemeral else { return }
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

final class PanelsNotificationDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
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
        let open = isDefaultTap ? NotificationRouting.parse(response.notification.request.content.userInfo) : nil
        Task { @MainActor in
            if isDefaultTap { NotificationController.shared.receivedTap(open) }
            completionHandler()
        }
    }
}
