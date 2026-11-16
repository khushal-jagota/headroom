import SwiftUI

/// A visible screen owns focus. Draft content remains with its existing model.
struct ScreenTextEntryScope<Content: View>: View {
    @ViewBuilder let content: (ScreenTextEntryContext) -> Content
    @FocusState private var focusedInput: UUID?

    var body: some View {
        let entry = ScreenTextEntryContext(focus: $focusedInput)
        content(entry).environment(\.screenTextEntry, entry)
    }
}

@MainActor struct ScreenTextEntryContext {
    let focus: FocusState<UUID?>.Binding

    func requestFocus(_ id: UUID) { focus.wrappedValue = id }
    func endEditing() { focus.wrappedValue = nil }

    func endEditingBeforeNavigation(_ navigate: () -> Void) {
        endEditing()
        navigate()
    }
}

private struct ScreenTextEntryKey: EnvironmentKey {
    static let defaultValue: ScreenTextEntryContext? = nil
}

extension EnvironmentValues {
    var screenTextEntry: ScreenTextEntryContext? {
        get { self[ScreenTextEntryKey.self] }
        set { self[ScreenTextEntryKey.self] = newValue }
    }
}

extension View {
    func screenTextInput(_ id: UUID) -> some View { modifier(ScreenTextInput(id: id)) }
}

private struct ScreenTextInput: ViewModifier {
    let id: UUID
    @Environment(\.screenTextEntry) private var entry
    @ViewBuilder func body(content: Content) -> some View {
        if let entry { content.focused(entry.focus, equals: id) }
        else { content }
    }
}

/// A value route clears the outgoing screen's focus before its push.
struct ScreenTextEntryNavigationLink: View {
    let title: String
    let route: AppRoute
    @Environment(\.screenTextEntry) private var entry
    @State private var opened = false

    init(_ title: String, value: AppRoute) { self.title = title; route = value }

    var body: some View {
        Button(title) {
            if let entry { entry.endEditingBeforeNavigation { opened = true } }
            else { opened = true }
        }
        .navigationDestination(isPresented: $opened) { RouteDestination(route: route) }
    }
}
