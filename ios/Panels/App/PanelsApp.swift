import SwiftUI

@main struct PanelsApp: App {
    @State private var store = AppStore()
    var body: some Scene {
        WindowGroup {
            Group {
#if DEBUG
                if ProcessInfo.processInfo.arguments.contains("--conversation-smoke") {
                    ConversationSmokeView()
                } else {
                    AppShell()
                }
#else
                AppShell()
#endif
            }.environment(store)
                .preferredColorScheme(.dark)
                .tint(PanelsTheme.accent)
        }
    }
}
