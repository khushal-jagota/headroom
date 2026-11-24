import SwiftUI

enum AttachChoice { case picture, camera, file, command }

/// What the composer's plus button offers. The choice is handed back and the sheet closes.
struct AttachSheet: View {
    let cameraAvailable: Bool
    var commands = true
    let choose: (AttachChoice) -> Void
    @State private var action = SheetAction()
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        SheetForm(title: "Attach", action: action) {
            Section {
                row("Photo Library", systemImage: "photo", .picture)
                if cameraAvailable { row("Camera", systemImage: "camera", .camera) }
                row("Choose File", systemImage: "doc", .file)
            }
            if commands { Section { row("Command", systemImage: "slash.circle", .command) } }
        }.presentationDetents([.medium])
    }

    private func row(_ title: String, systemImage: String, _ choice: AttachChoice) -> some View {
        Button { choose(choice); dismiss() } label: { Label(title, systemImage: systemImage) }
            .foregroundStyle(.primary)
    }
}
