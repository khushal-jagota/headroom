import SwiftUI
import Observation

struct GoalEditableText: View {
    let value: String
    let label: String
    let session: GoalSession
    var blockID: String? = nil
    var path: [String]
    var markdown = false
    var multiline = false
    var editable = true
    @FocusState private var focused: Bool
    private var key: String { (blockID ?? "header") + ":" + path.joined(separator: ".") }
    private var draft: GoalTextDraft? { session.textDrafts[key] }
    var body: some View {
        Group {
            if let draft {
                VStack(alignment: .leading, spacing: 8) {
                    TextField(label, text: Binding(get: { draft.text }, set: { draft.text = $0 }), axis: multiline ? .vertical : .horizontal)
                        .focused($focused).onSubmit { commit() }.accessibilityLabel(label)
                    HStack(spacing: 16) {
                        Button("Save", action: commit).disabled(session.busy || session.saveError != nil || path == ["title"] && draft.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        Button("Cancel") { session.textDrafts[key] = nil; focused = false }
                    }.font(.footnote)
                }
            } else if markdown {
                ArtifactMarkdownContent(source: value, baseURL: session.baseURL)
                    .contextMenu { if editable { Button("Edit " + label, action: begin) } }
            } else {
                Text(value.isEmpty ? label + "…" : value).frame(maxWidth: .infinity, alignment: .leading)
                    .contentShape(Rectangle()).onTapGesture { if editable { begin() } }
                    .accessibilityAction(named: "Edit " + label) { if editable { begin() } }
            }
        }
        .onChange(of: session.goal?.revision) { _, _ in
            if session.saveError == nil, session.submittedTextKey == key {
                session.textDrafts[key] = nil; session.submittedTextKey = nil; focused = false
            }
        }
        .onChange(of: focused) { old, current in if old && !current && draft != nil && session.saveError == nil { commit() } }
    }
    private func begin() {
        guard editable, !session.busy, !session.hasPendingEdit, session.textDrafts[key] == nil else { return }
        session.textDrafts[key] = .init(text: value, revision: session.goal?.revision ?? 0); focused = true
    }
    private func commit() {
        guard let draft, !session.busy, session.saveError == nil else { return }
        if draft.text == value { session.textDrafts[key] = nil; return }
        let text = draft.text, revision = draft.revision
        session.submittedTextKey = key
        Task {
            if let blockID { await session.updateBlock(blockID, revision: revision) { $0.replacing(path: path, with: .string(text)) } }
            else if let field = path.first { await session.save([field: .string(text)], revision: revision) }
        }
    }
}

struct GoalDateControl: View {
    let value: String?
    let label: String
    let tone: String
    let revision: Int
    let save: (String?, Int) async -> Void
    @State private var opened = false
    @State private var date = Date()
    @State private var capturedRevision = 0
    var body: some View {
        Button {
            date = GoalPresentation.calendarDate(value ?? "") ?? Date()
            capturedRevision = revision; opened = true
        } label: {
            Text(value == nil || value == "" ? "No deadline" : GoalPresentation.date(value)).foregroundStyle(PanelsTheme.Goal.tone(tone))
                .frame(minHeight: 32)
        }.buttonStyle(.plain).accessibilityLabel(label + ", " + GoalPresentation.date(value))
            .sheet(isPresented: $opened) {
                NavigationStack {
                    DatePicker(label, selection: $date, displayedComponents: .date).datePickerStyle(.graphical).environment(\.timeZone, TimeZone(secondsFromGMT: 0)!).padding()
                        .toolbar {
                            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { opened = false } }
                            ToolbarItem(placement: .confirmationAction) { Button("Done") { let chosen = GoalPresentation.day(date); opened = false; Task { await save(chosen, capturedRevision) } } }
                            ToolbarItem(placement: .bottomBar) { Button("No deadline") { opened = false; Task { await save(nil, capturedRevision) } } }
                        }.navigationTitle(label).navigationBarTitleDisplayMode(.inline)
                }.presentationDetents([.medium, .large]).preferredColorScheme(.dark)
            }
    }
}
