import SwiftUI
import UIKit
import Observation

@MainActor @Observable final class AgentPageActions {
    var isSending = false
    var note: String?

    func perform(_ action: () async throws -> Void) async {
        guard !isSending else { return }
        isSending = true; note = nil
        defer { isSending = false }
        do { try await action(); Haptics.success() }
        catch {
            note = error.localizedDescription
            Haptics.error()
        }
    }

    func requestRecap(client: APIClient, target: Principal, refresh: () async -> Void) async {
        await perform {
            let result = try await client.requestRecap(target: target)
            await refresh()
            if !result.accepted { throw APIError(status: 0, message: result.note ?? "Delivery uncertain") }
        }
    }
}

struct AgentPageMenu: View {
    let target: Principal
    let settled: Bool?
    let actions: AgentPageActions
    let refresh: () async -> Void
    @Environment(AppStore.self) private var store
    var body: some View {
        Menu {
            Button("Ask for a recap", systemImage: "arrow.clockwise") {
                Task { await actions.requestRecap(client: store.client, target: target, refresh: refresh) }
            }.disabled(actions.isSending)
            if let settled {
                Button(settled ? "Reopen" : "Settle", systemImage: "checkmark") {
                    Task { await actions.perform {
                        try await store.client.setManagerSettled(target.id, settled: !settled)
                        await refresh()
                    } }
                }.disabled(actions.isSending)
            }
            Button("Copy link", systemImage: "doc.on.doc") {
                var url = URLComponents(url: store.baseURL, resolvingAgainstBaseURL: false)
                url?.fragment = target.kind == "chief" ? "/workspace/chief-of-staff" : "/workspace/manager/\(target.id)"
                UIPasteboard.general.url = url?.url; Haptics.success()
            }
        } label: {
            Image(systemName: "ellipsis").frame(width: 44, height: 44).contentShape(Rectangle())
        }.accessibilityLabel("Page actions").accessibilityIdentifier("agent-page-menu")
    }
}

struct AgentReadingPage: View {
    let title: String
    let originalText: String
    var initiallyEditing = false
    let save: (String) async throws -> Void
    @State private var text = ""
    @State private var draft = ""
    @State private var editing = false
    @State private var saving = false
    @State private var error: String?
    var body: some View {
        VStack(alignment: .leading, spacing: PanelsTheme.Space.md) {
            if let error { Text(error).font(PanelsTheme.TypeScale.small).foregroundStyle(PanelsTheme.error) }
            if editing {
                TextEditor(text: $draft).font(PanelsTheme.TypeScale.body).scrollContentBackground(.hidden)
                    .accessibilityLabel("\(title) text")
            } else {
                ScrollView {
                    Text((try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text))
                        .font(PanelsTheme.TypeScale.body).lineSpacing(PanelsTheme.Space.xs)
                        .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }.padding(PanelsTheme.Space.xl).panelsScreen().navigationTitle(title).navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(editing ? "Done" : "Edit") {
                        if !editing { draft = text; editing = true; return }
                        saving = true; error = nil
                        Task {
                            defer { saving = false }
                            do { try await save(draft); text = draft; editing = false; Haptics.success() }
                            catch { self.error = error.localizedDescription; Haptics.error() }
                        }
                    }.disabled(saving)
                }
                if editing { ToolbarItem(placement: .topBarLeading) { Button("Cancel") { editing = false; error = nil } } }
            }
            .onAppear { if text.isEmpty && !editing { text = originalText; draft = originalText; editing = initiallyEditing } }
    }
}

/// Retain UIKit's edge-only interactive pop when a detail page hides the native bar.
/// The original delegate is restored when this page leaves; content drags are untouched.
struct AgentPageBackGesture: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> Controller { Controller() }
    func updateUIViewController(_ controller: Controller, context: Context) {}

    final class Controller: UIViewController, UIGestureRecognizerDelegate {
        private weak var gesture: UIGestureRecognizer?
        private weak var previousDelegate: (any UIGestureRecognizerDelegate)?

        override func viewDidAppear(_ animated: Bool) {
            super.viewDidAppear(animated)
            guard let gesture = navigationController?.interactivePopGestureRecognizer else { return }
            if gesture.delegate !== self { previousDelegate = gesture.delegate }
            self.gesture = gesture
            gesture.delegate = self
            gesture.isEnabled = true
        }

        override func viewDidDisappear(_ animated: Bool) {
            super.viewDidDisappear(animated)
            if gesture?.delegate === self { gesture?.delegate = previousDelegate }
        }

        func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
            guard let navigationController else { return false }
            return navigationController.viewControllers.count > 1 && navigationController.transitionCoordinator == nil
        }
    }
}
