import SwiftUI

@main struct PanelsApp: App {
    @State private var store = AppStore()
    var body: some Scene {
        WindowGroup {
            AppShell().environment(store)
                .preferredColorScheme(.dark)
                .tint(PanelsTheme.accent)
        }
    }
}
