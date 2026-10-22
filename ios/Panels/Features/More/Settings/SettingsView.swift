import SwiftUI

struct SettingsView: View {
    @Environment(AppStore.self) private var store
    @State private var server = ""
    @State private var error: String?
    var body: some View {
        Form {
            Section("Server") {
                TextField("https://panels.example.com", text: $server)
                    .textContentType(.URL).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                if let error { Text(error).foregroundStyle(PanelsTheme.error) }
                Button("Save server") {
                    let text = server.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard let components = URLComponents(string: text), components.scheme == "https", components.host != nil,
                          components.user == nil, components.password == nil,
                          components.query == nil, components.fragment == nil,
                          components.path.isEmpty || components.path == "/", let url = components.url else {
                        error = "Enter an HTTPS server address without a path."; return
                    }
                    error = nil; Haptics.success(); store.configure(baseURL: url)
                }
            }
        }.navigationTitle("Settings").panelsScreen().onAppear { server = store.baseURL.absoluteString }
    }
}
