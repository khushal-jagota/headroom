import SwiftUI

struct DeploymentStrip: View {
    @Environment(AppStore.self) private var store
    private var label: String {
        if store.connection == "Reconnecting" { return "Reconnecting to Panels" }
        if store.errors["Deployment"] != nil { return "Deployment status unavailable" }
        guard let status = store.deployment else { return "Connecting to Panels" }
        switch status.state {
        case "preparing": return "Preparing update"
        case "restarting": return "Server restarting"
        case "problem": return status.detail ?? "Deployment needs attention"
        case "back_up", "idle": return "Panels · \(status.deployedSha.map { String($0.prefix(7)) } ?? "Connected")"
        default: return "Deployment status unknown"
        }
    }
    var body: some View {
        HStack(spacing: PanelsTheme.Space.sm) {
            Circle().fill(store.deployment?.state == "problem" ? PanelsTheme.error : PanelsTheme.accent).frame(width: 5, height: 5).accessibilityHidden(true)
            Text(label).font(PanelsTheme.TypeScale.caption).fixedSize(horizontal: false, vertical: true)
            Spacer()
        }
        .foregroundStyle(PanelsTheme.muted)
        .padding(.horizontal, PanelsTheme.Space.lg).padding(.vertical, PanelsTheme.Space.sm)
        .background(PanelsTheme.recessed)
        .accessibilityElement(children: .combine)
    }
}
