import SwiftUI

/// One server action run inside a sheet. The sheet waits on it and shows its failure inline.
@MainActor @Observable final class SheetAction {
    private(set) var busy = false
    private(set) var error: String?

    /// Returns true when the work finished. The caller then dismisses the sheet.
    func run(_ work: () async throws -> Void) async -> Bool {
        guard !busy else { return false }
        busy = true; error = nil; Haptics.light()
        defer { busy = false }
        do { try await work(); Haptics.success(); return true }
        catch { self.error = error.localizedDescription; Haptics.error(); return false }
    }
}

/// The standard sheet: a grouped inset Form with Cancel, a title and an optional confirm button.
struct SheetForm<Content: View>: View {
    let title: String
    var confirmTitle: String? = nil
    var canConfirm = true
    let action: SheetAction
    var confirm: () -> Void = {}
    @ViewBuilder let content: Content
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                content
                if let error = action.error {
                    Section { Text(error).textSelection(.enabled).foregroundStyle(PanelsTheme.error).accessibilityIdentifier("sheet-error") }
                }
            }
            .disabled(action.busy)
            .panelsNavigationTitle(title).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() }.disabled(action.busy) }
                ToolbarItem(placement: .confirmationAction) {
                    if action.busy { ProgressView() }
                    else if let confirmTitle {
                        Button(confirmTitle, action: confirm).disabled(!canConfirm).accessibilityIdentifier("sheet-confirm")
                    }
                }
            }
        }
        .presentationDragIndicator(.visible)
        .interactiveDismissDisabled(action.busy)
    }
}

/// A Form row that picks one value: an accent checkmark marks the chosen row.
struct CheckRow: View {
    let label: String
    let selected: Bool
    var busy = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack {
                Text(label)
                Spacer()
                if busy { ProgressView() }
                else if selected { Image(systemName: "checkmark").foregroundStyle(PanelsTheme.accent) }
            }.contentShape(Rectangle())
        }.foregroundStyle(.primary)
            .accessibilityAddTraits(selected ? .isSelected : []).systemCopyText(label)
    }
}
